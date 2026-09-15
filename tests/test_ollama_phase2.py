"""Phase 2 Ollama client and grounded-chat safety, using no live service."""
import asyncio
import json
import os
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.orm import Session

from backend.app import config, llm, router
from backend.app.agents import tools
from backend.app.agents.orchestrator import OrchestratorAgent
from backend.app.models import IntentLog, User


def _tags():
    return {"models": [{"name": config.OLLAMA_MODEL}]}


def _response(message, *, done=True, done_reason="stop", model=None):
    return {"model": model or config.OLLAMA_MODEL, "done": done,
            "done_reason": done_reason, "message": message}


@pytest.fixture(autouse=True)
def _reset_ollama_state():
    llm.reset_runtime_state_for_tests()
    yield
    llm.reset_runtime_state_for_tests()


def test_timeout_defaults_to_90_seconds_and_accepts_configuration(monkeypatch):
    monkeypatch.delenv("MAWOS_OLLAMA_TIMEOUT_SECONDS", raising=False)
    assert config._bounded_positive_float_setting(
        "MAWOS_OLLAMA_TIMEOUT_SECONDS", 90.0, 600.0) == 90.0
    monkeypatch.setenv("MAWOS_OLLAMA_TIMEOUT_SECONDS", "135.5")
    assert config._bounded_positive_float_setting(
        "MAWOS_OLLAMA_TIMEOUT_SECONDS", 90.0, 600.0) == 135.5


def test_legacy_timeout_name_remains_supported(monkeypatch):
    monkeypatch.delenv("MAWOS_OLLAMA_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("MAWOS_OLLAMA_TIMEOUT", "45")
    setting = ("MAWOS_OLLAMA_TIMEOUT_SECONDS"
               if os.getenv("MAWOS_OLLAMA_TIMEOUT_SECONDS", "").strip()
               else "MAWOS_OLLAMA_TIMEOUT")
    assert config._bounded_positive_float_setting(setting, 90.0, 600.0) == 45.0


@pytest.mark.parametrize("value", ["0", "-1", "601", "nan", "inf", "not-a-number"])
def test_timeout_rejects_unsafe_values(monkeypatch, value):
    monkeypatch.setenv("MAWOS_OLLAMA_TIMEOUT_SECONDS", value)
    with pytest.raises(config.ConfigurationError):
        config._bounded_positive_float_setting(
            "MAWOS_OLLAMA_TIMEOUT_SECONDS", 90.0, 600.0)


def test_health_rejects_missing_model_and_recovers_after_forced_retry(monkeypatch):
    replies = [(200, {"models": []}, None), (200, _tags(), None)]

    async def fake_request(*args, **kwargs):
        return replies.pop(0)

    monkeypatch.setattr(llm, "_request_json", fake_request)
    assert asyncio.run(llm.check_ollama_async(force=True)) is False
    assert llm.runtime_status()["health_error"] == "model_missing"
    assert asyncio.run(llm.check_ollama_async(force=True)) is True
    assert llm.runtime_status()["available"] is True


@pytest.mark.parametrize("body, expected", [
    (_response({"role": "assistant", "content": "{}"}, done=False), "incomplete_response"),
    (_response({"role": "assistant", "content": "<think>private</think>"}), "unexpected_markup"),
    (_response({"role": "assistant", "content": "", "tool_calls": [{"function": {
        "name": "get_placements", "arguments": {}}}]}), "unknown_tool"),
    (_response({"role": "assistant", "content": "", "tool_calls": [{"function": {
        "name": "get_attendance", "arguments": {"ignored": "x"}}}]}), "invalid_tool_arguments"),
    (_response({"role": "assistant", "content": "{}"}, model="other:model"), "model_mismatch"),
])
def test_client_rejects_malformed_or_forged_messages(body, expected):
    allowed = {"get_attendance": {"usn"}}
    _, error = llm._validate_message(body, allowed)
    assert error == expected


def _attendance_schema():
    return {"get_attendance": {
        "type": "object",
        "properties": {"usn": {"type": "string"}},
        "required": [],
    }}


def test_accepts_exact_qwen25_ollama_tool_call_shape():
    """Observed qwen2.5:3b adds call id and function index metadata."""
    body = _response({
        "role": "assistant", "content": "", "tool_calls": [{
            "id": "call_safe_synthetic", "function": {
                "index": 0, "name": "get_attendance", "arguments": {},
            },
        }],
    })
    message, error = llm._validate_message(body, _attendance_schema())
    assert error is None
    assert message["tool_calls"][0]["function"]["arguments"] == {}


@pytest.mark.parametrize("arguments", [
    {"usn": "SYNTHETIC001"},
    '{"usn":"SYNTHETIC001"}',
])
def test_accepts_object_and_json_object_string_arguments(arguments):
    body = _response({"role": "assistant", "content": "", "tool_calls": [{
        "function": {"name": "get_attendance", "arguments": arguments},
    }]})
    message, error = llm._validate_message(body, _attendance_schema())
    assert error is None
    assert message["tool_calls"][0]["function"]["arguments"] == {
        "usn": "SYNTHETIC001",
    }


@pytest.mark.parametrize("arguments", [
    "{malformed", '"plain string"', "[]", "1", [], 1, None,
    '{"usn":"SYNTHETIC001","usn":"SYNTHETIC002"}',
    {"usn": ["SYNTHETIC001"]}, {"secret": "synthetic"},
])
def test_rejects_malformed_non_object_or_schema_invalid_arguments(arguments):
    body = _response({"role": "assistant", "content": "", "tool_calls": [{
        "function": {"name": "get_attendance", "arguments": arguments},
    }]})
    message, error = llm._validate_message(body, _attendance_schema())
    assert message is None
    assert error == "invalid_tool_arguments"


def test_rejects_duplicate_and_conflicting_parallel_calls():
    duplicate = _response({"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "get_attendance", "arguments": {}}},
        {"function": {"name": "get_attendance", "arguments": "{}"}},
    ]})
    _, duplicate_error = llm._validate_message(duplicate, _attendance_schema())
    assert duplicate_error == "duplicate_tool_calls"

    conflicting = _response({"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "get_fees", "arguments": {}}},
        {"function": {"name": "get_marks", "arguments": {}}},
    ]})
    _, conflict_error = llm._validate_message(
        conflicting, {"get_fees": set(), "get_marks": set()})
    assert conflict_error == "conflicting_tool_calls"


