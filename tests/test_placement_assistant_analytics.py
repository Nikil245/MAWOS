"""Regression coverage for placement-specific assistant routing and scoping."""
import asyncio
import datetime as dt
import json

import pytest

from backend.app import ai_provider, read_only_db
from backend.app.models import (
    Department, Parent, ParentStudent, PlacementDrive, PlacementOutcome,
    PlacementShortlist, Student, User,
)
from backend.app.read_only_db import (
    DatabaseIntent, DatabaseIntentRequest, classify_database_fallback,
    execute_database_intent,
)


def _request(intent, department=None):
    return DatabaseIntentRequest(kind="database_query", intent=intent, parameters={
        "department": department, "year": None, "semester": None, "subject": None,
    })


def _seed(db):
    if db.get(Department, "PLAC") is None:
        db.add_all([
            Department(code="PLAC", name="Placement Engineering", intake=60),
            Department(code="EMPTY", name="Empty Placement Engineering", intake=60),
        ])
        db.add_all([
            Student(usn="PLACEMENT001", name="Placement One", dept_code="PLAC", year=4,
                    semester=7, section="A", cgpa=8.5, backlogs=0),
            Student(usn="PLACEMENT002", name="Placement Two", dept_code="PLAC", year=4,
                    semester=7, section="A", cgpa=8.0, backlogs=0),
            Student(usn="PLACEMENT003", name="Placement Three", dept_code="PLAC", year=4,
                    semester=7, section="A", cgpa=7.5, backlogs=0),
        ])
        today = dt.date.today()
        first = PlacementDrive(company="Alpha Corp", role="Engineer", package_lpa=8,
                               min_cgpa=6, max_backlogs=0, min_attendance=75,
                               drive_date=today, departments="PLAC", status="CLOSED")
        second = PlacementDrive(company="Beta Corp", role="Analyst", package_lpa=10,
                                min_cgpa=6, max_backlogs=0, min_attendance=75,
                                drive_date=today, departments="PLAC", status="CLOSED")
        db.add_all([first, second])
        db.flush()
        db.add_all([
            PlacementShortlist(drive_id=first.id, usn="PLACEMENT001", eligible=True, reasons=""),
            PlacementShortlist(drive_id=first.id, usn="PLACEMENT002", eligible=True, reasons=""),
            PlacementOutcome(drive_id=first.id, usn="PLACEMENT001", outcome_status="OFFER_MADE"),
            PlacementOutcome(drive_id=second.id, usn="PLACEMENT001", outcome_status="OFFER_ACCEPTED"),
            PlacementOutcome(drive_id=second.id, usn="PLACEMENT002", outcome_status="REJECTED"),
        ])
        users = [
            User(username="placement.student", password_hash="unused", role="student",
                 display_name="Placement One", usn="PLACEMENT001", dept_code="PLAC"),
            User(username="placement.other", password_hash="unused", role="student",
                 display_name="Placement Two", usn="PLACEMENT002", dept_code="PLAC"),
            User(username="placement.hod", password_hash="unused", role="hod",
                 display_name="Placement HOD", dept_code="PLAC"),
            User(username="placement.faculty", password_hash="unused", role="faculty",
                 display_name="Placement Faculty", dept_code="PLAC"),
            User(username="placement.librarian", password_hash="unused", role="librarian",
                 display_name="Placement Librarian"),
            User(username="placement.admin", password_hash="unused", role="admin",
                 display_name="Placement Admin"),
            User(username="placement.principal", password_hash="unused", role="principal",
                 display_name="Placement Principal"),
            User(username="placement.parent", password_hash="unused", role="parent",
                 display_name="Placement Parent"),
        ]
        db.add_all(users)
        db.flush()
        parent = Parent(user_id=next(row.id for row in users if row.role == "parent"),
                        full_name="Placement Parent", active=True)
        db.add(parent)
        db.flush()
        db.add(ParentStudent(parent_id=parent.id, student_usn="PLACEMENT001",
                             relationship="Guardian", active=True))
        db.commit()
    return {role: db.query(User).filter_by(username=f"placement.{role}").one()
            for role in ("student", "other", "hod", "faculty", "librarian",
                         "admin", "principal", "parent")}


def _ask(agents, db, user, message):
    return asyncio.run(agents["orchestrator_agent"].handle_chat(db, user, message))


@pytest.mark.parametrize("message,expected", [
    ("How many students are placed in PLAC department?",
     DatabaseIntent.get_department_placed_student_count),
    ("How many PLAC students received an offer?", DatabaseIntent.get_department_offer_count),
    ("Show PLAC placement summary.", DatabaseIntent.get_department_placement_summary),
    ("Which companies recruited PLAC students?", DatabaseIntent.get_department_placement_summary),
])
def test_placement_wording_precedes_generic_student_count(message, expected):
    request = classify_database_fallback(message)
    assert request is not None
    assert request.intent is expected
    assert request.intent is not DatabaseIntent.get_department_student_count


