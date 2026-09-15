"""Tool-free hosted/local provider selection for sanitized generative turns.

No provider in this module receives database handles, credentials, tools, or
authorization data. Callers must pass only bounded general-learning text or
sanitized public catalogue metadata.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
import logging
import os
import re
import threading
import time
from typing import Awaitable, Callable

import httpx

from . import config

logger = logging.getLogger(__name__)


@dataclass
class ProviderResult:
    message: dict | None = None
    error_code: str | None = None
    latency_ms: float = 0.0
    provider: str | None = None
    model: str | None = None


_groq_available: bool | None = None
_groq_health_error: str | None = None
_groq_checked_at = 0.0
_groq_health_lock = asyncio.Lock()
_rate_lock = threading.Lock()
_rate_windows: dict[str, deque[float]] = defaultdict(deque)

_PRIVATE_INPUT = re.compile(
    r"\b(?:password|passwd|api[ _-]?key|authorization|bearer|access[ _-]?token|"
    r"refresh[ _-]?token|secret|connection[ -]?string|borrower|reservation|fine|"
    r"attendance|marks?|fees?|hall[ -]?ticket|cgpa|backlogs?|student[ _-]?id)\b"
    r"|postgres(?:ql)?(?:\+psycopg)?://|\beyJ[\w-]+\.[\w-]+\.[\w-]+"
    r"|\b[0-9][A-Za-z0-9]{5,15}\b|(?:₹|\b(?:inr|rs\.?)\b|\b\d{1,3}(?:\.\d+)?\s*%)",
    re.IGNORECASE,
)


def _api_key() -> str | None:
    value = os.getenv("GROQ_API_KEY", "").strip()
    return value or None


def _safe_error(status: int | None, transport: str | None = None) -> str:
    if transport in {"timeout", "no_network"}:
        return transport
    if status == 401:
        return "invalid_key"
    if status == 403:
        return "permission_denied"
    if status == 429:
        return "rate_limited"
    if status is not None and status >= 500:
        return "provider_unavailable"
    return "provider_error"


def _set_groq_health(available: bool, error: str | None = None) -> None:
    global _groq_available, _groq_health_error, _groq_checked_at
    _groq_available = available
    _groq_health_error = error
    _groq_checked_at = time.monotonic()


def reset_runtime_state_for_tests() -> None:
    global _groq_available, _groq_health_error, _groq_checked_at, _groq_health_lock
    _groq_available = None
    _groq_health_error = None
    _groq_checked_at = 0.0
    _groq_health_lock = asyncio.Lock()
    with _rate_lock:
        _rate_windows.clear()


def runtime_status() -> dict:
    """Return display-safe cached status; never infer availability from a key."""
    configured = _api_key() is not None
    return {
        "mode": config.AI_PROVIDER,
        "selected": (
            "groq"
            if config.AI_PROVIDER in {"auto", "groq"} and _groq_available is True
            else None
        ),
        "groq_configured": configured,
        "groq_available": _groq_available,
        "groq_status": (
            "available" if _groq_available is True else
            _groq_health_error if _groq_available is False else
            "not_checked" if configured else "not_configured"
        ),
        "ollama_available": None,
    }


def _rate_allowed(user_key: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        window = _rate_windows[user_key]
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= config.AI_GENERATIVE_REQUESTS_PER_MINUTE:
            return False
        window.append(now)
        return True


def _safe_messages(messages: list[dict]) -> list[dict] | None:
    if not isinstance(messages, list) or not 1 <= len(messages) <= 8:
        return None
    safe, total = [], 0
    for item in messages:
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            return None
        role, content = item.get("role"), item.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            return None
        if not content.strip() or len(content) > 2000:
            return None
        if role != "system" and _PRIVATE_INPUT.search(content):
            return None
        total += len(content)
        if total > config.GROQ_MAX_INPUT_CHARS:
            return None
        safe.append({"role": role, "content": content})
    return safe


async def _groq_request(method: str, path: str, *, body: dict | None = None):
    key = _api_key()
    if key is None:
        return None, None, "missing_key"
    try:
        async with httpx.AsyncClient(
            base_url=config.GROQ_BASE_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=config.GROQ_TIMEOUT_S,
        ) as client:
            kwargs = {"json": body} if body is not None else {}
            response = await client.request(method, path, **kwargs)
    except httpx.TimeoutException:
        return None, None, "timeout"
    except httpx.RequestError:
        return None, None, "no_network"
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = None
    return response.status_code, payload, None


async def check_groq_async(force: bool = False) -> bool:
    if _api_key() is None:
        _set_groq_health(False, "missing_key")
        return False
    now = time.monotonic()
    ttl = config.GROQ_HEALTH_TTL_S if _groq_available else config.GROQ_RETRY_COOLDOWN_S
    if not force and _groq_available is not None and now - _groq_checked_at < ttl:
        return _groq_available
    async with _groq_health_lock:
        now = time.monotonic()
        ttl = config.GROQ_HEALTH_TTL_S if _groq_available else config.GROQ_RETRY_COOLDOWN_S
        if not force and _groq_available is not None and now - _groq_checked_at < ttl:
            return _groq_available
        status, payload, transport = await _groq_request("GET", "/models")
        if transport:
            _set_groq_health(False, transport)
            logger.info("AI provider health provider=groq status=%s", transport)
            return False
        models = payload.get("data") if isinstance(payload, dict) else None
        model_present = isinstance(models, list) and any(
            isinstance(item, dict) and item.get("id") == config.GROQ_MODEL for item in models)
        if status != 200 or not model_present:
            error = "model_missing" if status == 200 else _safe_error(status)
            _set_groq_health(False, error)
            logger.info("AI provider health provider=groq status=%s", error)
            return False
        _set_groq_health(True)
        logger.info("AI provider health provider=groq status=available")
        return True


async def _chat_groq(messages: list[dict]) -> ProviderResult:
    started = time.perf_counter()
    safe = _safe_messages(messages)
    if safe is None:
        return ProviderResult(error_code="private_or_invalid_input", provider="groq")
    body = {
        "model": config.GROQ_MODEL,
        "messages": safe,
        "temperature": 0.1,
        "max_completion_tokens": config.GROQ_MAX_TOKENS,
    }
    last_error = "provider_error"
    for attempt in range(2):
        status, payload, transport = await _groq_request("POST", "/chat/completions", body=body)
        last_error = _safe_error(status, transport)
        transient = last_error in {"timeout", "no_network", "rate_limited", "provider_unavailable"}
        if status == 200 and isinstance(payload, dict):
            choices = payload.get("choices")
            message = choices[0].get("message") if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and content.strip() and len(content) <= 12000:
                return ProviderResult(
                    message={"role": "assistant", "content": content},
                    latency_ms=(time.perf_counter() - started) * 1000,
                    provider="groq", model=config.GROQ_MODEL,
                )
            last_error = "invalid_response"
            transient = False
        if not transient or attempt == 1:
            break
        await asyncio.sleep(0)
    _set_groq_health(False, last_error)
    logger.warning(
        "AI provider request failed provider=groq category=%s elapsed_ms=%.1f",
        last_error, (time.perf_counter() - started) * 1000,
    )
    return ProviderResult(
        error_code=last_error, latency_ms=(time.perf_counter() - started) * 1000,
        provider="groq", model=config.GROQ_MODEL,
    )


async def generate_async(
    messages: list[dict], *, user_key: str,
    ollama_call: Callable[[], Awaitable[ProviderResult]],
) -> ProviderResult:
    """Select only Groq/Ollama/disabled according to the explicit policy."""
    mode = config.AI_PROVIDER
    if mode == "disabled":
        return ProviderResult(error_code="provider_disabled")
    if not _rate_allowed(user_key):
        return ProviderResult(error_code="local_rate_limited")
    if mode in {"auto", "groq"}:
        if await check_groq_async():
            result = await _chat_groq(messages)
            if (result.message is not None or mode == "groq"
                    or result.error_code == "private_or_invalid_input"):
                return result
        elif mode == "groq":
            return ProviderResult(
                error_code=_groq_health_error or "unavailable",
                provider="groq", model=config.GROQ_MODEL,
            )
    result = await ollama_call()
    if getattr(result, "provider", None) is None:
        result.provider = "ollama"
    if getattr(result, "model", None) is None:
        result.model = config.OLLAMA_MODEL
    return result
