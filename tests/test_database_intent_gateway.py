"""Strict Groq aggregate classification and fixed read-only execution tests."""
import asyncio
import json

import pytest

from backend.app import ai_provider
from backend.app.auth import hash_password
from backend.app.models import (AttendanceSummary, Department, Faculty, Student,
                                Subject, TeachingAssignment, User)
from backend.app.read_only_db import (
    DatabaseIntent, DatabaseIntentRequest, classify_database_fallback,
    execute_database_intent, validate_database_intent_response,
)


def _seed(db):
    if db.get(Department, "GATE") is None:
        db.add_all([
            Department(code="GATE", name="Gateway Engineering", intake=60),
            Department(code="SIDE", name="Other Engineering", intake=60),
            Subject(code="27GT11", name="Gateway Systems", dept_code="GATE", semester=1),
            Subject(code="27SD11", name="Other Systems", dept_code="SIDE", semester=1),
        ])
        faculty = Faculty(name="Gateway Faculty", dept_code="GATE")
        db.add(faculty)
        db.flush()
        db.add(TeachingAssignment(faculty_id=faculty.id, subject_code="27GT11",
                                  dept_code="GATE", year=1, section="A"))
        db.add_all([
            Student(usn="4MT27GT101", name="Gateway One", dept_code="GATE", year=1,
                    semester=1, section="A", cgpa=9.0, backlogs=0),
            Student(usn="4MT27GT102", name="Gateway Two", dept_code="GATE", year=1,
                    semester=1, section="A", cgpa=7.0, backlogs=0),
            Student(usn="4MT27SD101", name="Other One", dept_code="SIDE", year=1,
                    semester=1, section="A", cgpa=10.0, backlogs=0),
        ])
        db.add_all([
            AttendanceSummary(usn="4MT27GT101", subject_code="27GT11",
                              classes_held=10, classes_attended=9, percentage=90, shortage=False),
            AttendanceSummary(usn="4MT27GT102", subject_code="27GT11",
                              classes_held=10, classes_attended=5, percentage=50, shortage=True),
            AttendanceSummary(usn="4MT27SD101", subject_code="27SD11",
                              classes_held=10, classes_attended=10, percentage=100, shortage=False),
        ])
        db.add_all([
            User(username="gateway.hod", password_hash=hash_password("x"), role="hod",
                 display_name="Gateway HOD", dept_code="GATE", faculty_id=faculty.id),
            User(username="gateway.faculty", password_hash=hash_password("x"), role="faculty",
                 display_name="Gateway Faculty", dept_code="GATE", faculty_id=faculty.id),
            User(username="gateway.admin", password_hash=hash_password("x"), role="admin",
                 display_name="Gateway Admin"),
            User(username="gateway.principal", password_hash=hash_password("x"), role="principal",
                 display_name="Gateway Principal"),
            User(username="gateway.parent", password_hash=hash_password("x"), role="parent",
                 display_name="Gateway Parent"),
        ])
        db.commit()
    return {role: db.query(User).filter_by(username=f"gateway.{role}").one()
            for role in ("hod", "faculty", "admin", "principal", "parent")}


def _request(intent, **parameters):
    complete = {"department": None, "year": None, "semester": None, "subject": None}
    complete.update(parameters)
    return DatabaseIntentRequest(
        kind="database_query", intent=intent, parameters=complete)


def _ask(agents, db, user, message):
    return asyncio.run(agents["orchestrator_agent"].handle_chat(db, user, message))


def test_valid_groq_classification_executes_fixed_query_once_without_result_roundtrip(
        agents, db, monkeypatch):
    users = _seed(db)
    calls = []

    async def classify(messages, *, user_key):
        calls.append((messages, user_key))
        return ai_provider.ProviderResult(
            message={"role": "assistant", "content": json.dumps({
                "kind": "database_query",
                "intent": "get_department_average_cgpa",
                "parameters": {"department": "GATE", "year": None,
                               "semester": None, "subject": None},
            })}, provider="groq", model="test-model")

    monkeypatch.setattr(ai_provider, "classify_database_async", classify)
    result = _ask(agents, db, users["hod"], "What is the average CGPA of GATE?")

    assert result["intent"] == "get_department_average_cgpa"
    assert result["source_label"] == "Deterministic MAWOS result"
    assert result["data"]["average_cgpa"] == 8.0
    assert result["routing"]["accepted_llm"] is True
    assert len(calls) == 1
    sent = json.dumps(calls[0][0])
    assert "8.0" not in sent and "Gateway One" not in sent and "4MT27GT101" not in sent