@pytest.mark.parametrize("question", ["fees marks", "attendance fees"])
def test_multi_intent_clarifies_without_model_tool_or_database(
        question, agents, monkeypatch):
    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError(f"database access before clarification: {name}")

    async def forbidden_model(*args, **kwargs):
        raise AssertionError("model called before clarification")

    async def forbidden_router(*args, **kwargs):
        raise AssertionError("router called before clarification")

    def forbidden_tool(*args, **kwargs):
        raise AssertionError("tool called before clarification")

    monkeypatch.setattr(llm, "chat_async", forbidden_model)
    monkeypatch.setattr(router, "decide_async", forbidden_router)
    monkeypatch.setattr(tools, "execute_chat", forbidden_tool)
    user = SimpleNamespace(role="student", usn="SYNTHETIC001")
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(
        NoDatabase(), user, question))
    assert response["mode"] == "scope"
    assert response["tools_used"] == []
    assert response["fallback_code"] is None
    assert response["routing"] == {
        "tier": "scope", "margin": 0.0, "tau": router.TAU,
        "escalated": False, "attempted_llm": False,
        "accepted_llm": False, "deterministic_fallback": False,
        "reason": "multiple supported intents require clarification",
        "fallback_from": None,
    }


def test_student_tool_schemas_do_not_expose_identity_parameters():
    student_schemas = tools.chat_schemas_for_role("student")
    assert student_schemas
    assert all(item["function"]["parameters"] == {
        "type": "object", "properties": {}, "required": [],
    } for item in student_schemas)

    faculty_schemas = tools.chat_schemas_for_role("faculty")
    attendance = next(item for item in faculty_schemas
                      if item["function"]["name"] == "get_attendance")
    assert set(attendance["function"]["parameters"]["properties"]) == {"usn"}


