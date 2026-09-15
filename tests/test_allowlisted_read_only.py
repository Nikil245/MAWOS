"""Canonical allowlisted database assistant boundary tests."""
import asyncio
import json

import pytest
from pydantic import ValidationError

from backend.app import ai_provider, llm
from backend.app.agents import tools
from backend.app.models import Parent, ParentStudent, User
from backend.app.read_only_db import (
    AllowedIntentRequest, AllowedIntentResponse, AllowedIntent,
    classify_deterministic, execute,
)


def request(intent, **parameters):
    return AllowedIntentRequest(intent=intent, parameters=parameters)


def test_allowlist_accepts_only_named_intents_and_forbids_extra_fields():
    parsed = AllowedIntentResponse.model_validate_json(
        '{"intent":"get_my_subject_attendance","parameters":{"subject":null}}')
    assert parsed.intent is AllowedIntent.get_my_subject_attendance
    with pytest.raises(ValidationError):
        AllowedIntentResponse.model_validate({
            "intent": "get_my_attendance", "parameters": {}, "sql": "select 1"})
    with pytest.raises(ValidationError):
        AllowedIntentRequest.model_validate({
            "intent": "get_my_profile", "parameters": {"role": "admin"}})
    with pytest.raises(ValidationError):
        AllowedIntentRequest.model_validate({
            "intent": "get_my_profile", "parameters": {"subject": "x"}})


def test_sql_like_parameters_are_rejected():
    for value in ("x; select * from users", "' OR 1=1 --", "postgresql://secret"):
        with pytest.raises(ValidationError):
            request("search_library_catalogue", query=value)


def test_deterministic_classifier_covers_database_surface_without_provider():
    assert classify_deterministic("show my attendance status").intent is AllowedIntent.get_my_attendance
    assert classify_deterministic("show my subject-wise attendance").intent is AllowedIntent.get_my_subject_attendance
    assert classify_deterministic("show my internal marks").intent is AllowedIntent.get_my_marks
    assert classify_deterministic("show my fee status").intent is AllowedIntent.get_my_fee_status
    assert classify_deterministic("show my profile").intent is AllowedIntent.get_my_profile
    assert classify_deterministic("will I get my hall-ticket?").intent is AllowedIntent.get_my_hall_ticket_eligibility
    assert classify_deterministic("show my timetable").intent is AllowedIntent.get_my_timetable
    assert classify_deterministic("show my placements").intent is AllowedIntent.get_my_placements
    assert classify_deterministic("search the library for Python").intent is AllowedIntent.search_library_catalogue
    assert classify_deterministic("is this book available in the library?").intent is AllowedIntent.get_library_book_availability
    assert classify_deterministic("show visible campus events").intent is AllowedIntent.get_visible_campus_events


@pytest.mark.parametrize("question", [
    "show me the companies which I am available for",
    "which companies am I eligible for?",
    "show placement drives I can apply for",
    "show my eligible placement opportunities",
    "available placement drives",
    "show my shortlisted companies",
    "show my placement status",
])
def test_placement_phrases_precede_library_availability(question):
    request_value = classify_deterministic(question)
    assert request_value is not None
    assert request_value.intent is AllowedIntent.get_my_placements


@pytest.mark.parametrize("question", [
    "show available Python books",
    "is this book available?",
    "find available AIML books",
    "search the library catalogue",
])
def test_library_availability_phrases_remain_library_intents(question):
    request_value = classify_deterministic(question)
    assert request_value is not None
    assert request_value.intent in {
        AllowedIntent.search_library_catalogue,
        AllowedIntent.get_library_book_availability,
    }


def test_student_is_bound_to_authenticated_identity(agents, db):
    own = execute(db, agents, db.query(User).filter_by(username="4MT23AI001").one(),
                  request("get_my_attendance"))
    other = execute(db, agents, db.query(User).filter_by(username="4MT23AI001").one(),
                    request("get_my_attendance", student_usn="4MT23AI002"))
    assert "error" not in own
    assert "error" in other
    assert "4MT23AI002" not in json.dumps(other)