@pytest.mark.parametrize("payload", [
    {"kind": "database_query", "intent": "unknown", "parameters": {}},
    {"kind": "database_query", "intent": "get_department_student_count",
     "parameters": {}, "extra": "no"},
    {"kind": "database_query", "intent": "get_department_student_count",
     "parameters": {"department": "GATE", "role": "admin"}},
    {"kind": "database_query", "intent": "get_department_student_count",
     "parameters": {"department": "GATE", "student_usn": "4MT27GT101"}},
    {"kind": "database_query", "intent": "get_department_student_count_by_year",
     "parameters": {"department": "GATE", "year": 9}},
    {"kind": "database_query", "intent": "get_department_average_attendance_by_year",
     "parameters": {"department": "GATE", "year": 1, "semester": 5}},
    {"kind": "database_query", "intent": "get_department_student_count",
     "parameters": {"department": "GATE"}},
])
def test_unknown_extra_identity_and_invalid_parameters_are_rejected(payload):
    with pytest.raises(ValueError):
        validate_database_intent_response(payload)


@pytest.mark.parametrize("payload", [
    "not-json",
    '{"kind":"database_query","intent":"get_department_student_count",',
    '{"kind":"database_query","intent":"get_department_student_count",'
    '"parameters":{"department":"GATE; SELECT * FROM students"}}',
    '{"kind":"database_query","intent":"get_department_student_count",'
    '"parameters":{"department":"USERS"}}',
])
def test_malformed_sql_and_internal_schema_output_is_rejected(payload):
    with pytest.raises(ValueError):
        validate_database_intent_response(payload)


def test_hod_is_bound_to_own_department_and_returns_only_aggregates(db):
    users = _seed(db)
    own = execute_database_intent(
        db, users["hod"], _request(DatabaseIntent.get_department_student_count,
                                   department="GATE"))
    foreign = execute_database_intent(
        db, users["hod"], _request(DatabaseIntent.get_department_student_count,
                                   department="SIDE"))
    assert own["student_count"] == 2
    assert foreign == {"error": "I cannot retrieve that database information through the assistant."}
    assert "usn" not in json.dumps(own).lower()


@pytest.mark.parametrize("intent,parameters,expected_key", [
    (DatabaseIntent.get_department_student_count, {}, "student_count"),
    (DatabaseIntent.get_department_student_count_by_year, {"year": 1}, "student_count"),
    (DatabaseIntent.get_department_average_attendance, {}, "average_attendance"),
    (DatabaseIntent.get_department_average_attendance_by_year, {"year": 1}, "average_attendance"),
    (DatabaseIntent.get_department_average_cgpa, {}, "average_cgpa"),
    (DatabaseIntent.get_department_attendance_risk_count, {}, "students_below_75"),
    (DatabaseIntent.get_department_subject_attendance_summary,
     {"subject": "27GT11"}, "rows"),
])
def test_hod_supported_department_intents_are_fixed_aggregate_queries(
        intent, parameters, expected_key, db):
    users = _seed(db)
    result = execute_database_intent(
        db, users["hod"], _request(intent, department="GATE", **parameters))
    assert expected_key in result
    assert "error" not in result
    assert "usn" not in json.dumps(result).lower()


@pytest.mark.parametrize("intent", [
    DatabaseIntent.get_institution_department_overview,
    DatabaseIntent.get_institution_attendance_summary,
])
def test_admin_supported_institution_intents_are_fixed_aggregate_queries(intent, db):
    users = _seed(db)
    result = execute_database_intent(db, users["admin"], _request(intent))
    assert result["rows"] and result["department_count"] >= 2
    assert "usn" not in json.dumps(result).lower()