def test_chat_timeout_and_concurrency_are_bounded(monkeypatch):
    async def available(*args, **kwargs):
        return True

    async def timeout(*args, **kwargs):
        return None, None, "timeout"

    monkeypatch.setattr(llm, "check_ollama_async", available)
    monkeypatch.setattr(llm, "_request_json", timeout)
    result = asyncio.run(llm.chat_async([{"role": "user", "content": "x"}]))
    assert result.message is None and result.error_code == "timeout"

    started, release = asyncio.Event(), asyncio.Event()
    in_flight = 0

    async def delayed_request(method, path, budget, payload=None):
        nonlocal in_flight
        assert method == "POST" and path == "/api/chat"
        in_flight += 1
        started.set()
        await release.wait()
        in_flight -= 1
        return 200, _response({"role": "assistant", "content": "{}"}), None

    monkeypatch.setattr(llm, "_request_json", delayed_request)

    async def scenario():
        first = asyncio.create_task(llm.chat_async([{"role": "user", "content": "one"}],
                                                    budget=llm.RequestBudget(2)))
        await started.wait()
        second = asyncio.create_task(llm.chat_async([{"role": "user", "content": "two"}],
                                                     budget=llm.RequestBudget(2)))
        await asyncio.sleep(0)
        assert in_flight == 1
        release.set()
        return await asyncio.gather(first, second)

    results = asyncio.run(scenario())
    assert all(result.error_code is None for result in results)


def test_slower_response_succeeds_within_configured_budget_and_uses_concise_options(
        monkeypatch):
    async def available(*args, **kwargs):
        return True

    async def delayed_request(method, path, budget, payload=None):
        assert method == "POST" and path == "/api/chat"
        assert budget.timeout_s == 0.2
        assert payload["model"] == "qwen2.5:3b"
        assert payload["options"]["num_predict"] == 160
        assert payload["options"]["num_predict"] <= 180
        assert payload["keep_alive"] == "300s"
        await asyncio.sleep(0.03)
        return 200, _response({"role": "assistant", "content": "A concise answer."}), None

    monkeypatch.setattr(llm, "check_ollama_async", available)
    monkeypatch.setattr(llm, "_request_json", delayed_request)
    result = asyncio.run(llm.chat_async(
        [{"role": "user", "content": "Explain x"}], budget=llm.RequestBudget(0.2)))
    assert result.error_code is None
    assert result.message["content"] == "A concise answer."
    assert result.latency_ms >= 20


@pytest.mark.parametrize(("error_code", "category"), [
    ("timeout", "timeout"),
    ("connection_failure", "connection_failure"),
    ("model_missing", "missing_model"),
    ("invalid_json", "invalid_response"),
    ("invalid_message", "invalid_response"),
    ("http_error", "ollama_http_error"),
])
def test_safe_error_classification(error_code, category):
    assert llm.safe_error_category(error_code) == category


def test_connection_failure_log_contains_only_safe_category_and_elapsed_time(
        caplog, monkeypatch):
    secret = "student-private-value"

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **_kwargs):
            raise httpx.ConnectError(secret)

    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **_kwargs: FailingClient())
    with caplog.at_level("WARNING"):
        result = asyncio.run(llm.chat_async(
            [{"role": "user", "content": secret}], budget=llm.RequestBudget(0.2)))
    assert result.error_code == "connection_failure"
    assert "category=connection_failure" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert secret not in caplog.text


def _force_llm(monkeypatch):
    async def decide(query, budget):
        result = llm.classify_keyword(query)
        return result, router.Decision("llm", 0.0, True, "mocked LLM route")

    monkeypatch.setattr(router, "decide_async", decide)


def test_supported_paraphrase_is_deterministic_and_never_calls_ollama(agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()

    async def forbidden(*args, **kwargs):
        raise AssertionError("paraphrase should be classified locally")

    monkeypatch.setattr(llm, "chat_async", forbidden)
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, user, "How often have I attended class?"))
    assert response["mode"] == "lexicon"
    assert response["intent"] == "attendance_query"
    assert response["routing"]["reason"] == "recognized supported Phase 2 paraphrase"


