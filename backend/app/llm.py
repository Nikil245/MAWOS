"""LLM layer v2.

Primary path: local Ollama chat API with native tool calling (Qwen2.5-class
models). The Orchestrator sends the conversation + the role-filtered tool
schemas; the model decides which tools to call and finally writes a grounded
natural-language answer.

Offline fallback: deterministic weighted-keyword classifier mapping a query
to the single most likely tool. Always available; its share of traffic is
the measured fallback-trigger rate.

11 intents (P2): `admission_query` was retired with `get_admissions_funnel`
(docs/RESEARCH_PLAN_V3.md §7.1) — Admission no longer meets the agent
criterion and this was its only chat-facing capability.
"""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from . import ai_provider, config

logger = logging.getLogger(__name__)

# fallback intent -> tool name
INTENT_TOOL = {
    "attendance_query": "get_attendance",
    "fees_query": "get_fees",
    "scholarship_query": "get_scholarship",
    "exam_query": "get_hall_ticket",
    "exam_schedule_query": "get_exam_schedule",
    "marks_query": "get_marks",
    "timetable_query": "get_timetable",
    "placement_query": "get_placements",
    "analytics_query": "get_dept_analytics",
    "notification_query": "get_notifications",
    "profile_query": "get_student_overview",
}
INTENTS = list(INTENT_TOOL)

_LEXICON: dict[str, list[tuple[str, float]]] = {
    "attendance_query": [
        (r"attendance(?!.*event)", 3), (r"absent", 2), (r"shortage", 2.5),
        (r"miss\w*\b.{0,20}\b(class|lecture)", 3), (r"75\s*%", 2),
        (r"bunk|skip\w*", 2), (r"below the limit", 2.5), (r"present", 1.5),
    ],
    "fees_query": [
        (r"fees?", 3), (r"tuition", 2.5), (r"payment", 2), (r"fine", 1.5),
        (r"dues?\b|owe", 2.5), (r"pay\b", 1.5), (r"defaulter", 2.5),
        (r"penalt\w*", 2), (r"receipt", 2),
    ],
    "scholarship_query": [
        # "fee waiver" outranks the bare "fee" signal: a waiver request is a
        # financial-aid request by definition, not a payment query.
        (r"scholarship", 3.5), (r"stipend", 2.5), (r"fee waiver", 4.5),
        (r"waive", 2.5),
        (r"(financial|money) (aid|help|support)", 3.5), (r"grant", 2),
        (r"merit.{0,15}(scholar|award)", 2.5),
    ],
    "exam_query": [
        (r"hall\s*ticket", 3.5), (r"admit card", 3),
        (r"eligib\w*.{0,20}exam", 3), (r"sit for the (finals?|exams?)", 3),
        (r"writ\w* (my )?(papers?|exams?|finals?)", 2.5),
        (r"exam hall", 3), (r"blocked", 1.5),
    ],
    "exam_schedule_query": [
        (r"exam (schedule|time\s*table|dates?)", 3.5), (r"when.{0,25}exams?", 3),
        (r"(sem|semester).{0,15}exam", 2), (r"exams? (start|begin)", 3),
    ],
    "marks_query": [
        (r"marks?\b", 3), (r"internals?\b", 2.5), (r"\bcie\b", 3.5),
        (r"scores?\b", 1.5), (r"test (result|performance)", 2.5),
    ],
    "timetable_query": [
        (r"time\s*table(?!.*exam)", 3.5), (r"class schedule", 3),
        (r"(what|which).{0,15}(class(es)?|periods?|subjects?)\b", 3),
        (r"(class(es)?|periods?).{0,20}(today|tomorrow|this week)", 3),
        (r"my (classes|schedule)\b", 2.5), (r"routine", 2),
    ],
    "placement_query": [
        (r"placement", 3), (r"compan(y|ies)|firms?", 2), (r"drive", 2),
        (r"job|recruit\w*|hired?", 2.5), (r"shortlist\w*", 2.5),
        (r"package|lpa", 2), (r"interview", 2), (r"cutoff", 2),
        (r"openings?", 2.5), (r"campus", 1.5),
    ],
    "analytics_query": [
        (r"analytics|statistics|overview of (the )?(dept|department|branch)", 3),
        (r"how (is|are) (the )?(dept|department|students) (doing|performing)", 3),
        # "average X" is an aggregate signal — outranks the per-student domain word.
        (r"average (attendance|cgpa|marks)", 4.5), (r"department report", 3),
        (r"department\b", 1.5), (r"health check", 2.5),
    ],
    "notification_query": [
        (r"notifications?", 3), (r"alerts?", 2.5), (r"announce\w*", 2.5),
        (r"messages?", 2), (r"warnings?", 2), (r"should know", 2),
        (r"what did i miss", 2.5), (r"remind\w*", 2),
    ],
    "profile_query": [
        (r"profile|my details|dashboard", 3), (r"cgpa", 2.5),
        (r"backlogs?", 2), (r"who am i", 3), (r"overview|summary|rundown", 2),
        (r"where i stand|my standing", 2.5), (r"overall", 1.5),
    ],
}