def test_parent_is_bound_to_active_linked_child_only(agents, db):
    parent_user = User(username="allowlist.parent", password_hash="not-used",
                       role="parent", display_name="Linked Parent")
    db.add(parent_user)
    db.flush()
    parent = Parent(user_id=parent_user.id, full_name="Linked Parent", active=True)
    db.add(parent)
    db.flush()
    db.add(ParentStudent(parent_id=parent.id, student_usn="4MT23AI001",
                         relationship="Guardian", active=True))
    db.commit()
    allowed = execute(db, agents, parent_user,
                      request("get_my_attendance", child_usn="4MT23AI001"))
    allowed_placements = execute(db, agents, parent_user,
                                 request("get_my_placements", child_usn="4MT23AI001"))
    denied = execute(db, agents, parent_user,
                     request("get_my_attendance", child_usn="4MT23AI002"))
    denied_placements = execute(db, agents, parent_user,
                                request("get_my_placements", child_usn="4MT23AI002"))
    assert "error" not in allowed
    assert "error" not in allowed_placements
    assert "error" in denied
    assert "error" in denied_placements
    assert "4MT23AI002" not in json.dumps(denied)
    assert "4MT23AI002" not in json.dumps(denied_placements)


def test_deterministic_attendance_does_not_call_provider(agents, db, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("provider called for deterministic database intent")
    monkeypatch.setattr(llm, "chat_async", forbidden)
    monkeypatch.setattr(ai_provider, "_groq_request", forbidden)
    user = db.query(User).filter_by(username="4MT23AI001").one()
    result = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, user, "show my attendance status"))
    assert result["intent"] in {"attendance_query", "get_my_attendance"}
    assert result["routing"]["attempted_llm"] is False


def test_placement_query_does_not_call_provider(agents, db, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("provider called for deterministic placement intent")
    monkeypatch.setattr(llm, "chat_async", forbidden)
    monkeypatch.setattr(ai_provider, "_groq_request", forbidden)
    user = db.query(User).filter_by(username="4MT23AI001").one()
    result = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, user, "which companies am I eligible for?"))
    assert result["intent"] == "get_my_placements"
    assert result["routing"]["attempted_llm"] is False
    assert "library" not in result["text"].lower()


def test_student_cannot_query_another_student_placements(agents, db):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    result = execute(db, agents, user,
                     request("get_my_placements", student_usn="4MT23AI002"))
    assert "error" in result
    assert "4MT23AI002" not in json.dumps(result)


def test_deterministic_library_search_does_not_call_provider(agents, db, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("provider called for deterministic catalogue search")
    monkeypatch.setattr(llm, "chat_async", forbidden)
    user = db.query(User).filter_by(username="4MT23AI001").one()
    result = asyncio.run(agents["orchestrator_agent"].handle_chat(
        db, user, "search the library for Python"))
    assert result["intent"] == "search_library_catalogue"
    assert result["routing"]["attempted_llm"] is False


def test_read_only_operations_do_not_stage_database_writes(agents, db):
    user = db.query(User).filter_by(username="4MT23AI001").one()
    before = (db.query(User).count(), db.query(Parent).count(), db.query(ParentStudent).count())
    execute(db, agents, user, request("get_my_fee_status"))
    execute(db, agents, user, request("get_my_placements"))
    execute(db, agents, user, request("get_visible_campus_events", limit=2))
    after = (db.query(User).count(), db.query(Parent).count(), db.query(ParentStudent).count())
    assert after == before
    assert not db.new and not db.dirty and not db.deleted


def test_result_limits_are_bounded():
    parsed = request("search_library_catalogue", query="python", limit=50)
    assert parsed.parameters.limit == 50
    with pytest.raises(ValidationError):
        request("search_library_catalogue", query="python", limit=51)
    with pytest.raises(ValidationError):
        request("get_visible_campus_events", limit=0)
