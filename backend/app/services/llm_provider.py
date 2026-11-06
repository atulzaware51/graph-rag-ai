"""Bounded generation with configured LLM provider failover."""

from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS_PER_PROVIDER = 2
RETRY_BASE_SECONDS = 0.25
RETRY_MAX_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 2.0
OPENAI_COMPATIBLE_ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "mistral": "https://api.mistral.ai/v1/chat/completions",
}
PROVIDER_SETTINGS = {
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "gemini-2.5-flash"),
    "groq": ("GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile"),
    "mistral": ("MISTRAL_API_KEY", "MISTRAL_MODEL", "mistral-small-latest"),
}


def _environment_value(name: str, default: str = "") -> str:
    """Read a provider setting and remove optional quotes from local env files."""
    value = os.getenv(name, default).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value


def provider_model_name(provider: str) -> str | None:
    """Return a provider's configured model without exposing credentials."""
    setting = PROVIDER_SETTINGS.get(provider)
    if setting is None:
        return None
    _, model_name, default_model = setting
    return _environment_value(model_name, default_model) or None


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
        """Expose safe error details so callers can decide whether to retry."""
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.provider = provider


class _ProviderCallError(RuntimeError):
    """Represent one provider failure with retry details for the failover loop."""

    def __init__(
        self,
        code: str,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        """Keep provider-specific retry hints for the provider failover loop."""
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


def provider_diagnostics() -> list[dict[str, str | bool | None]]:
    """Describe configured provider names/models without returning credentials."""
    diagnostics = []
    for provider in _configured_order():
        try:
            config = _provider_config(provider)
        except _ProviderCallError as exc:
            diagnostics.append(
                {
                    "provider": provider,
                    "model": provider_model_name(provider),
                    "configured": False,
                    "status": exc.code.casefold(),
                }
            )
            continue
        diagnostics.append(
            {
                "provider": provider,
                "model": provider_model_name(provider),
                "configured": config is not None,
                "status": "configured" if config else "missing_api_key",
            }
        )
    return diagnostics


def _retry_after_seconds(value: str | None) -> float | None:
    """Convert either numeric or HTTP-date retry guidance into seconds."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (retry_at - datetime.now(timezone.utc)).total_seconds(),
            )
        except (TypeError, ValueError, OverflowError):
            return None


def _exception_retry_after_seconds(exc: Exception) -> float | None:
    """Extract retry timing from an SDK exception's HTTP response when present."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    value = headers.get("Retry-After") if hasattr(headers, "get") else None
    return _retry_after_seconds(value if isinstance(value, str) else None)


def _response_error_details(response: httpx.Response) -> tuple[str, str]:
    """Read normalized error code and message fields from provider JSON."""
    try:
        payload = response.json()
    except ValueError:
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    error: Any = payload.get("error", {})
    if isinstance(error, str):
        return "", error.casefold()
    if not isinstance(error, dict):
        return "", ""
    code = error.get("code") or error.get("type") or ""
    message = error.get("message") or ""
    return (
        code.casefold() if isinstance(code, str) else "",
        message.casefold() if isinstance(message, str) else "",
    )


def _is_quota_failure(code: str, message: str) -> bool:
    """Distinguish exhausted account quota from a temporary request rate limit."""
    return any(
        marker in code or marker in message
        for marker in ("quota", "resource_exhausted", "billing")
    )


def _classify_http_response(response: httpx.Response) -> _ProviderCallError:
    """Map HTTP responses to consistent authentication, retry, or model errors."""
    code_detail, message = _response_error_details(response)
    if response.status_code in {401, 403}:
        return _ProviderCallError("AUTHENTICATION_FAILED", retryable=False)
    if response.status_code == 404:
        return _ProviderCallError("MODEL_UNAVAILABLE", retryable=False)
    if response.status_code == 429:
        if _is_quota_failure(code_detail, message):
            return _ProviderCallError("QUOTA_EXHAUSTED", retryable=False)
        return _ProviderCallError(
            "RATE_LIMITED",
            retryable=True,
            retry_after_seconds=_retry_after_seconds(
                response.headers.get("Retry-After")
            ),
        )
    if response.status_code >= 500:
        return _ProviderCallError(
            "PROVIDER_UNAVAILABLE",
            retryable=True,
            retry_after_seconds=_retry_after_seconds(
                response.headers.get("Retry-After")
            ),
        )
    return _ProviderCallError("PROVIDER_REQUEST_REJECTED", retryable=False)


def _configured_order() -> list[str]:
    """Build and validate the primary-provider-first failover order."""
    primary = _environment_value("LLM_PROVIDER", "gemini").lower()
    fallback = _environment_value(
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
    """Return usable credentials and model settings, or None if unconfigured."""
    key_name, model_name, default_model = PROVIDER_SETTINGS[provider]
    api_key = _environment_value(key_name)
    model = _environment_value(model_name, default_model)
    if api_key.casefold().startswith(("your_", "your-", "replace_", "replace-", "placeholder")):
        return None
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
    """Send prompts through the provider's native or OpenAI-compatible API."""
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
        if response.status_code >= 400:
            raise _classify_http_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise _ProviderCallError(
                "INVALID_RESPONSE",
                retryable=False,
            ) from exc
        if not isinstance(payload, dict):
            raise _ProviderCallError("INVALID_RESPONSE", retryable=False)
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise _ProviderCallError("EMPTY_RESPONSE", retryable=True)
        if not isinstance(choices[0], dict):
            raise _ProviderCallError("INVALID_RESPONSE", retryable=False)
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
    """Translate SDK exceptions into the same categories used for HTTP errors."""
    if isinstance(exc, _ProviderCallError):
        return exc
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return _ProviderCallError("PROVIDER_TIMEOUT", retryable=True)
    if isinstance(exc, genai_errors.APIError):
        status_code = getattr(exc, "code", None)
        provider_status = str(getattr(exc, "status", "")).upper()
        provider_message = str(getattr(exc, "message", "")).casefold()
        if status_code == 429:
            if _is_quota_failure(provider_status, provider_message):
                return _ProviderCallError("QUOTA_EXHAUSTED", retryable=False)
            return _ProviderCallError(
                "RATE_LIMITED",
                retryable=True,
                retry_after_seconds=_exception_retry_after_seconds(exc),
            )
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
            return _ProviderCallError(
                "RATE_LIMITED",
                retryable=True,
                retry_after_seconds=_retry_after_seconds(
                    exc.response.headers.get("Retry-After")
                ),
            )
        if status_code in {401, 403}:
            return _ProviderCallError("AUTHENTICATION_FAILED", retryable=False)
        if status_code == 404:
            return _ProviderCallError("MODEL_UNAVAILABLE", retryable=False)
        return _ProviderCallError(
            "PROVIDER_UNAVAILABLE" if status_code >= 500 else
            "PROVIDER_REQUEST_REJECTED",
            retryable=status_code >= 500,
            retry_after_seconds=(
                _retry_after_seconds(exc.response.headers.get("Retry-After"))
                if status_code >= 500
                else None
            ),
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
            logger.warning(
                "LLM provider %s configuration failed (category=%s).",
                provider,
                exc.code,
            )
            continue
        if config is None:
            logger.info(
                "LLM provider %s skipped (model=%s, API key=missing).",
                provider,
                provider_model_name(provider),
            )
            continue
        api_key, model = config
        attempted.append(provider)
        logger.info(
            "LLM provider %s configured (model=%s, API key=present).",
            provider,
            model,
        )

        for attempt in range(MAX_ATTEMPTS_PER_PROVIDER):
            started_at = time.monotonic()
            try:
                text = _call_provider(
                    provider,
                    api_key,
                    model,
                    system_prompt,
                    user_prompt,
                )
                logger.info(
                    "LLM provider %s request succeeded "
                    "(model=%s, attempt=%s, duration_ms=%s).",
                    provider,
                    model,
                    attempt + 1,
                    round((time.monotonic() - started_at) * 1000),
                )
                return text, provider
            except Exception as exc:
                failure = _classify_provider_error(exc)
                last_error = failure
                provider_failures[provider] = failure.code
                logger.warning(
                    "LLM provider %s failed "
                    "(model=%s, category=%s, attempt=%s, duration_ms=%s).",
                    provider,
                    model,
                    failure.code,
                    attempt + 1,
                    round((time.monotonic() - started_at) * 1000),
                )
                if not failure.retryable or attempt == MAX_ATTEMPTS_PER_PROVIDER - 1:
                    break
                if failure.retry_after_seconds is not None:
                    delay = min(
                        failure.retry_after_seconds,
                        MAX_RETRY_AFTER_SECONDS,
                    )
                else:
                    delay = min(
                        RETRY_BASE_SECONDS * (2**attempt),
                        RETRY_MAX_SECONDS,
                    )
                    delay = random.uniform(delay * 0.75, delay * 1.25)
                if delay > 0:
                    time.sleep(delay)

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