class IntentResult:
    def __init__(self, intent, method, latency_ms, tool=None, margin=0.0):
        self.intent = intent
        self.method = method
        self.latency_ms = latency_ms
        self.tool = tool or INTENT_TOOL.get(intent)
        #: Top-1 minus top-2 intent score — the classifier's own confidence,
        #: and the signal the hybrid router escalates on (see router.py).
        #: Zero means nothing matched at all and `intent` is the
        #: `profile_query` default rather than a decision.
        self.margin = margin


def classify_keyword(query: str) -> IntentResult:
    start = time.perf_counter()
    q = query.lower()
    scores = {intent: 0.0 for intent in INTENTS}
    for intent, patterns in _LEXICON.items():
        for pattern, weight in patterns:
            if re.search(pattern, q):
                scores[intent] += weight
    best = max(scores, key=scores.get)
    ranked = sorted(scores.values(), reverse=True)
    margin = ranked[0] - ranked[1]
    if scores[best] <= 0:
        best = "profile_query"
    return IntentResult(best, "keyword", (time.perf_counter() - start) * 1000,
                        margin=margin)


# Kept outside `_LEXICON` so the frozen routing instrument and its recorded
# margins are untouched.  These patterns only rescue clearly supported Phase 2
# wording that otherwise falls through to the unsupported profile intent.
_CHAT_PARAPHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("attendance_query", (
        r"class(room)? presence", r"lecture (record|rate)",
        r"have i attended enough classes",
        r"how (often|many).{0,20}(attend|class)", r"classes? attended",
        r"\bhow regularly\b.{0,40}\b(?:attend\w*|classes?)\b",
        r"\battend\w*\b.{0,25}\bclasses?\b.{0,15}\bregularly\b",
        r"(?:show|check) (?:me )?(?:my )?attendance",
        r"what is my attendance", r"attendance status",
        r"how much attendance do i have", r"show attendance please",
        r"show my attendance status(?: with (?:the )?subject names?)?",
        r"(?:show )?(?:my )?subject[ -]?wise attendance",
        r"(?:show )?(?:my )?attendance (?:for|in) each subject",
    )),
    ("fees_query", (
        r"(account|tuition|college) balance", r"amount (i )?(need|have) to pay",
        r"money (i )?(owe|due)", r"unpaid (amount|bill|balance)",
        r"do i owe anything", r"is (?:there )?any payment pending",
        r"my fee status",
    )),
    ("marks_query", (
        r"(assessment|test|exam) (score|grade|result)", r"how did i score",
        r"my grade(s)?", r"continuous assessment",
        r"how did i do in internals", r"show (?:me )?(?:my )?cie marks",
        r"what marks did i get", r"show my subject marks",
    )),
    ("exam_query", (
        r"\bhall-ticket\b",
        r"can i (take|write|sit).{0,20}exam", r"allowed to (take|write|sit).{0,20}exam",
        r"exam (permission|clearance)", r"can i appear for",
        r"will i get my hall[ -]?ticket", r"blocked from the exam",
    )),
)

