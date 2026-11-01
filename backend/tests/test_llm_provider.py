import pytest
import httpx
from google.genai import errors as genai_errors

from backend.app.services import llm_provider
from backend.app.services.llm_provider import (
    LLMProviderError,
    _ProviderCallError,
    _classify_provider_error,
    generate_text,
)
from google.genai import errors as genai_errors


@pytest.fixture(autouse=True)
def clear_provider_environment(monkeypatch):
    for key in (
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "MISTRAL_API_KEY",
        "MISTRAL_MODEL",
        "LLM_PROVIDER",
        "LLM_FALLBACK_PROVIDERS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(llm_provider.time, "sleep", lambda _seconds: None)


def test_falls_back_to_groq_after_bounded_gemini_retries(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    calls = []

    def call_provider(provider, *_args):
        calls.append(provider)
        if provider == "gemini":
            raise _ProviderCallError("RATE_LIMITED", retryable=True)
        return "Generated from Groq."

    monkeypatch.setattr(llm_provider, "_call_provider", call_provider)
    text, provider = generate_text(user_prompt="Question")

    assert text == "Generated from Groq."
    assert provider == "groq"
    assert calls == ["gemini"] * 3 + ["groq"]


def test_provider_order_is_configurable_and_missing_keys_are_skipped(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "gemini,mistral")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    calls = []
    monkeypatch.setattr(
        llm_provider,
        "_call_provider",
        lambda provider, *_args: calls.append(provider) or "answer",
    )

    text, provider = generate_text(user_prompt="Question")

    assert text == "answer"
    assert provider == "gemini"
    assert calls == ["gemini"]


def test_all_provider_failures_are_reported_without_secrets(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "do-not-return-this-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.setattr(
        llm_provider,
        "_call_provider",
        lambda *_args: (_ for _ in ()).throw(
            _ProviderCallError("RATE_LIMITED", retryable=True)
        ),
    )

    with pytest.raises(LLMProviderError) as error:
        generate_text(user_prompt="Question")

    assert error.value.code == "LLM_PROVIDERS_UNAVAILABLE"
    assert error.value.retryable
    assert "do-not-return-this-key" not in str(error.value)
    assert "Gemini: rate limited" in str(error.value)
    assert "Groq: rate limited" in str(error.value)


def test_invalid_provider_configuration_fails_explicitly(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "unknown")
    with pytest.raises(LLMProviderError) as error:
        generate_text(user_prompt="Question")
    assert error.value.code == "LLM_PROVIDER_CONFIGURATION_INVALID"


@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (401, "AUTHENTICATION_FAILED", False),
        (403, "AUTHENTICATION_FAILED", False),
        (404, "MODEL_UNAVAILABLE", False),
        (429, "RATE_LIMITED", True),
        (503, "PROVIDER_UNAVAILABLE", True),
    ],
)
def test_gemini_sdk_statuses_are_classified_for_failover(
    status_code,
    expected_code,
    retryable,
):
    error_type = (
        genai_errors.ServerError
        if status_code >= 500
        else genai_errors.ClientError
    )
    failure = _classify_provider_error(
        error_type(status_code, {"error": {"message": "provider failure"}})
    )

    assert failure.code == expected_code
    assert failure.retryable is retryable


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (429, genai_errors.ClientError),
        (503, genai_errors.ServerError),
    ],
)
def test_gemini_rate_limit_or_outage_falls_back_to_groq(
    monkeypatch,
    status_code,
    error_type,
):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    gemini_attempts = []
    requests = []

    class FakeModels:
        def generate_content(self, **_kwargs):
            gemini_attempts.append(1)
            raise error_type(status_code, {"error": {"message": "temporary"}})

    monkeypatch.setattr(
        llm_provider.genai,
        "Client",
        lambda **_kwargs: type(
            "FakeClient",
            (),
            {"models": FakeModels()},
        )(),
    )

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "Groq answer"}}]}

    def fake_post(url, *, headers, json, timeout):
        requests.append((url, headers, json, timeout))
        return FakeResponse()

    monkeypatch.setattr(llm_provider.httpx, "post", fake_post)
    text, provider = generate_text(user_prompt="Question")

    assert text == "Groq answer"
    assert provider == "groq"
    assert len(gemini_attempts) == llm_provider.MAX_ATTEMPTS_PER_PROVIDER
    assert requests[0][0] == llm_provider.OPENAI_COMPATIBLE_ENDPOINTS["groq"]
    assert requests[0][1]["Authorization"] == "Bearer groq-key"
    assert requests[0][2]["model"] == "llama-3.3-70b-versatile"
    assert requests[0][3] == llm_provider.REQUEST_TIMEOUT_SECONDS


def test_timeout_retries_then_uses_next_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "mistral-key")
    attempts = []

    class FakeModels:
        def generate_content(self, **_kwargs):
            attempts.append(1)
            raise httpx.ReadTimeout("simulated timeout")

    monkeypatch.setattr(
        llm_provider.genai,
        "Client",
        lambda **_kwargs: type(
            "FakeClient",
            (),
            {"models": FakeModels()},
        )(),
    )
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "Mistral answer"}}]}

    monkeypatch.setattr(
        llm_provider.httpx,
        "post",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    text, provider = generate_text(user_prompt="Question")

    assert text == "Mistral answer"
    assert provider == "mistral"
    assert len(attempts) == llm_provider.MAX_ATTEMPTS_PER_PROVIDER
