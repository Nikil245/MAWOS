"""Hosted/local provider selection, privacy, failure, and cost controls."""
import asyncio
import logging

import pytest

from backend.app import ai_provider, config, llm


MESSAGES = [
    {"role": "system", "content": "Answer a safe learning question briefly."},
    {"role": "user", "content": "Explain binary search."},
]


def run(messages=MESSAGES, *, user_key="student:1", local=None):
    async def default_local():
        return ai_provider.ProviderResult(
            message={"role": "assistant", "content": "Local answer."},
            provider="ollama", model=config.OLLAMA_MODEL,
        )
    return asyncio.run(ai_provider.generate_async(
        messages, user_key=user_key, ollama_call=local or default_local))


def _configured(monkeypatch, mode="auto"):
    monkeypatch.setattr(config, "AI_PROVIDER", mode)
    monkeypatch.setenv("GROQ_API_KEY", "test-secret-never-log-this")


def test_auto_selects_verified_groq_without_calling_ollama(monkeypatch):
    _configured(monkeypatch)
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("body")))
        if path == "/models":
            return 200, {"data": [{"id": config.GROQ_MODEL}]}, None
        return 200, {"choices": [{"message": {"role": "assistant", "content": "Hosted answer."}}]}, None

    async def forbidden_local():
        raise AssertionError("Ollama called while verified Groq was healthy")

    monkeypatch.setattr(ai_provider, "_groq_request", request)
    result = run(local=forbidden_local)
    assert result.message["content"] == "Hosted answer."
    assert result.provider == "groq" and result.model == config.GROQ_MODEL
    assert [path for _, path, _ in calls] == ["/models", "/chat/completions"]
    body = calls[1][2]
    assert body["max_completion_tokens"] == config.GROQ_MAX_TOKENS
    assert body["messages"] == MESSAGES


def test_auto_uses_ollama_only_after_groq_health_failure(monkeypatch):
    _configured(monkeypatch)
    local_calls = []

    async def unavailable(*_args, **_kwargs):
        return None, None, "no_network"

    async def local():
        local_calls.append(True)
        return llm.OllamaResult(message={"role": "assistant", "content": "Local."})

    monkeypatch.setattr(ai_provider, "_groq_request", unavailable)
    result = run(local=local)
    assert result.provider == "ollama"
    assert result.message["content"] == "Local."
    assert local_calls == [True]


def test_transient_groq_chat_failure_is_retried_once(monkeypatch):
    _configured(monkeypatch, "groq")
    ai_provider._set_groq_health(True)
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path))
        if len(calls) == 1:
            return 429, {}, None
        return 200, {"choices": [{"message": {"content": "Recovered."}}]}, None

    monkeypatch.setattr(ai_provider, "_groq_request", request)
    result = run()
    assert result.message["content"] == "Recovered."
    assert result.provider == "groq"
    assert calls == [("POST", "/chat/completions"), ("POST", "/chat/completions")]


def test_auto_does_not_send_rejected_private_input_to_local_fallback(monkeypatch):
    _configured(monkeypatch, "auto")
    ai_provider._set_groq_health(True)

    async def forbidden_local():
        raise AssertionError("provider-rejected private text reached local fallback")

    result = run([
        {"role": "system", "content": "Answer briefly."},
        {"role": "user", "content": "Explain my fee of INR 85000."},
    ], local=forbidden_local)
    assert result.error_code == "private_or_invalid_input"


@pytest.mark.parametrize("mode", ["disabled", "groq"])
def test_disabled_or_missing_key_never_calls_a_provider(monkeypatch, mode):
    monkeypatch.setattr(config, "AI_PROVIDER", mode)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    async def forbidden():
        raise AssertionError("local provider called")

    async def forbidden_http(*_args, **_kwargs):
        raise AssertionError("hosted provider called")

    monkeypatch.setattr(ai_provider, "_groq_request", forbidden_http)
    result = run(local=forbidden)
    assert result.message is None
    assert result.error_code == ("provider_disabled" if mode == "disabled" else "missing_key")


@pytest.mark.parametrize("status,transport,expected", [
    (401, None, "invalid_key"),
    (None, "timeout", "timeout"),
    (429, None, "rate_limited"),
])
def test_groq_failures_return_safe_categories(monkeypatch, status, transport, expected):
    _configured(monkeypatch, "groq")

    async def failure(*_args, **_kwargs):
        return status, {}, transport

    monkeypatch.setattr(ai_provider, "_groq_request", failure)
    result = run()
    assert result.message is None
    assert result.error_code == expected
    assert "test-secret" not in repr(result)


def test_private_record_text_is_rejected_before_groq_transport(monkeypatch):
    _configured(monkeypatch, "groq")
    ai_provider._set_groq_health(True)

    async def forbidden_http(*_args, **_kwargs):
        raise AssertionError("private academic data reached Groq transport")

    monkeypatch.setattr(ai_provider, "_groq_request", forbidden_http)
    result = run([
        {"role": "system", "content": "Answer briefly."},
        {"role": "user", "content": "My attendance is 72%. Explain my result."},
    ])
    assert result.error_code == "private_or_invalid_input"


def test_local_per_user_rate_limit_blocks_excess_provider_calls(monkeypatch):
    monkeypatch.setattr(config, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(config, "AI_GENERATIVE_REQUESTS_PER_MINUTE", 1)
    calls = []

    async def local():
        calls.append(True)
        return ai_provider.ProviderResult(message={"role": "assistant", "content": "ok"})

    assert run(local=local).message is not None
    limited = run(local=local)
    assert limited.error_code == "local_rate_limited"
    assert calls == [True]


def test_api_key_never_appears_in_provider_logs_or_result(monkeypatch, caplog):
    secret = "groq-secret-marker-should-never-appear"
    monkeypatch.setattr(config, "AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", secret)

    async def invalid(*_args, **_kwargs):
        return 401, {"error": {"message": f"bad key {secret}"}}, None

    monkeypatch.setattr(ai_provider, "_groq_request", invalid)
    with caplog.at_level(logging.INFO):
        result = run()
    assert secret not in caplog.text
    assert secret not in repr(result)


def test_status_never_treats_key_presence_as_provider_availability(monkeypatch):
    _configured(monkeypatch, "auto")
    status = ai_provider.runtime_status()
    assert status["groq_configured"] is True
    assert status["groq_available"] is None
    assert status["groq_status"] == "not_checked"
    assert status["selected"] is None