def test_hod_gets_only_own_department_aggregate_and_offer_semantics_are_distinct(db):
    users = _seed(db)
    result = execute_database_intent(
        db, users["hod"], _request(DatabaseIntent.get_department_placement_summary, "PLAC"))
    assert result["student_count"] == 3
    assert result["placement_eligible_students"] == 2
    assert result["students_with_confirmed_offers"] == 1
    assert result["total_offers"] == 2
    assert result["students_without_offer"] == 2
    assert "PLACEMENT001" not in json.dumps(result)
    assert "error" in execute_database_intent(
        db, users["hod"], _request(DatabaseIntent.get_department_placement_summary, "EMPTY"))


def test_misclassified_generic_intent_is_rejected_and_fallback_routes_placement(
        agents, db, monkeypatch):
    users = _seed(db)

    async def misleading(*args, **kwargs):
        return ai_provider.ProviderResult(provider="groq", message={"content": json.dumps({
            "kind": "database_query", "intent": "get_department_student_count",
            "parameters": {"department": "PLAC", "year": None,
                           "semester": None, "subject": None},
        })})

    monkeypatch.setattr(ai_provider, "classify_database_async", misleading)
    result = _ask(agents, db, users["hod"],
                  "How many students are placed in PLAC department?")
    assert result["intent"] == "get_department_placed_student_count"
    assert result["data"]["students_with_confirmed_offers"] == 1
    assert result["data"]["student_count"] == 3
    assert result["source_label"] == "Deterministic MAWOS result"


def test_personal_and_linked_child_offer_summaries_never_call_provider(
        agents, db, monkeypatch):
    users = _seed(db)

    async def forbidden(*args, **kwargs):
        raise AssertionError("personal placement data reached a provider")

    monkeypatch.setattr(ai_provider, "classify_database_async", forbidden)
    monkeypatch.setattr(ai_provider, "_groq_request", forbidden)
    own = _ask(agents, db, users["student"], "Do I have any offer letters?")
    child = _ask(agents, db, users["parent"], "Does my child have any offer letters?")
    assert own["intent"] == "get_my_placement_summary"
    assert child["intent"] == "get_linked_child_placement_summary"
    assert own["data"]["confirmed_offer_count"] == 2
    assert child["data"]["confirmed_offer_count"] == 2
    assert {row["company"] for row in own["data"]["offers"]} == {"Alpha Corp", "Beta Corp"}
    assert "PLACEMENT002" not in json.dumps(own, default=str)


@pytest.mark.parametrize("role", ["faculty", "librarian"])
def test_unauthorized_roles_are_denied_placement_analytics(role, db):
    users = _seed(db)
    result = execute_database_intent(
        db, users[role], _request(DatabaseIntent.get_department_placement_summary, "PLAC"))
    assert result == {"error": "I cannot retrieve that database information through the assistant."}


@pytest.mark.parametrize("role", ["principal", "admin"])
def test_principal_and_admin_receive_aggregate_only_placement_results(role, db):
    users = _seed(db)
    result = execute_database_intent(
        db, users[role], _request(DatabaseIntent.get_department_placement_summary, "PLAC"))
    assert result["total_offers"] == 2
    serialized = json.dumps(result)
    assert "PLACEMENT001" not in serialized and "Placement One" not in serialized


def test_zero_records_are_distinct_from_unavailable_offer_storage(db, monkeypatch):
    users = _seed(db)
    empty = execute_database_intent(
        db, users["admin"], _request(DatabaseIntent.get_department_placement_summary, "EMPTY"))
    assert empty["offer_data_available"] is True
    assert empty["placement_records_available"] is False
    assert empty["total_offers"] == 0

    actual = read_only_db._placement_schema_capabilities(db)
    monkeypatch.setattr(read_only_db, "_placement_schema_capabilities", lambda _db: {
        key: value for key, value in actual.items() if key != "placement_outcomes"})
    unavailable = execute_database_intent(
        db, users["admin"], _request(DatabaseIntent.get_department_placement_summary, "EMPTY"))
    assert unavailable["offer_data_available"] is False
    assert unavailable["total_offers"] is None


def test_provider_receives_only_question_and_database_execution_is_read_only(
        agents, db, monkeypatch):
    users = _seed(db)
    sent = []

    async def classify(messages, *, user_key):
        sent.append(messages)
        return ai_provider.ProviderResult(provider="groq", message={"content": json.dumps({
            "kind": "database_query", "intent": "get_department_placement_summary",
            "parameters": {"department": "PLAC", "year": None,
                           "semester": None, "subject": None},
        })})

    monkeypatch.setattr(ai_provider, "classify_database_async", classify)
    before = (set(db.new), set(db.dirty), set(db.deleted))
    result = _ask(agents, db, users["hod"], "Show PLAC placement summary")
    after = (set(db.new), set(db.dirty), set(db.deleted))
    assert result["data"]["total_offers"] == 2
    assert after == before
    provider_input = json.dumps(sent)
    assert "Alpha Corp" not in provider_input
    assert "PLACEMENT001" not in provider_input
    assert "OFFER_ACCEPTED" not in provider_input