@pytest.mark.parametrize("question", [
    "Could you summarize how regularly I have attended classes?",
    "How regularly have I attended classes?",
    "How often have I attended class?",
    "Summarize my class attendance.",
    "Have I been attending classes regularly?",
])
def test_attendance_paraphrases_execute_one_authorized_read_only_tool(
        question, agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    executions, commits, flushes = [], [], []
    original_execute = tools.execute_chat
    original_commit, original_flush = Session.commit, Session.flush

    async def forbidden_model(*args, **kwargs):
        raise AssertionError("clear attendance paraphrase must not call Ollama")

    def track_execute(call_db, call_agents, call_user, name, args):
        executions.append((call_user, name, args))
        return original_execute(call_db, call_agents, call_user, name, args)

    def track_commit(session, *args, **kwargs):
        commits.append(session)
        return original_commit(session, *args, **kwargs)

    def track_flush(session, *args, **kwargs):
        flushes.append(session)
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(llm, "chat_async", forbidden_model)
    monkeypatch.setattr(tools, "execute_chat", track_execute)
    monkeypatch.setattr(Session, "commit", track_commit)
    monkeypatch.setattr(Session, "flush", track_flush)
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, user, question))

    assert response["mode"] == "lexicon"
    assert response["intent"] == "attendance_query"
    assert response["fallback"] is False
    assert response["routing"]["attempted_llm"] is False
    assert len(executions) == 1
    assert executions[0] == (user, "get_attendance", {})
    assert len(response["tools_used"]) == 1
    assert commits == [] and flushes == []


@pytest.mark.parametrize("question", [
    "Show my profile.",
    "Give me my dashboard overview.",
    "Who am I?",
])
def test_profile_queries_do_not_become_attendance(question):
    result = llm.classify_supported_chat(question)
    assert result.intent == "profile_query"


@pytest.mark.parametrize("question", [
    "Could you summarize this?",
    "How am I doing?",
    "Tell me about my records.",
])
def test_unclear_requests_still_require_clarification(
        question, agents, monkeypatch):
    async def forbidden_model(*args, **kwargs):
        raise AssertionError("unclear request must not call Ollama")

    def forbidden_tool(*args, **kwargs):
        raise AssertionError("unclear request must not select a record")

    monkeypatch.setattr(llm, "chat_async", forbidden_model)
    monkeypatch.setattr(tools, "execute_chat", forbidden_tool)
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(
        object(), SimpleNamespace(role="student", usn="SYNTHETIC001"),
        question))
    assert response["mode"] == "scope"
    assert response["intent"] == "profile_query"
    assert response["routing"]["reason"] == "supported record type is unclear"
    assert response["tools_used"] == []


@pytest.mark.parametrize("question", [
    "How regularly have I attended classes and what fees do I owe?",
    "Have I been attending classes regularly and show my marks.",
])
def test_attendance_paraphrase_with_another_intent_still_clarifies(
        question, agents, monkeypatch):
    async def forbidden_model(*args, **kwargs):
        raise AssertionError("multi-intent request must not call Ollama")

    def forbidden_tool(*args, **kwargs):
        raise AssertionError("multi-intent request must not execute a tool")

    monkeypatch.setattr(llm, "chat_async", forbidden_model)
    monkeypatch.setattr(tools, "execute_chat", forbidden_tool)
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(
        object(), SimpleNamespace(role="student", usn="SYNTHETIC001"),
        question))
    assert response["mode"] == "scope"
    assert response["routing"]["reason"] == (
        "multiple supported intents require clarification")
    assert response["routing"]["attempted_llm"] is False
    assert response["tools_used"] == []