def test_student_and_parent_cannot_request_department_or_institution_analytics(db):
    users = _seed(db)
    student = db.query(User).filter_by(username="4MT23AI001").one()
    department = _request(DatabaseIntent.get_department_average_cgpa, department="AIML")
    institution = _request(DatabaseIntent.get_institution_department_overview)
    for user in (student, users["parent"]):
        assert "error" in execute_database_intent(db, user, department)
        assert "error" in execute_database_intent(db, user, institution)


def test_faculty_is_limited_to_assigned_subject_and_class(db):
    users = _seed(db)
    allowed = execute_database_intent(
        db, users["faculty"],
        _request(DatabaseIntent.get_department_subject_attendance_summary,
                 department="GATE", year=1, subject="27GT11"))
    denied_subject = execute_database_intent(
        db, users["faculty"],
        _request(DatabaseIntent.get_department_subject_attendance_summary,
                 department="GATE", year=1, subject="27SD11"))
    denied_broad = execute_database_intent(
        db, users["faculty"],
        _request(DatabaseIntent.get_department_average_attendance, department="GATE"))
    assert allowed["students_included"] == 2
    assert allowed["average_attendance"] == 70.0
    assert "error" in denied_subject and "error" in denied_broad
    assert "usn" not in json.dumps(allowed).lower()


@pytest.mark.parametrize("role", ["principal", "admin"])
def test_principal_and_admin_receive_aggregate_only_institution_rows(role, db):
    users = _seed(db)
    result = execute_database_intent(
        db, users[role], _request(DatabaseIntent.get_institution_department_overview))
    assert result["department_count"] >= 2
    assert result["rows"]
    serialized = json.dumps(result).lower()
    assert "usn" not in serialized and "gateway one" not in serialized
    assert "error" in execute_database_intent(
        db, users[role], _request(DatabaseIntent.get_department_student_count,
                                  department="GATE"))


def test_existing_personal_record_never_calls_groq_classifier(agents, db, monkeypatch):
    _seed(db)
    student = db.query(User).filter_by(username="4MT23AI001").one()

    async def forbidden(*args, **kwargs):
        raise AssertionError("personal record reached Groq classifier")

    monkeypatch.setattr(ai_provider, "classify_database_async", forbidden)
    result = _ask(agents, db, student, "Show my attendance")
    assert result["category"] == "personal_record"
    assert result["source"] == "deterministic"


def test_unsupported_database_question_cannot_be_recast_by_groq(agents, db, monkeypatch):
    users = _seed(db)

    async def misleading(*args, **kwargs):
        return ai_provider.ProviderResult(message={"role": "assistant", "content": json.dumps({
            "kind": "database_query", "intent": "get_department_student_count",
            "parameters": {"department": "GATE", "year": None,
                           "semester": None, "subject": None},
        })}, provider="groq")

    monkeypatch.setattr(ai_provider, "classify_database_async", misleading)
    result = _ask(agents, db, users["hod"], "List every student's residential address")
    assert result["text"] == "I cannot retrieve that database information through the assistant."
    assert result["source"] == "safe_fallback"


@pytest.mark.parametrize("message", [
    "Explain sorting and email the notes to learner@example.com",
    "Explain recursion; my phone is +91 98765 43210",
    "Explain databases; I live at 12 Example Road",
    "Explain trees; my date of birth is 01/02/2004",
    "Explain queues using student 4MT27GT101",
])
def test_pii_general_questions_never_reach_groq(message, agents, db, monkeypatch):
    student = db.query(User).filter_by(username="4MT23AI001").one()

    async def forbidden(*args, **kwargs):
        raise AssertionError("PII reached a hosted provider")

    monkeypatch.setattr(ai_provider, "_groq_request", forbidden)
    result = _ask(agents, db, student, message)
    assert result["intent"] == "privacy_input_rejected"
    assert result["routing"]["attempted_llm"] is False


def test_database_intents_are_read_only_and_fallback_classifier_is_bounded(db):
    users = _seed(db)
    request = classify_database_fallback("What is the average CGPA of GATE?")
    assert request.intent is DatabaseIntent.get_department_average_cgpa
    before = (set(db.new), set(db.dirty), set(db.deleted))
    result = execute_database_intent(db, users["hod"], request)
    after = (set(db.new), set(db.dirty), set(db.deleted))
    assert result["average_cgpa"] == 8.0
    assert after == before
