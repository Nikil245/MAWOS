"""Natural personal-record routing and authenticated own-profile access."""
import asyncio
import json

import pytest
from sqlalchemy.orm import Session

from backend.app import llm
from backend.app.agents import tools
from backend.app.api.schemas import ChatResponse
from backend.app.models import User
from test_read_only_chat import _domain_snapshot, _ensure_chat_scope


def ask(agents, db, user, question):
    result = asyncio.run(agents["orchestrator_agent"].handle_chat(db, user, question))
    ChatResponse.model_validate(result)
    return result


@pytest.mark.parametrize("question,expected_tool", [
    ("can u show me my attendance", "get_attendance"),
    ("CAN U SHOW ME MY ATTENDANCE!!!", "get_attendance"),
    ("could you check my attendance", "get_attendance"),
    ("how much attendance do I have", "get_attendance"),
    ("have I attended enough classes", "get_attendance"),
    ("show attendance please", "get_attendance"),
    ("do i owe anything", "get_fees"),
    ("is any payment pending", "get_fees"),
    ("show my fee records", "get_fees"),
    ("what have I paid", "get_fees"),
    ("show my recorded fee items", "get_fees"),
    ("give me my fee breakdown", "get_fees"),
    ("how did i do in internals", "get_marks"),
    ("can u show my cie marks", "get_marks"),
    ("what marks did i get", "get_marks"),
    ("show my subject marks", "get_marks"),
    ("can i sit for the exam", "get_hall_ticket"),
    ("will i get my hall ticket", "get_hall_ticket"),
    ("why am i blocked from the exam", "get_hall_ticket"),
])
def test_natural_student_requests_execute_one_tool_and_never_general_ai(
        question, expected_tool, agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    original = tools.execute_chat
    executions = []

    async def forbidden_general(*args, **kwargs):
        raise AssertionError("clear personal request reached Ollama/general_ai")

    def tracked(*args):
        executions.append(args[3])
        return original(*args)

    monkeypatch.setattr(llm, "chat_async", forbidden_general)
    monkeypatch.setattr(tools, "execute_chat", tracked)
    result = ask(agents, db, user, question)
    assert result["category"] == "personal_record"
    assert result["mode"] == "lexicon"
    assert executions == [expected_tool]
    assert len(result["tools_used"]) == 1


@pytest.mark.parametrize("question", [
    "Can u tell me my Name", "WHAT IS MY NAME???", "show my profile",
    "which department am I in", "what is my role",
])
def test_student_profile_wording_is_authenticated_and_tool_backed(
        question, agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    monkeypatch.setattr(llm, "chat_async", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("profile paraphrase should not need Ollama")))
    result = ask(agents, db, user, question)
    assert result["category"] == "personal_record"
    assert result["tools_used"][0]["name"] == "get_my_profile"
    assert "Display name: Good Student" in result["text"]
    assert "USN: 4MT23AI001" in result["text"]
    assert "Department: AIML" in result["text"]
    assert "password" not in json.dumps(result).lower()


def test_own_profile_for_every_role_has_only_allowlisted_fields(agents, db):
    _ensure_chat_scope(db)
    expected = {
        "student": {"display_name", "role", "usn", "department", "year", "semester", "section"},
        "faculty": {"display_name", "role", "faculty_id", "department", "designation"},
        "hod": {"display_name", "role", "department"},
        "principal": {"display_name", "role"},
        "admin": {"display_name", "role"},
    }
    usernames = {
        "student": "4MT23AI001", "faculty": "chat.faculty", "hod": "chat.hod",
        "principal": "chat.principal", "admin": "chat.admin",
    }
    for role, username in usernames.items():
        user = db.query(User).filter_by(username=username).one()
        raw = tools.execute_chat(db, agents, user, "get_my_profile", {})
        assert set(raw) == expected[role]
        assert raw["display_name"] == user.display_name
        assert raw["role"] == role
        assert not ({"password_hash", "email", "phone", "username", "id"} & set(raw))


def test_own_profile_schema_has_no_target_parameters_for_any_role():
    for role in tools.ALL_ROLES:
        schema = next(item["function"] for item in tools.chat_schemas_for_role(role)
                      if item["function"]["name"] == "get_my_profile")
        assert schema["parameters"] == {"type": "object", "properties": {}, "required": []}


@pytest.mark.parametrize("question", [
    "show another student's profile", "show my friend's name", "show Alice's profile",
])
def test_other_person_profile_is_denied_without_model_or_tool(
        question, agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    monkeypatch.setattr(llm, "chat_async", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(tools, "execute_chat", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    result = ask(agents, db, user, question)
    assert result["category"] == "sensitive_or_disallowed"
    assert result["routing"]["attempted_llm"] is False
    assert "exist" not in result["text"].lower()


def test_incomplete_personal_wording_clarifies_and_profile_plus_record_is_multi_intent(
        agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    monkeypatch.setattr(llm, "chat_async", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(tools, "execute_chat", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    assert ask(agents, db, user, "can u tell me my")["category"] == "clarification"
    multi = ask(agents, db, user, "show my profile and attendance")
    assert multi["category"] == "clarification"
    assert "multiple topics" in multi["text"].lower()


def test_fee_status_breakdown_official_and_ambiguous_are_distinct(
        agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    original = tools.execute_chat
    calls = []

    async def forbidden(*args, **kwargs):
        raise AssertionError("these deterministic routes must not call Ollama")

    def tracked(*args):
        calls.append(args[3])
        return original(*args)

    monkeypatch.setattr(llm, "chat_async", forbidden)
    monkeypatch.setattr(tools, "execute_chat", tracked)
    status = ask(agents, db, user, "show my fee status")
    breakdown = ask(agents, db, user, "give me my fee breakdown")
    recorded_structure = ask(agents, db, user, "my recorded fee structure")
    official = ask(agents, db, user, "official college fee structure")
    ambiguous = ask(agents, db, user, "Can u tell me my fees structure")
    assert status["text"] == "All fees are cleared ✓"
    assert "Recorded fee items" in breakdown["text"] and "paid ₹85,000" in breakdown["text"]
    assert "Recorded fee items" in recorded_structure["text"]
    assert official["category"] == "institutional_faq"
    assert "official college documentation" in official["text"]
    assert ambiguous["category"] == "clarification"
    assert "recorded fee items" in ambiguous["text"]
    assert calls == ["get_fees", "get_fees", "get_fees"]


def test_checked_rag_definition_never_calls_general_model_or_tools(
        agents, db, monkeypatch):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    monkeypatch.setattr(llm, "chat_async", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(tools, "execute_chat", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    result = ask(agents, db, user, "What does RAG stand for?")
    assert result["text"].startswith("RAG means Retrieval-Augmented Generation.")
    assert result["source_label"] == "Deterministic answer"
    assert result["tools_used"] == []


def test_all_new_personal_reads_have_zero_mutation_flush_or_commit(
        agents, db, monkeypatch):
    _ensure_chat_scope(db)
    users = [db.query(User).filter_by(username=name).one() for name in
             ("4MT23AI001", "chat.faculty", "chat.hod", "chat.principal", "chat.admin")]
    before = _domain_snapshot(db)

    def forbidden(*args, **kwargs):
        raise AssertionError("read-only assistant attempted a write")

    for method in ("add", "add_all", "delete", "flush", "commit"):
        monkeypatch.setattr(Session, method, forbidden)
    for user in users:
        result = ask(agents, db, user, "show my profile")
        assert result["tools_used"][0]["name"] == "get_my_profile"
    ask(agents, db, users[0], "what have I paid")
    assert not db.dirty and not db.new and not db.deleted
    assert _domain_snapshot(db) == before