_FEE_ITEM_PATTERNS = (
    r"show my fee records", r"what have i paid", r"recorded fee items?",
    r"(?:give|show) me my fee breakdown", r"my fee breakdown",
    r"my recorded fee structure",
)
_PROFILE_PATTERNS = (
    r"what is my name", r"(?:can you )?tell me my name",
    r"(?:tell|show) me my faculty[ -]?id", r"what is my faculty[ -]?id",
    r"show my profile", r"which department am i in", r"what is my role",
    r"who am i",
)
_PERSONAL_CHAT_PATTERNS = (
    ("attendance_query", (
        r"show me my attendance status", r"what is my attendance",
        r"show my attendance", r"attendance status",
        r"show attendance subject wise",
        r"show my attendance status with subject name",
        r"attendance for each subject",
        r"(?:can|could|would) you show me my attendance",
        r"(?:could you )?check my attendance", r"how much attendance do i have",
        r"have i attended enough classes", r"show attendance please",
        r"show my attendance status(?: with (?:the )?subject names?)?",
        r"(?:show )?(?:my )?subject[ -]?wise attendance",
        r"(?:show )?(?:my )?attendance (?:for|in) each subject",
    )),
    ("fees_query", (
        r"do i owe anything", r"is (?:there )?any payment pending", r"my fee status",
    )),
    ("fee_items_query", _FEE_ITEM_PATTERNS),
    ("marks_query", (
        r"how did i do in internals", r"(?:can you )?show my cie marks",
        r"what marks did i get", r"show my subject marks",
    )),
    ("exam_query", (
        r"can i sit for the exam", r"will i get my hall[ -]?ticket",
        r"why am i blocked from the exam",
    )),
    ("profile_query", _PROFILE_PATTERNS),
)