def test_tool_free_or_incorrect_non_numeric_model_answer_falls_back(agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    _force_llm(monkeypatch)

    async def tool_free(*args, **kwargs):
        return llm.OllamaResult(message={"role": "assistant", "content": "Your attendance is excellent."})

    monkeypatch.setattr(llm, "chat_async", tool_free)
    no_tool = asyncio.run(agents["orchestrator_agent"].handle_chat(db, user, "attendance"))
    assert no_tool["mode"] == "lexicon"
    assert no_tool["fallback"] is False
    assert no_tool["fallback_code"] is None

    calls = 0

    async def incorrect_final(messages, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return llm.OllamaResult(message={"role": "assistant", "content": "", "tool_calls": [{
                "function": {"name": "get_attendance", "arguments": {}}}]})
        tool_message = next(item for item in messages if item.get("role") == "tool")
        evidence = json.loads(tool_message["content"])
        # Even a correct reference is rejected when the model adds a claim.
        return llm.OllamaResult(message={"role": "assistant", "content": json.dumps({
            "tool": evidence["tool"], "evidence_ref": evidence["evidence_ref"],
            "answer": "invented",
        })})

    monkeypatch.setattr(llm, "chat_async", incorrect_final)
    wrong_claim = asyncio.run(agents["orchestrator_agent"].handle_chat(db, user, "attendance"))
    assert wrong_claim["mode"] == "lexicon"
    assert wrong_claim["fallback"] is False
    assert wrong_claim["fallback_code"] is None


def test_verified_model_answer_is_read_only(agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    _force_llm(monkeypatch)
    calls, commits, flushes, executions, exposed_tools = 0, [], [], 0, []
    original_commit, original_flush = Session.commit, Session.flush
    original_execute = tools.execute_chat

    def track_commit(session, *args, **kwargs):
        commits.append(session)
        return original_commit(session, *args, **kwargs)

    def track_flush(session, *args, **kwargs):
        flushes.append(session)
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "commit", track_commit)
    monkeypatch.setattr(Session, "flush", track_flush)

    def track_execute(*args, **kwargs):
        nonlocal executions
        executions += 1
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(tools, "execute_chat", track_execute)

    async def exact_copy(messages, *args, **kwargs):
        nonlocal calls
        calls += 1
        exposed_tools.append(kwargs.get("tools"))
        if calls == 1:
            return llm.OllamaResult(message={"role": "assistant", "content": "", "tool_calls": [{
                "function": {"name": "get_fees", "arguments": {}}}]})
        tool_message = next(item for item in messages if item.get("role") == "tool")
        evidence = json.loads(tool_message["content"])
        return llm.OllamaResult(message={"role": "assistant", "content": json.dumps({
            "tool": evidence["tool"], "evidence_ref": evidence["evidence_ref"],
        })})

    monkeypatch.setattr(llm, "chat_async", exact_copy)
    before_logs = db.query(IntentLog).count()
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(db, user, "fees"))

    assert response["mode"] == "lexicon"
    assert response["fallback"] is False
    assert response.get("model") is None
    assert response["routing"]["attempted_llm"] is False
    assert response["routing"]["accepted_llm"] is False
    assert response["routing"]["deterministic_fallback"] is False
    assert executions == 1
    assert len(response["tools_used"]) == 1
    assert exposed_tools == []
    assert commits == [] and flushes == []
    assert db.query(IntentLog).count() == before_logs


def _synthetic_grounding_cases():
    return [
        ("get_fees", {
            "usn": "SYNTHETIC001", "cleared": True,
            "total_outstanding": 0.0, "items": [],
        }),
        ("get_fees", {
            "usn": "SYNTHETIC001", "cleared": False,
            "total_outstanding": 20190.0,
            "items": [{
                "type": f"Synthetic fee {index}",
                "amount_due": float(1000 + index), "fine": 0.0,
                "status": "pending", "due_date": "2099-01-01",
            } for index in range(20)],
        }),
        ("get_attendance", {
            "usn": "SYNTHETIC001", "overall_pct": 80.0,
            "subjects": [{
                "subject": f"SYN{index:02d}", "attended": 40,
                "held": 50, "pct": 80.0, "shortage": False,
            } for index in range(30)],
        }),
        ("get_marks", {
            "usn": "SYNTHETIC001", "marks": [{
                "subject": f"SYN{index:02d}", "name": f"Synthetic {index}",
                "internals": {"CIE-1": 20, "CIE-2": 21, "CIE-3": 22},
                "cie_average": 21.0,
            } for index in range(30)],
        }),
    ]


@pytest.mark.parametrize(("tool_name", "synthetic_result"),
                         _synthetic_grounding_cases())
def test_compact_reference_accepts_short_and_long_server_answers_once(
        tool_name, synthetic_result, monkeypatch):
    agent = OrchestratorAgent(None, {})
    user = SimpleNamespace(role="student", usn="SYNTHETIC001")
    model_round, executions = 0, 0

    def execute_once(*args, **kwargs):
        nonlocal executions
        executions += 1
        return synthetic_result

    async def compact_ack(messages, tools=None, budget=None):
        nonlocal model_round
        model_round += 1
        if model_round == 1:
            assert tools is not None
            return llm.OllamaResult(message={
                "role": "assistant", "content": "", "tool_calls": [{
                    "function": {"name": tool_name, "arguments": {}},
                }],
            })
        assert tools is None
        evidence = json.loads(next(
            item["content"] for item in messages if item.get("role") == "tool"))
        assert set(evidence) == {"tool", "evidence_ref", "instruction"}
        return llm.OllamaResult(message={
            "role": "assistant", "content": json.dumps({
                "tool": evidence["tool"],
                "evidence_ref": evidence["evidence_ref"],
            }),
        })

    monkeypatch.setattr(tools, "execute_chat", execute_once)
    monkeypatch.setattr(llm, "chat_async", compact_ack)
    response, error = asyncio.run(agent._handle_llm(
        object(), user, "synthetic single intent", llm.RequestBudget(5)))
    assert error is None
    assert response["mode"] == "llm"
    assert response["text"] == agent._format(tool_name, synthetic_result)
    assert len(response["tools_used"]) == 1
    assert model_round == 2 and executions == 1


def test_compact_reference_rejects_wrong_tool_or_fact_set():
    agent = OrchestratorAgent(None, {})
    cleared = {"usn": "SYNTHETIC001", "cleared": True,
               "total_outstanding": 0.0, "items": []}
    outstanding = {"usn": "SYNTHETIC001", "cleared": False,
                   "total_outstanding": 1000.0, "items": [{
                       "type": "Synthetic", "amount_due": 1000.0,
                       "fine": 0.0, "status": "pending",
                       "due_date": "2099-01-01",
                   }]}
    expected_ref = agent._evidence_ref("get_fees", cleared)
    other_ref = agent._evidence_ref("get_fees", outstanding)
    assert agent._validated_final(json.dumps({
        "tool": "get_marks", "evidence_ref": expected_ref,
    }), "get_fees", expected_ref) is None
    assert agent._validated_final(json.dumps({
        "tool": "get_fees", "evidence_ref": other_ref,
    }), "get_fees", expected_ref) is None


def test_forged_or_denied_model_tool_never_reaches_non_chat_service(agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    _force_llm(monkeypatch)
    placement = agents["placement_agent"]
    monkeypatch.setattr(placement, "student_view", lambda *args: (_ for _ in ()).throw(AssertionError()))

    async def forged(*args, **kwargs):
        return llm.OllamaResult(message={"role": "assistant", "content": "", "tool_calls": [{
            "function": {"name": "get_placements", "arguments": {"usn": "4MT23AI002"}}}]})

    monkeypatch.setattr(llm, "chat_async", forged)
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(db, user, "attendance"))
    assert response["mode"] == "lexicon"
    assert response["fallback_code"] is None
    assert response["routing"]["attempted_llm"] is False
    assert response["routing"]["accepted_llm"] is False
    assert response["routing"]["deterministic_fallback"] is False


@pytest.mark.parametrize("second_calls", [
    [{"function": {"name": "get_fees", "arguments": {}}}],
    [{"function": {"name": "get_marks", "arguments": {}}}],
    [{"function": {"name": "unknown_tool", "arguments": {}}}],
    [{"function": {"name": "pay_fee", "arguments": {}}}],
    [
        {"function": {"name": "get_fees", "arguments": {}}},
        {"function": {"name": "get_fees", "arguments": {}}},
    ],
    [
        {"function": {"name": "get_fees", "arguments": {}}},
        {"function": {"name": "get_marks", "arguments": {}}},
    ],
])
def test_final_stage_rejects_every_additional_tool_without_reexecution(
        second_calls, agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    _force_llm(monkeypatch)
    model_round, executions = 0, 0
    original_execute = tools.execute_chat

    def track_execute(*args, **kwargs):
        nonlocal executions
        executions += 1
        return original_execute(*args, **kwargs)

    async def repeated_tool(messages, tools=None, budget=None):
        nonlocal model_round
        model_round += 1
        if model_round == 1:
            assert tools is not None
            return llm.OllamaResult(message={
                "role": "assistant", "content": "", "tool_calls": [{
                    "function": {"name": "get_fees", "arguments": {}},
                }],
            })
        assert tools is None
        return llm.OllamaResult(message={
            "role": "assistant", "content": "", "tool_calls": second_calls,
        })

    monkeypatch.setattr(tools, "execute_chat", track_execute)
    monkeypatch.setattr(llm, "chat_async", repeated_tool)
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, user, "fees"))
    assert executions == 1
    assert len(response["tools_used"]) == 1
    assert response["mode"] == "lexicon"
    assert response["fallback_code"] is None
    assert response["routing"]["attempted_llm"] is False
    assert response["routing"]["accepted_llm"] is False
    assert response["routing"]["deterministic_fallback"] is False


