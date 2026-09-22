"""Deterministic, role-scoped academic analytics assistant tests."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app import ai_provider, llm
from backend.app.auth import hash_password
from backend.app.models import (AttendanceSummary, Department, Faculty, MarksRecord,
                                Student, Subject, User)
from backend.app.read_only_db import (AllowedIntent, AllowedIntentRequest,
                                      academic_year_semesters, classify_deterministic)


def _seed(db):
    if db.get(Department, "ANLY") is None:
        db.add_all([
            Department(code="ANLY", name="Analytics", intake=60),
            Department(code="OTHR", name="Other Department", intake=60),
        ])
        db.add_all([
            Subject(code="26AN11", name="Foundations I", dept_code="ANLY", semester=1),
            Subject(code="26AN21", name="Foundations II", dept_code="ANLY", semester=2),
            Subject(code="26AN31", name="Second Year", dept_code="ANLY", semester=3),
            Subject(code="26OT11", name="Foreign Foundations", dept_code="OTHR", semester=1),
        ])
        db.add_all([
            Student(usn="4MT26AN101", name="First One", dept_code="ANLY", year=1,
                    semester=1, section="A", cgpa=8, backlogs=0),
            Student(usn="4MT26AN102", name="First Two", dept_code="ANLY", year=1,
                    semester=2, section="A", cgpa=8, backlogs=0),
            Student(usn="4MT25AN201", name="Second Year", dept_code="ANLY", year=2,
                    semester=3, section="A", cgpa=8, backlogs=0),
            Student(usn="4MT26OT101", name="Foreign First", dept_code="OTHR", year=1,
                    semester=1, section="A", cgpa=8, backlogs=0),
        ])
        db.add_all([
            AttendanceSummary(usn="4MT26AN101", subject_code="26AN11",
                              classes_held=10, classes_attended=8, percentage=80, shortage=False),
            AttendanceSummary(usn="4MT26AN102", subject_code="26AN21",
                              classes_held=10, classes_attended=6, percentage=60, shortage=True),
            AttendanceSummary(usn="4MT25AN201", subject_code="26AN31",
                              classes_held=10, classes_attended=10, percentage=100, shortage=False),
            AttendanceSummary(usn="4MT26OT101", subject_code="26OT11",
                              classes_held=10, classes_attended=10, percentage=100, shortage=False),
            MarksRecord(usn="4MT26AN101", subject_code="26AN11", internal=1,
                        marks=40, max_marks=50, entered_by="test"),
            MarksRecord(usn="4MT26AN102", subject_code="26AN21", internal=1,
                        marks=30, max_marks=50, entered_by="test"),
        ])
        faculty = Faculty(name="Analytics Faculty", dept_code="ANLY")
        db.add(faculty)
        db.flush()
        db.add_all([
            User(username="analytics.hod", password_hash=hash_password("x"), role="hod",
                 display_name="Analytics HOD", dept_code="ANLY", faculty_id=faculty.id),
            User(username="analytics.admin", password_hash=hash_password("x"), role="admin",
                 display_name="Analytics Admin"),
            User(username="analytics.principal", password_hash=hash_password("x"), role="principal",
                 display_name="Analytics Principal"),
            User(username="analytics.parent", password_hash=hash_password("x"), role="parent",
                 display_name="Analytics Parent"),
        ])
        db.commit()
    return {role: db.query(User).filter_by(username=f"analytics.{role}").one()
            for role in ("hod", "admin", "principal", "parent")}


def _forbid_providers(monkeypatch):
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("deterministic analytics reached an AI provider")

    monkeypatch.setattr(llm, "chat_async", forbidden)
    monkeypatch.setattr(llm, "general_chat_async", forbidden)
    monkeypatch.setattr(ai_provider, "_groq_request", forbidden)
    return calls


def _ask(agents, db, user, question):
    return asyncio.run(agents["orchestrator_agent"].handle_chat(db, user, question))


def test_hod_first_year_average_is_deterministic_and_maps_semesters_one_and_two(
        agents, db, monkeypatch):
    users = _seed(db)
    calls = _forbid_providers(monkeypatch)

    result = _ask(agents, db, users["hod"], "what is the average attendance of first year")

    assert calls == []
    assert result["intent"] == "get_department_average_attendance"
    assert result["source"] == "deterministic"
    assert result["routing"]["attempted_llm"] is False
    assert result["data"]["semesters"] == [1, 2]
    assert result["data"]["students_included"] == 2
    assert result["data"]["average_attendance"] == 70.0
    assert result["data"]["students_below_75"] == 1
    assert result["text"] == "The average attendance for first-year ANLY students is 70.0%."
    metrics = next(block for block in result["blocks"] if block["type"] == "metric_cards")
    assert [card["label"] for card in metrics["cards"]] == [
        "Department", "Students included", "Average attendance", "Students below 75%"]
    assert {row["semester"] for block in result["blocks"] if block["type"] == "table"
            for row in block["rows"]} == {"1", "2"}
    assert result["suggestions"] == [
        "Show attendance risk by semester", "Show student count in my department",
        "Show average marks for first year"]


def test_hod_scope_comes_from_account_and_query_department_cannot_override(
        agents, db, monkeypatch):
    users = _seed(db)
    _forbid_providers(monkeypatch)

    result = _ask(agents, db, users["hod"],
                  "what is the average attendance of first-year OTHR students")

    assert result["source"] == "safe_fallback"
    assert result["category"] == "sensitive_or_disallowed"
    serialized = json.dumps(result)
    assert "4MT26AN101" not in serialized
    assert "4MT26OT101" not in serialized


@pytest.mark.parametrize("role", ["admin", "principal"])
def test_admin_and_principal_can_request_explicit_institution_overview(
        role, agents, db, monkeypatch):
    users = _seed(db)
    _forbid_providers(monkeypatch)

    result = _ask(agents, db, users[role], "show institution-wide academic overview")

    assert result["intent"] == "get_institution_overview"
    assert result["source"] == "deterministic"
    assert result["data"]["department_count"] >= 2
    assert any(row["department_code"] == "ANLY" for row in result["data"]["rows"])


@pytest.mark.parametrize("role", ["student", "parent"])
def test_student_and_parent_department_aggregates_are_denied_without_provider(
        role, agents, db, monkeypatch):
    users = _seed(db)
    calls = _forbid_providers(monkeypatch)
    user = (db.query(User).filter_by(username="4MT23AI001").one()
            if role == "student" else users["parent"])

    result = _ask(agents, db, user, "show department attendance summary")

    assert calls == []
    assert result["source"] == "safe_fallback"
    assert result["category"] == "unsupported"
    assert result["routing"]["attempted_llm"] is False
    assert result["blocks"][0]["type"] == "status_notice"


def test_empty_analytics_is_a_deterministic_empty_state(agents, db, monkeypatch):
    users = _seed(db)
    _forbid_providers(monkeypatch)

    result = _ask(agents, db, users["hod"], "average attendance for fourth year")

    assert result["source"] == "deterministic"
    assert result["fallback"] is False
    assert result["blocks"] == [{
        "type": "empty_state", "title": "No attendance data",
        "message": "No attendance records matched the authorized department and academic filters."}]


@pytest.mark.parametrize("question,intent", [
    ("how many students are in my department", "get_department_student_count"),
    ("how many faculty are in my department", "get_department_faculty_count"),
    ("show attendance risk by semester", "get_department_attendance_risk_summary"),
    ("show department attendance summary", "get_department_average_attendance"),
    ("show average marks for first year", "get_department_marks_summary"),
    ("show department overview", "get_department_overview"),
])
def test_hod_analytics_operations_execute_locally(question, intent, agents, db, monkeypatch):
    users = _seed(db)
    calls = _forbid_providers(monkeypatch)

    result = _ask(agents, db, users["hod"], question)

    assert calls == []
    assert result["intent"] == intent
    assert result["source"] == "deterministic"
    assert result["routing"]["attempted_llm"] is False


def test_all_allowlisted_analytics_phrases_classify_locally():
    expected = {
        "how many students are in my department": AllowedIntent.get_department_student_count,
        "how many faculty are in my department": AllowedIntent.get_department_faculty_count,
        "show attendance risk by semester": AllowedIntent.get_department_attendance_risk_summary,
        "show department attendance summary": AllowedIntent.get_department_average_attendance,
        "show average marks for first year": AllowedIntent.get_department_marks_summary,
        "show department overview": AllowedIntent.get_department_overview,
        "show institution-wide academic overview": AllowedIntent.get_institution_overview,
    }
    for question, intent in expected.items():
        assert classify_deterministic(question).intent is intent
    assert academic_year_semesters(1) == (1, 2)
    assert academic_year_semesters(2) == (3, 4)
    assert academic_year_semesters(3) == (5, 6)
    assert academic_year_semesters(4) == (7, 8)


def test_admin_department_strength_is_a_deterministic_aggregate_with_trace_metadata(
        agents, db, monkeypatch):
    users = _seed(db)
    calls = _forbid_providers(monkeypatch)

    result = _ask(agents, db, users["admin"], "Can you show me the total strength of AIML department?")

    assert calls == []
    assert result["intent"] == "get_department_student_count"
    assert result["source"] == "deterministic"
    assert result["data"]["department_code"] == "AIML"
    assert result["data"]["student_count"] == 2
    assert result["text"] == "AIML currently has 2 active students."
    assert result["safe_trace"]["steps"][0]["detail"] == "Department Strength query"


def test_hod_department_strength_is_limited_to_their_department(agents, db, monkeypatch):
    users = _seed(db)
    _forbid_providers(monkeypatch)

    own = _ask(agents, db, users["hod"], "How many students are in ANLY?")
    foreign = _ask(agents, db, users["hod"], "What is the total strength of OTHR department?")

    assert own["source"] == "deterministic"
    assert own["data"]["department_code"] == "ANLY"
    assert own["data"]["student_count"] == 3
    assert foreign["source"] == "safe_fallback"
    assert foreign["category"] == "sensitive_or_disallowed"
    assert "own authorized department" in foreign["text"]


@pytest.mark.parametrize("role", ["faculty", "student", "librarian"])
def test_department_strength_is_denied_outside_authorized_roles(agents, db, monkeypatch, role):
    _seed(db)
    _forbid_providers(monkeypatch)
    user = SimpleNamespace(role=role, dept_code="AIML", usn="4MT23AI001", display_name="Scoped user")

    result = _ask(agents, db, user, "AIML student count")

    assert result["source"] == "safe_fallback"
    assert result["category"] == "unsupported"
    assert "2 active students" not in json.dumps(result)


def test_unknown_department_strength_returns_valid_codes_without_provider(agents, db, monkeypatch):
    users = _seed(db)
    _forbid_providers(monkeypatch)

    result = _ask(agents, db, users["admin"], "How many students are in UNKNOWN?")

    assert result["source"] == "safe_fallback"
    assert result["category"] == "sensitive_or_disallowed"
    assert "Unknown department" in result["text"]
    assert "AIML" in result["text"]


def test_normal_admin_general_question_keeps_the_existing_provider_fallback(agents, db, monkeypatch):
    users = _seed(db)

    async def unavailable(*args, **kwargs):
        return llm.OllamaResult(error_code="missing_key")

    monkeypatch.setattr(llm, "general_chat_async", unavailable)
    result = _ask(agents, db, users["admin"], "How should administrators welcome new visitors?")

    assert result["category"] == "general_ai"
    assert result["fallback"] is True
    assert result["fallback_code"] == "missing_key"


def test_analytics_parameters_reject_unknown_enums_and_invalid_year_semester_pairs():
    base = {"intent": "get_department_average_attendance"}
    for parameters in (
        {"academic_year": 5},
        {"semester": 9},
        {"academic_year": 1, "semester": 3},
        {"group_by": "student"},
        {"metric": "sql"},
        {"department_code": "AIML;DROP"},
    ):
        with pytest.raises(ValidationError):
            AllowedIntentRequest.model_validate({**base, "parameters": parameters})