def normalize_chat_query(query: str) -> str:
    """Conservative chat normalization without changing word interiors."""
    value = query.replace("’", "'").lower()
    value = re.sub(r"\bu\b", "you", value)
    value = re.sub(r"[,.!?;:]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def detect_personal_chat_intents(query: str) -> list[str]:
    """Detect explicit own-record language before the general-AI boundary."""
    lowered = normalize_chat_query(query)
    return [intent for intent, patterns in _PERSONAL_CHAT_PATTERNS
            if any(re.fullmatch(pattern, lowered) for pattern in patterns)]


def classify_supported_chat(query: str) -> IntentResult:
    """Classify supported paraphrases without modifying frozen routing data."""
    lowered = normalize_chat_query(query)
    if any(re.fullmatch(pattern, lowered) for pattern in _PROFILE_PATTERNS):
        return IntentResult("profile_query", "paraphrase", 0.0,
                            tool="get_my_profile", margin=1.0)
    if any(re.search(pattern, lowered) for pattern in _FEE_ITEM_PATTERNS):
        return IntentResult("fee_items_query", "paraphrase", 0.0,
                            tool="get_fees", margin=1.0)
    base = classify_keyword(lowered)
    if base.tool in {"get_attendance", "get_fees", "get_marks", "get_hall_ticket"}:
        return base
    started = time.perf_counter()
    for intent, patterns in _CHAT_PARAPHRASES:
        if any(re.search(pattern, lowered) for pattern in patterns):
            return IntentResult(intent, "paraphrase",
                                (time.perf_counter() - started) * 1000,
                                margin=1.0)
    return base


def detect_supported_chat_intents(query: str) -> list[str]:
    """Return every explicitly matched Phase 2 intent in stable order.

    This detector is deliberately separate from the frozen, single-winner
    routing instrument.  It is used only to prevent an ambiguous multi-topic
    request from reaching either Ollama or a data tool.
    """
    lowered = normalize_chat_query(query)
    supported = (
        "attendance_query", "fees_query", "marks_query", "exam_query",
    )
    paraphrases = dict(_CHAT_PARAPHRASES)
    matches = []
    for intent in supported:
        patterns = tuple(pattern for pattern, _weight in _LEXICON[intent])
        if any(re.search(r"\b(?:" + pattern + ")", lowered) for pattern in
               patterns + paraphrases.get(intent, ())):
            matches.append(intent)
    if any(re.search(pattern, lowered) for pattern in _PROFILE_PATTERNS):
        matches.append("profile_query")
    return matches


@dataclass
class RequestBudget:
    """One bounded deadline shared by health checks and all model rounds."""
    timeout_s: float = config.OLLAMA_TIMEOUT_S

    def __post_init__(self) -> None:
        self.deadline = time.monotonic() + self.timeout_s

    def remaining_s(self) -> float:
        return max(0.0, self.deadline - time.monotonic())


@dataclass
class OllamaResult:
    """Safe result envelope.  Errors are stable codes, never exception text."""
    message: dict | None = None
    error_code: str | None = None
    latency_ms: float = 0.0
    provider: str | None = None
    model: str | None = None


_TIMEOUT_ERRORS = frozenset({"timeout", "deadline_exceeded", "busy"})
_INVALID_RESPONSE_ERRORS = frozenset({
    "invalid_json", "response_too_large", "invalid_response", "model_mismatch",
    "incomplete_response", "truncated_response", "invalid_message",
    "unexpected_thinking", "invalid_content", "unexpected_markup",
    "invalid_tool_calls", "mixed_tool_response", "invalid_tool_call",
    "unknown_tool", "invalid_tool_arguments", "duplicate_tool_calls",
    "conflicting_tool_calls", "empty_response",
})


def safe_error_category(error_code: str | None) -> str:
    """Collapse internal failures into categories safe for operational logs."""
    if error_code in _TIMEOUT_ERRORS:
        return "timeout"
    if error_code == "connection_failure":
        return "connection_failure"
    if error_code == "model_missing":
        return "missing_model"
    if error_code in _INVALID_RESPONSE_ERRORS:
        return "invalid_response"
    return "ollama_http_error"


def _failed_result(error_code: str, started: float) -> OllamaResult:
    elapsed_ms = (time.perf_counter() - started) * 1000
    # Never log the URL, messages, response body, or exception text.
    logger.warning("Ollama request failed category=%s elapsed_ms=%.1f",
                   safe_error_category(error_code), elapsed_ms)
    return OllamaResult(error_code=error_code, latency_ms=elapsed_ms)


_health_available: bool | None = None
_health_error_code: str | None = None
_health_checked_at = 0.0
_health_lock = asyncio.Lock()
_chat_semaphore = asyncio.Semaphore(config.OLLAMA_CONCURRENCY)


def runtime_status() -> dict:
    """Safe runtime metadata; frozen evaluation metadata lives in router.py."""
    status = {
        "runtime_model": config.OLLAMA_MODEL,
        "host": config.OLLAMA_HOST,
        "available": _health_available,
        "health_error": _health_error_code,
    }
    provider = ai_provider.runtime_status()
    provider["ollama_available"] = _health_available
    status["generative_provider"] = provider
    return status


def reset_runtime_state_for_tests() -> None:
    """Reset process-local health/concurrency state for isolated unit tests."""
    global _health_available, _health_error_code, _health_checked_at, _chat_semaphore
    _health_available = None
    _health_error_code = None
    _health_checked_at = 0.0
    _chat_semaphore = asyncio.Semaphore(config.OLLAMA_CONCURRENCY)
    ai_provider.reset_runtime_state_for_tests()


def _set_health(available: bool, error_code: str | None = None) -> None:
    global _health_available, _health_error_code, _health_checked_at
    _health_available = available
    _health_error_code = error_code
    _health_checked_at = time.monotonic()


async def _request_json(method: str, path: str, budget: RequestBudget,
                        payload: dict | None = None) -> tuple[int | None, Any, str | None]:
    """Make one bounded request without exposing transport details upstream."""
    remaining = budget.remaining_s()
    if remaining <= 0:
        return None, None, "deadline_exceeded"
    try:
        async with httpx.AsyncClient(base_url=config.OLLAMA_HOST) as client:
            response = await client.request(method, path, json=payload, timeout=remaining)
    except httpx.TimeoutException:
        return None, None, "timeout"
    except httpx.RequestError:
        return None, None, "connection_failure"
    if len(response.content) > config.OLLAMA_MAX_RESPONSE_CHARS * 4:
        return response.status_code, None, "response_too_large"
    try:
        body = response.json()
    except (ValueError, TypeError):
        return response.status_code, None, "invalid_json"
    return response.status_code, body, None


def _tags_contain_configured_model(body: Any) -> bool:
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        return False
    for item in body["models"]:
        if isinstance(item, dict) and item.get("name") == config.OLLAMA_MODEL:
            return True
    return False


async def check_ollama_async(budget: RequestBudget | None = None,
                             force: bool = False) -> bool:
    """Bounded, coalesced health/model check with recovery after cooldown."""
    budget = budget or RequestBudget()
    now = time.monotonic()
    age = now - _health_checked_at
    if not force and _health_available is not None:
        if _health_available and age < config.OLLAMA_HEALTH_TTL_S:
            return True
        if not _health_available and age < config.OLLAMA_RETRY_COOLDOWN_S:
            return False
    async with _health_lock:
        now = time.monotonic()
        age = now - _health_checked_at
        if not force and _health_available is not None:
            if _health_available and age < config.OLLAMA_HEALTH_TTL_S:
                return True
            if not _health_available and age < config.OLLAMA_RETRY_COOLDOWN_S:
                return False
        status, body, error = await _request_json("GET", "/api/tags", budget)
        if error:
            _set_health(False, error)
            return False
        if status != 200:
            _set_health(False, "unavailable")
            return False
        if not isinstance(body, dict) or not isinstance(body.get("models"), list):
            _set_health(False, "invalid_response")
            return False
        if not _tags_contain_configured_model(body):
            _set_health(False, "model_missing")
            return False
        _set_health(True)
        return True


def _tool_parameters(tools: list[dict] | None) -> dict[str, dict]:
    allowed: dict[str, dict] = {}
    for item in tools or []:
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            continue
        name, params = function.get("name"), function.get("parameters")
        if not isinstance(name, str) or not isinstance(params, dict):
            continue
        properties = params.get("properties", {})
        if params.get("type") == "object" and isinstance(properties, dict):
            allowed[name] = params
    return allowed


def _has_unexpected_markup(content: str) -> bool:
    lowered = content.lower()
    return any(marker in lowered for marker in ("<think", "</think", "<tool", "</tool"))


def _normalized_arguments(value: Any) -> dict | None:
    """Accept Ollama's object form and its JSON-object string variant only."""
    if isinstance(value, str):
        def reject_duplicate_keys(pairs):
            parsed = {}
            for key, item in pairs:
                if key in parsed:
                    raise ValueError("duplicate JSON object key")
                parsed[key] = item
            return parsed

        try:
            value = json.loads(value, object_pairs_hook=reject_duplicate_keys)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    return value if isinstance(value, dict) else None


def _arguments_match_schema(arguments: dict, schema: dict | set[str]) -> bool:
    """Apply the supplied tool's closed, shallow JSON object schema strictly."""
    if isinstance(schema, set):  # Compatibility for focused validator tests.
        return set(arguments) <= schema
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if (schema.get("type") != "object" or not isinstance(properties, dict)
            or not isinstance(required, list)
            or not all(isinstance(item, str) for item in required)
            or not set(required) <= set(arguments)
            or not set(arguments) <= set(properties)):
        return False
    type_checks = {
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
    }
    for key, value in arguments.items():
        definition = properties[key]
        if not isinstance(definition, dict):
            return False
        expected = definition.get("type")
        check = type_checks.get(expected)
        if check is None or not check(value):
            return False
    return True


def _validate_message(body: Any, allowed_tools: dict[str, dict | set[str]]) -> tuple[dict | None, str | None]:
    if not isinstance(body, dict):
        return None, "invalid_response"
    if body.get("model") != config.OLLAMA_MODEL:
        return None, "model_mismatch"
    if body.get("done") is not True:
        return None, "incomplete_response"
    if body.get("done_reason") not in (None, "stop"):
        return None, "truncated_response"
    message = body.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None, "invalid_message"
    if "thinking" in message:
        return None, "unexpected_thinking"
    content = message.get("content", "")
    if not isinstance(content, str) or len(content) > config.OLLAMA_MAX_RESPONSE_CHARS:
        return None, "invalid_content"
    if _has_unexpected_markup(content):
        return None, "unexpected_markup"
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list):
        return None, "invalid_tool_calls"
    if calls:
        if content.strip():
            return None, "mixed_tool_response"
        normalized_calls = []
        for call in calls:
            if (not isinstance(call, dict)
                    or not set(call) <= {"id", "type", "function"}
                    or ("id" in call and not isinstance(call["id"], str))
                    or ("type" in call and call["type"] != "function")):
                return None, "invalid_tool_call"
            function = call.get("function")
            if (not isinstance(function, dict)
                    or not set(function) <= {"name", "arguments", "index"}
                    or ("index" in function and
                        (not isinstance(function["index"], int)
                         or isinstance(function["index"], bool)))):
                return None, "invalid_tool_call"
            name = function.get("name")
            if not isinstance(name, str) or name not in allowed_tools:
                return None, "unknown_tool"
            arguments = _normalized_arguments(function.get("arguments"))
            if arguments is None or not _arguments_match_schema(
                    arguments, allowed_tools[name]):
                return None, "invalid_tool_arguments"
            normalized_function = dict(function)
            normalized_function["arguments"] = arguments
            normalized_call = dict(call)
            normalized_call["function"] = normalized_function
            normalized_calls.append(normalized_call)
        if len(normalized_calls) > 1:
            signatures = [
                (call["function"]["name"], call["function"]["arguments"])
                for call in normalized_calls
            ]
            if all(item == signatures[0] for item in signatures[1:]):
                return None, "duplicate_tool_calls"
            return None, "conflicting_tool_calls"
        message = dict(message)
        message["tool_calls"] = normalized_calls
    elif not content.strip():
        return None, "empty_response"
    return message, None