@pytest.mark.parametrize(("final_result", "expected_code"), [
    (llm.OllamaResult(error_code="unexpected_markup"), "unexpected_markup"),
    (llm.OllamaResult(error_code="truncated_response"), "truncated_response"),
    (llm.OllamaResult(error_code="timeout"), "timeout"),
    (llm.OllamaResult(message={"role": "assistant", "content": "{malformed"}),
     "grounding_validation_failed"),
    (llm.OllamaResult(message={"role": "assistant", "content": json.dumps({
        "tool": "get_fees", "evidence_ref": "wrong-reference",
    })}), "grounding_validation_failed"),
])
def test_final_stage_failures_reuse_one_authorized_result_as_safe_fallback(
        final_result, expected_code, agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    _force_llm(monkeypatch)
    model_round, executions = 0, 0
    original_execute = tools.execute_chat

    def track_execute(*args, **kwargs):
        nonlocal executions
        executions += 1
        return original_execute(*args, **kwargs)

    async def two_stage(messages, tools=None, budget=None):
        nonlocal model_round
        model_round += 1
        if model_round == 1:
            return llm.OllamaResult(message={
                "role": "assistant", "content": "", "tool_calls": [{
                    "function": {"name": "get_fees", "arguments": {}},
                }],
            })
        assert tools is None
        return final_result

    monkeypatch.setattr(tools, "execute_chat", track_execute)
    monkeypatch.setattr(llm, "chat_async", two_stage)
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, user, "fees"))
    assert model_round == 0 and executions == 1
    assert response["mode"] == "lexicon"
    assert response["fallback"] is False
    assert response["fallback_code"] is None
    assert response["intent"] == "fees_query"
    assert response["routing"]["attempted_llm"] is False
    assert response["routing"]["accepted_llm"] is False
    assert response["routing"]["deterministic_fallback"] is False


@pytest.mark.parametrize("selection_error", ["timeout", "truncated_response"])
def test_selection_stage_failure_falls_back_without_model_selected_execution(
        selection_error, agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    _force_llm(monkeypatch)
    executions = 0
    original_execute = tools.execute_chat

    def track_execute(*args, **kwargs):
        nonlocal executions
        executions += 1
        return original_execute(*args, **kwargs)

    async def failed_selection(*args, **kwargs):
        return llm.OllamaResult(error_code=selection_error)

    monkeypatch.setattr(tools, "execute_chat", track_execute)
    monkeypatch.setattr(llm, "chat_async", failed_selection)
    response = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, user, "fees"))
    # One deterministic fallback read; no model-selected tool was executed.
    assert executions == 1
    assert response["mode"] == "lexicon"
    assert response["fallback_code"] is None
    assert response["routing"]["attempted_llm"] is False
    assert response["routing"]["accepted_llm"] is False
