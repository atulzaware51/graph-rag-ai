"""Bounded generation with configured LLM provider failover."""

from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS_PER_PROVIDER = 3
RETRY_BASE_SECONDS = 0.5
RETRY_MAX_SECONDS = 4.0
OPENAI_COMPATIBLE_ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "mistral": "https://api.mistral.ai/v1/chat/completions",
}


class LLMProviderError(RuntimeError):
    """Raised when no configured LLM provider can generate a response."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        provider: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.provider = provider


class _ProviderCallError(RuntimeError):
    def __init__(self, code: str, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _configured_order() -> list[str]:
    primary = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    fallback = os.getenv(
        "LLM_FALLBACK_PROVIDERS",
        "groq,mistral",
    )
    providers = [primary, *(value.strip().lower() for value in fallback.split(","))]
    if any(provider not in {"gemini", "groq", "mistral"} for provider in providers):
        raise LLMProviderError(
            "LLM_PROVIDER or LLM_FALLBACK_PROVIDERS contains an unsupported provider.",
            code="LLM_PROVIDER_CONFIGURATION_INVALID",
            retryable=False,
        )
    return list(dict.fromkeys(providers))


def _provider_config(provider: str) -> tuple[str, str] | None:
    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    elif provider == "groq":
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    else:
        api_key = os.getenv("MISTRAL_API_KEY", "").strip()
        model = os.getenv("MISTRAL_MODEL", "mistral-small-latest").strip()
    if not api_key:
        return None
    if not model:
        raise _ProviderCallError("MODEL_NOT_CONFIGURED", retryable=False)
    return api_key, model


def _call_provider(
    provider: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    if provider == "gemini":
        client = genai.Client(
            api_key=api_key,
            http_options=genai.types.HttpOptions(
                timeout=int(REQUEST_TIMEOUT_SECONDS * 1000),
                retry_options=genai.types.HttpRetryOptions(attempts=1),
            ),
        )
        contents = (
            f"{system_prompt}\n\n{user_prompt}"
            if system_prompt
            else user_prompt
        )
        response = client.models.generate_content(
            model=model,
            contents=contents,
        )
        text = (response.text or "").strip()
    else:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        response = httpx.post(
            OPENAI_COMPATIBLE_ENDPOINTS[provider],
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": messages,
                "temperature": 0,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        status_code = response.status_code
        if status_code >= 400:
            if status_code in {401, 403}:
                code = "AUTHENTICATION_FAILED"
                retryable = False
            elif status_code == 404:
                code = "MODEL_UNAVAILABLE"
                retryable = False
            elif status_code == 429:
                code = "RATE_LIMITED"
                retryable = True
            elif status_code >= 500:
                code = "PROVIDER_UNAVAILABLE"
                retryable = True
            else:
                code = "PROVIDER_REQUEST_REJECTED"
                retryable = False
            raise _ProviderCallError(
                code,
                retryable=retryable,
            )
        payload = response.json()
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise _ProviderCallError("EMPTY_RESPONSE", retryable=True)
        message = choices[0].get("message", {})
        text = message.get("content", "") if isinstance(message, dict) else ""
        if isinstance(text, list):
            text = "\n".join(
                item.get("text", "")
                for item in text
                if isinstance(item, dict)
            )
        text = text.strip() if isinstance(text, str) else ""

    if not text:
        raise _ProviderCallError("EMPTY_RESPONSE", retryable=True)
    return text


def _classify_provider_error(exc: Exception) -> _ProviderCallError:
    if isinstance(exc, _ProviderCallError):
        return exc
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return _ProviderCallError("PROVIDER_TIMEOUT", retryable=True)
    if isinstance(exc, genai_errors.APIError):
        status_code = getattr(exc, "code", None)
        provider_status = str(getattr(exc, "status", "")).upper()
        provider_message = str(getattr(exc, "message", "")).casefold()
        if status_code == 429:
            return _ProviderCallError("RATE_LIMITED", retryable=True)
        if status_code in {500, 502, 503, 504}:
            return _ProviderCallError("PROVIDER_UNAVAILABLE", retryable=True)
        if status_code == 404:
            return _ProviderCallError("MODEL_UNAVAILABLE", retryable=False)
        if status_code in {401, 403} or (
            status_code == 400
            and (
                "API_KEY_INVALID" in provider_status
                or "api key not valid" in provider_message
                or "invalid api key" in provider_message
            )
        ):
            return _ProviderCallError(
                "AUTHENTICATION_FAILED",
                retryable=False,
            )
        return _ProviderCallError("PROVIDER_REQUEST_REJECTED", retryable=False)
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code == 429:
            return _ProviderCallError("RATE_LIMITED", retryable=True)
        if status_code in {401, 403}:
            return _ProviderCallError("AUTHENTICATION_FAILED", retryable=False)
        if status_code == 404:
            return _ProviderCallError("MODEL_UNAVAILABLE", retryable=False)
        return _ProviderCallError(
            "PROVIDER_UNAVAILABLE" if status_code >= 500 else
            "PROVIDER_REQUEST_REJECTED",
            retryable=status_code >= 500,
        )
    return _ProviderCallError("PROVIDER_REQUEST_FAILED", retryable=False)


def generate_text(
    *,
    system_prompt: str = "",
    user_prompt: str,
) -> tuple[str, str]:
    """Try configured providers in order, retrying transient failures finitely."""
    order = _configured_order()
    last_error: _ProviderCallError | None = None
    attempted: list[str] = []
    provider_failures: dict[str, str] = {}

    for provider in order:
        try:
            config = _provider_config(provider)
        except _ProviderCallError as exc:
            last_error = exc
            attempted.append(provider)
            provider_failures[provider] = exc.code
            continue
        if config is None:
            continue
        api_key, model = config
        attempted.append(provider)

        for attempt in range(MAX_ATTEMPTS_PER_PROVIDER):
            try:
                return (
                    _call_provider(
                        provider,
                        api_key,
                        model,
                        system_prompt,
                        user_prompt,
                    ),
                    provider,
                )
            except Exception as exc:
                failure = _classify_provider_error(exc)
                last_error = failure
                provider_failures[provider] = failure.code
                logger.warning(
                    "LLM provider %s failed (model=%s, code=%s, attempt=%s).",
                    provider,
                    model,
                    failure.code,
                    attempt + 1,
                )
                if not failure.retryable or attempt == MAX_ATTEMPTS_PER_PROVIDER - 1:
                    break
                delay = min(
                    RETRY_BASE_SECONDS * (2**attempt),
                    RETRY_MAX_SECONDS,
                )
                time.sleep(random.uniform(delay * 0.75, delay * 1.25))

    if not attempted:
        raise LLMProviderError(
            "No configured LLM API key was found. Set at least one of "
            "GEMINI_API_KEY, GROQ_API_KEY, or MISTRAL_API_KEY.",
            code="LLM_PROVIDER_NOT_CONFIGURED",
            retryable=False,
        )

    failed_provider = attempted[-1]
    failure_details = "; ".join(
        f"{provider.title()}: {code.replace('_', ' ').lower()}"
        for provider, code in provider_failures.items()
    )
    logger.error(
        "All configured LLM providers failed; providers=%s.",
        failure_details,
    )
    raise LLMProviderError(
        "All configured LLM providers are unavailable or misconfigured. "
        f"Provider diagnostics: {failure_details}. "
        "Check the listed provider keys, model names, quotas, and service status, then retry.",
        code="LLM_PROVIDERS_UNAVAILABLE",
        retryable=bool(
            last_error
            and any(
                failure == "RATE_LIMITED"
                or failure == "PROVIDER_UNAVAILABLE"
                or failure == "PROVIDER_TIMEOUT"
                for failure in provider_failures.values()
            )
        ),
        provider=failed_provider,
    )