async def chat_async(messages: list[dict], tools: list[dict] | None = None,
                     budget: RequestBudget | None = None) -> OllamaResult:
    """One validated, concurrency-limited Ollama chat request."""
    budget = budget or RequestBudget()
    started = time.perf_counter()
    if not await check_ollama_async(budget):
        return _failed_result(_health_error_code or "unavailable", started)
    acquired = False
    try:
        remaining = budget.remaining_s()
        if remaining <= 0:
            return _failed_result("deadline_exceeded", started)
        try:
            await asyncio.wait_for(_chat_semaphore.acquire(), timeout=remaining)
            acquired = True
        except TimeoutError:
            return _failed_result("busy", started)
        body = {
            "model": config.OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "keep_alive": f"{config.OLLAMA_KEEP_ALIVE_S}s",
            "options": {
                "temperature": 0.1,
                "num_ctx": config.OLLAMA_CONTEXT_TOKENS,
                "num_predict": config.OLLAMA_MAX_OUTPUT_TOKENS,
            },
        }
        if tools:
            body["tools"] = tools
        status, payload, error = await _request_json("POST", "/api/chat", budget, body)
        latency = (time.perf_counter() - started) * 1000
        if error:
            return _failed_result(error, started)
        if status == 404:
            _set_health(False, "model_missing")
            return _failed_result("model_missing", started)
        if status != 200:
            return _failed_result("http_error", started)
        message, validation_error = _validate_message(payload, _tool_parameters(tools))
        if validation_error:
            return _failed_result(validation_error, started)
        return OllamaResult(message=message, latency_ms=latency,
                            provider="ollama", model=config.OLLAMA_MODEL)
    finally:
        if acquired:
            _chat_semaphore.release()


def check_ollama(force: bool = False) -> bool:
    """Compatibility wrapper for offline evaluation scripts outside an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(check_ollama_async(force=force))
    return bool(_health_available)


async def general_chat_async(messages: list[dict], *, user_key: str,
                             budget: RequestBudget | None = None) -> OllamaResult:
    """Run one sanitized tool-free turn through the configured provider layer."""
    budget = budget or RequestBudget()

    async def local_call():
        return await chat_async(messages, tools=None, budget=budget)

    return await ai_provider.generate_async(
        messages, user_key=user_key, ollama_call=local_call)


def chat(messages: list[dict], tools: list[dict] | None = None) -> dict | None:
    """Compatibility wrapper; FastAPI must use ``chat_async`` instead."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        result = asyncio.run(chat_async(messages, tools=tools))
        return result.message
    return None
