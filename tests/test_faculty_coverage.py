import datetime as dt
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import ai_provider, assistant_routing
from backend.app.auth import create_token, hash_password
from backend.app.coverage import service
from backend.app.coverage.models import (AttendanceSheet, CoverageAssignment,
                                         CoverageAudit, CoverageRequest,
                                         FacultyAbsence)
from backend.app.coverage.schemas import AbsenceCreate, AttendanceSubmission
from backend.app.database import Base, get_session
from backend.app.main import app
from backend.app.models import (Department, Faculty, LibrarianAccount, Notification,
                                Parent, Student, Subject, TeachingAssignment, User)
from backend.app.timetable import models as tm
from backend.app.timetable import reads as timetable_reads


@pytest.fixture()
def coverage_world(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    today = dt.date(2030, 1, 7)  # Monday, independent of host/browser timezone.
    monkeypatch.setattr(service, "business_today", lambda: today)
    db.add_all([Department(code="CVR", name="Coverage", intake=40),
                Department(code="OTH", name="Other", intake=40)])
    db.add(Subject(code="CVR101", name="Runtime Coverage", dept_code="CVR",
                   semester=5, credits=4))
    faculty = {}
    users = {}
    for name, dept, role in [
        ("Original", "CVR", "faculty"), ("Eligible", "CVR", "faculty"),
        ("Unavailable", "CVR", "faculty"), ("Absent", "CVR", "faculty"),
        ("Overloaded", "CVR", "faculty"), ("Conflicted", "CVR", "faculty"),
        ("Unqualified", "CVR", "faculty"), ("Wrong Department", "OTH", "faculty"),
        ("Department HOD", "CVR", "hod"), ("Other HOD", "OTH", "hod"),
    ]:
        row = Faculty(name=name, dept_code=dept); db.add(row); db.flush()
        faculty[name] = row
        user = User(username=f"coverage-{name.lower().replace(' ', '-')}",
                    password_hash=hash_password("Password123"), role=role,
                    display_name=name, faculty_id=row.id, dept_code=dept)
        db.add(user); db.flush(); users[name] = user
    for name, role in [("Principal", "principal"), ("Admin", "admin"),
                       ("Student User", "student"), ("Parent User", "parent"),
                       ("Librarian User", "librarian")]:
        user = User(username=f"coverage-{role}", password_hash=hash_password("Password123"),
                    role=role, display_name=name,
                    usn="CVR001" if role == "student" else None)
        db.add(user); db.flush(); users[name] = user
    db.add(Parent(user_id=users["Parent User"].id, full_name="Coverage Parent", active=True))
    db.add(LibrarianAccount(user_id=users["Librarian User"].id, active=True))
    db.add(Student(usn="CVR001", name="Coverage Student", dept_code="CVR",
                   year=3, semester=5, section="A", cgpa=8, backlogs=0))
    assignment = TeachingAssignment(faculty_id=faculty["Original"].id,
        subject_code="CVR101", dept_code="CVR", year=3, section="A")
    conflict_assignment = TeachingAssignment(faculty_id=faculty["Conflicted"].id,
        subject_code="CVR101", dept_code="CVR", year=3, section="B")
    load_assignment = TeachingAssignment(faculty_id=faculty["Overloaded"].id,
        subject_code="CVR101", dept_code="CVR", year=3, section="C")
    db.add_all([assignment, conflict_assignment, load_assignment]); db.flush()
    term = tm.Term(name="Coverage term", starts_on=today - dt.timedelta(days=1),
                   ends_on=today + dt.timedelta(days=30)); db.add(term); db.flush()
    period = tm.PeriodDefinition(term_id=term.id, day_of_week=today.weekday(),
        period_index=0, starts_at=dt.time(9), ends_at=dt.time(10),
        is_break=False, is_closed=False)
    period_later = tm.PeriodDefinition(term_id=term.id, day_of_week=today.weekday(),
        period_index=1, starts_at=dt.time(10), ends_at=dt.time(11),
        is_break=False, is_closed=False)
    section = tm.Section(term_id=term.id, dept_code="CVR", year=3, semester=5,
                         name="A", size=40)
    section_b = tm.Section(term_id=term.id, dept_code="CVR", year=3, semester=5,
                           name="B", size=40)
    section_c = tm.Section(term_id=term.id, dept_code="CVR", year=3, semester=5,
                           name="C", size=40)
    room = tm.Room(name="CVR-101", dept_code="CVR", kind="classroom", capacity=50)
    room_b = tm.Room(name="CVR-102", dept_code="CVR", kind="classroom", capacity=50)
    room_c = tm.Room(name="CVR-103", dept_code="CVR", kind="classroom", capacity=50)
    db.add_all([period, period_later, section, section_b, section_c, room, room_b, room_c]); db.flush()
    requirement = tm.Requirement(section_id=section.id, assignment_id=assignment.id,
        periods_per_week=1, max_per_day=1, block_length=1, room_type="classroom")
    conflict_requirement = tm.Requirement(section_id=section_b.id,
        assignment_id=conflict_assignment.id, periods_per_week=1, max_per_day=1,
        block_length=1, room_type="classroom")
    load_requirement = tm.Requirement(section_id=section_c.id,
        assignment_id=load_assignment.id, periods_per_week=1, max_per_day=1,
        block_length=1, room_type="classroom")
    db.add_all([requirement, conflict_requirement, load_requirement]); db.flush()
    run = tm.Run(term_id=term.id, dept_code="CVR", status="PUBLISHED", seed=1,
        input_snapshot="{}", input_hash="coverage", metrics="{}", conflicts="[]",
        unplaced="[]", created_by=users["Admin"].id,
        published_by=users["Admin"].id)
    db.add(run); db.flush()
    entry = tm.Entry(run_id=run.id, term_id=term.id, dept_code="CVR",
        section_id=section.id, requirement_id=requirement.id, subject_code="CVR101",
        faculty_id=faculty["Original"].id, room_id=room.id, occurrence=0,
        day_of_week=today.weekday(), period_index=0)
    conflict_entry = tm.Entry(run_id=run.id, term_id=term.id, dept_code="CVR",
        section_id=section_b.id, requirement_id=conflict_requirement.id,
        subject_code="CVR101", faculty_id=faculty["Conflicted"].id,
        room_id=room_b.id, occurrence=0, day_of_week=today.weekday(), period_index=0)
    load_entry = tm.Entry(run_id=run.id, term_id=term.id, dept_code="CVR",
        section_id=section_c.id, requirement_id=load_requirement.id,
        subject_code="CVR101", faculty_id=faculty["Overloaded"].id,
        room_id=room_c.id, occurrence=0, day_of_week=today.weekday(), period_index=1)
    db.add_all([entry, conflict_entry, load_entry]); db.flush()
    for name in ["Eligible", "Unavailable", "Absent", "Overloaded", "Conflicted"]:
        db.add(tm.Qualification(faculty_id=faculty[name].id, subject_code="CVR101"))
    db.add(tm.FacultyUnavailable(faculty_id=faculty["Unavailable"].id,
                                 period_id=period.id))
    db.add(tm.FacultyLimit(faculty_id=faculty["Overloaded"].id, term_id=term.id,
                           daily_limit=1, weekly_limit=1))
    db.add(FacultyAbsence(faculty_id=faculty["Absent"].id, starts_on=today,
        ends_on=today, reason_category="PERSONAL", status="APPROVED",
        submitted_by=users["Absent"].id, reviewed_by=users["Department HOD"].id))
    db.commit()

    def override_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()
    app.dependency_overrides[get_session] = override_session
    yield {"db": db, "today": today, "faculty": faculty, "users": users,
           "term": term, "period": period, "entry": entry}
    app.dependency_overrides.pop(get_session, None)
    db.close(); engine.dispose()


def _headers(user):
    return {"Authorization": f"Bearer {create_token(user)}"}


def _approved_request(world):
    db = world["db"]; user = world["users"]["Original"]
    absence, _ = service.create_absence(db, user, AbsenceCreate(
        starts_on=world["today"], ends_on=world["today"],
        reason_category="MEDICAL", private_note="secret medical details"))
    service.submit_absence(db, user, absence["id"])
    service.review_absence(db, world["users"]["Department HOD"], absence["id"], "APPROVE")
    db.commit()
    return db.query(CoverageRequest).filter_by(absence_id=absence["id"]).one()


def _publish_coverage_snapshot(world):
    """Supply the published read-model metadata used by the personal timetable."""
    entry = world["entry"]
    run = world["db"].get(tm.Run, entry.run_id)
    run.input_snapshot = json.dumps({
        "metadata": {
            "term": {"starts_on": str(world["term"].starts_on), "ends_on": str(world["term"].ends_on)},
            "holidays": [],
            "sections": {str(entry.section_id): {"year": 3, "semester": 5, "name": "A"}},
            "subjects": {"CVR101": "Runtime Coverage"},
            "faculty": {str(world["faculty"]["Original"].id): "Original"},
            "rooms": {str(entry.room_id): "CVR-101"},
        },
        "data": {"periods": [{"day": world["today"].weekday(), "index": 0, "start": 540, "end": 600}]},
    })
    world["db"].commit()


def test_own_absence_and_role_boundaries(coverage_world):
    world = coverage_world; client = TestClient(app)
    original = world["users"]["Original"]
    response = client.post("/api/coverage/faculty/absences", headers=_headers(original), json={
        "starts_on": str(world["today"]), "ends_on": str(world["today"]),
        "reason_category": "OFFICIAL_DUTY", "private_note": "bounded private detail",
    })
    assert response.status_code == 201 and response.json()["faculty_id"] == original.faculty_id
    absence_id = response.json()["id"]
    assert client.post(f"/api/coverage/faculty/absences/{absence_id}/submit",
                       headers=_headers(original), json={}).status_code == 200
    submitted = client.get("/api/coverage/hod/absence-queue",
                           headers=_headers(world["users"]["Department HOD"]))
    assert submitted.status_code == 200
    assert [row["id"] for row in submitted.json()] == [absence_id]
    assert submitted.json()[0]["status"] == "SUBMITTED"
    assert client.get("/api/coverage/hod/absence-queue",
                      headers=_headers(world["users"]["Other HOD"])).json() == []
    assert client.post(f"/api/coverage/absences/{absence_id}/review",
        headers=_headers(world["users"]["Other HOD"]), json={"decision": "APPROVE"}).status_code == 404
    for actor in (world["users"]["Student User"], world["users"]["Parent User"],
                  world["users"]["Librarian User"], world["users"]["Principal"]):
        assert client.get("/api/coverage/faculty/absences", headers=_headers(actor)).status_code == 403


def test_queue_uses_submitter_role_not_another_account_on_the_faculty_profile(coverage_world):
    """A historical HOD account must not escalate a faculty-account submission."""
    world = coverage_world; db = world["db"]; client = TestClient(app)
    faculty = world["faculty"]["Original"]
    db.add(User(username="coverage-legacy-hod-link", password_hash=hash_password("Password123"),
                role="hod", display_name="Historical HOD link", faculty_id=faculty.id,
                dept_code="OTH"))
    db.commit()
    created = client.post("/api/coverage/faculty/absences", headers=_headers(world["users"]["Original"]), json={
        "starts_on": str(world["today"]), "ends_on": str(world["today"]),
        "reason_category": "PERSONAL"}).json()
    assert client.post(f"/api/coverage/faculty/absences/{created['id']}/submit",
                       headers=_headers(world["users"]["Original"]), json={}).status_code == 200
    queue = client.get("/api/coverage/hod/absence-queue",
                       headers=_headers(world["users"]["Department HOD"])).json()
    assert [row["id"] for row in queue] == [created["id"]]
    assert client.get("/api/coverage/escalations/absence-queue",
                      headers=_headers(world["users"]["Principal"])).json() == []


def test_hod_queue_excludes_non_submitted_and_routes_hod_absences_to_escalation(coverage_world):
    world = coverage_world; client = TestClient(app); db = world["db"]
    hod = world["users"]["Department HOD"]

    def create(user):
        response = client.post("/api/coverage/faculty/absences", headers=_headers(user), json={
            "starts_on": str(world["today"]), "ends_on": str(world["today"]),
            "reason_category": "PERSONAL"})
        assert response.status_code == 201
        return response.json()["id"]

    draft_id = create(world["users"]["Eligible"])
    rejected_id = create(world["users"]["Unavailable"])
    assert client.post(f"/api/coverage/faculty/absences/{rejected_id}/submit",
                       headers=_headers(world["users"]["Unavailable"]), json={}).status_code == 200
    assert client.post(f"/api/coverage/absences/{rejected_id}/review", headers=_headers(hod),
                       json={"decision": "REJECT"}).status_code == 200
    cancelled_id = create(world["users"]["Conflicted"])
    assert client.post(f"/api/coverage/faculty/absences/{cancelled_id}/submit",
                       headers=_headers(world["users"]["Conflicted"]), json={}).status_code == 200
    assert client.post(f"/api/coverage/faculty/absences/{cancelled_id}/cancel",
                       headers=_headers(world["users"]["Conflicted"]), json={}).status_code == 200
    hod_absence_id = create(hod)
    assert client.post(f"/api/coverage/faculty/absences/{hod_absence_id}/submit",
                       headers=_headers(hod), json={}).status_code == 200

    assert client.get("/api/coverage/hod/absence-queue", headers=_headers(hod)).json() == []
    escalated = client.get("/api/coverage/escalations/absence-queue",
                           headers=_headers(world["users"]["Principal"])).json()
    assert [row["id"] for row in escalated] == [hod_absence_id]
    assert {db.get(FacultyAbsence, row_id).status for row_id in
            (draft_id, rejected_id, cancelled_id)} == {"DRAFT", "REJECTED", "CANCELLED"}


def test_submitted_absence_without_classes_is_reviewable_without_coverage_requests(coverage_world):
    world = coverage_world; client = TestClient(app); db = world["db"]
    hod = world["users"]["Department HOD"]

    def submit(user):
        created = client.post("/api/coverage/faculty/absences", headers=_headers(user), json={
            "starts_on": str(world["today"]), "ends_on": str(world["today"]),
            "reason_category": "PERSONAL"})
        absence_id = created.json()["id"]
        assert client.post(f"/api/coverage/faculty/absences/{absence_id}/submit",
                           headers=_headers(user), json={}).status_code == 200
        return absence_id

    approve_id = submit(world["users"]["Eligible"])
    reject_id = submit(world["users"]["Unavailable"])
    queued = client.get("/api/coverage/hod/absence-queue", headers=_headers(hod)).json()
    assert {row["id"] for row in queued} == {approve_id, reject_id}
    assert client.post(f"/api/coverage/absences/{approve_id}/review", headers=_headers(hod),
                       json={"decision": "APPROVE"}).status_code == 200
    assert client.post(f"/api/coverage/absences/{reject_id}/review", headers=_headers(hod),
                       json={"decision": "REJECT"}).status_code == 200
    assert db.get(FacultyAbsence, approve_id).status == "APPROVED"
    assert db.get(FacultyAbsence, reject_id).status == "REJECTED"
    assert db.query(CoverageRequest).filter(CoverageRequest.absence_id.in_(
        (approve_id, reject_id))).count() == 0


def test_approval_generates_requests_only_for_published_affected_occurrences(coverage_world):
    world = coverage_world; db = world["db"]; user = world["users"]["Original"]
    absence, _ = service.create_absence(db, user, AbsenceCreate(
        starts_on=world["today"], ends_on=world["today"], reason_category="MEDICAL"))
    service.submit_absence(db, user, absence["id"])
    service.review_absence(db, world["users"]["Department HOD"], absence["id"], "APPROVE")
    requests = db.query(CoverageRequest).filter_by(absence_id=absence["id"]).all()
    assert [(request.timetable_entry_id, request.occurrence_date) for request in requests] == [
        (world["entry"].id, world["today"])]


def test_hod_cannot_approve_self_as_substitute(coverage_world):
    world = coverage_world; db = world["db"]; request = _approved_request(world)
    hod = world["users"]["Department HOD"]
    db.add(tm.Qualification(faculty_id=hod.faculty_id, subject_code="CVR101")); db.flush()
    with pytest.raises(HTTPException, match="own coverage"):
        service.approve_candidate(db, hod, request.id, hod.faculty_id)


def test_hod_absence_requires_principal_or_admin(coverage_world):
    world = coverage_world; client = TestClient(app); hod = world["users"]["Department HOD"]
    created = client.post("/api/coverage/faculty/absences", headers=_headers(hod), json={
        "starts_on": str(world["today"]), "ends_on": str(world["today"]),
        "reason_category": "PERSONAL",
    }).json()
    client.post(f"/api/coverage/faculty/absences/{created['id']}/submit",
                headers=_headers(hod), json={})
    assert client.post(f"/api/coverage/absences/{created['id']}/review",
        headers=_headers(world["users"]["Other HOD"]), json={"decision": "APPROVE"}).status_code == 404
    approved = client.post(f"/api/coverage/absences/{created['id']}/review",
        headers=_headers(world["users"]["Principal"]), json={"decision": "APPROVE"})
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"


def test_candidates_are_deterministic_and_exclude_every_conflict(coverage_world):
    world = coverage_world; request = _approved_request(world)
    rows = service.candidates(world["db"], world["users"]["Department HOD"],
                              request.id, mutate_status=False)
    assert [row["name"] for row in rows] == ["Eligible"]
    excluded = {"Original", "Unavailable", "Absent", "Overloaded", "Conflicted",
                "Unqualified", "Wrong Department"}
    assert excluded.isdisjoint({row["name"] for row in rows})


def test_approved_absence_exposes_all_confirmed_resolution_paths(coverage_world):
    world = coverage_world
    request = _approved_request(world)
    queued = service.coverage_queue(world["db"], world["users"]["Department HOD"])
    item = next(row for row in queued if row["id"] == request.id)
    options = {row["kind"]: row for row in item["resolution_options"]}
    assert set(options) == {"QUALIFIED_SUBSTITUTE", "REPLACEMENT_SLOT",
                            "CANCEL_AND_MAKE_UP", "HOD_DECISION_REQUIRED"}
    assert options["QUALIFIED_SUBSTITUTE"]["candidate_count"] == 1
    assert options["REPLACEMENT_SLOT"]["operation"] == {
        "action": "preview_replacement_slot", "entry_id": world["entry"].id,
        "occurrence_date": str(world["today"])}
    assert all(row["requires_confirmation"] for row in options.values())


def test_approved_substitute_and_only_that_substitute_can_mark(coverage_world):
    world = coverage_world; db = world["db"]; request = _approved_request(world)
    hod = world["users"]["Department HOD"]; substitute = world["users"]["Eligible"]
    assignment, _ = service.approve_candidate(db, hod, request.id, substitute.faculty_id)
    assert db.query(Notification).filter_by(recipient_user_id=substitute.id,
                                             notification_type="COVERAGE_ASSIGNED").count() == 1
    service.respond_assignment(db, substitute, assignment["assignment_id"], True); db.commit()
    body = AttendanceSubmission(occurrence_id=world["entry"].id, date=world["today"],
        dept="CVR", year=3, section="A", subject_code="CVR101", absent_usns=[])
    with pytest.raises(HTTPException, match="absent original"):
        service.submit_attendance(db, world["users"]["Original"], body)
    with pytest.raises(HTTPException, match="accepted approved substitute"):
        service.submit_attendance(db, world["users"]["Unqualified"], body)
    result, events = service.submit_attendance(db, substitute, body); db.commit()
    assert result["marking_mode"] == "SUBSTITUTE"
    assert any(topic == "attendance.marked_by_substitute" for topic, _ in events)
    assert db.query(CoverageAudit).filter_by(
        action="attendance.marked_by_substitute",
        timetable_entry_id=world["entry"].id,
        occurrence_date=world["today"]).count() == 1
    sheet = db.get(AttendanceSheet, result["attendance_sheet_id"])
    assert (sheet.original_faculty_id, sheet.marking_faculty_id,
            sheet.coverage_assignment_id) == (world["faculty"]["Original"].id,
                                               substitute.faculty_id,
                                               assignment["assignment_id"])
    with pytest.raises(HTTPException, match="correction workflow"):
        service.submit_attendance(db, substitute, body)


def test_accepted_coverage_is_final_in_hod_queue_and_substitute_timetable(coverage_world):
    world = coverage_world; db = world["db"]; hod = world["users"]["Department HOD"]
    substitute = world["users"]["Eligible"]; request = _approved_request(world)
    proposal, _ = service.approve_candidate(db, hod, request.id, substitute.faculty_id)
    accepted, _ = service.respond_assignment(db, substitute, proposal["assignment_id"], True)
    db.commit()

    queued = next(row for row in service.coverage_queue(db, hod) if row["id"] == request.id)
    assert queued["status"] == "APPROVED"
    assert queued["coverage_assignment_status"] == accepted["status"] == "ACCEPTED"
    assert queued["substitute_faculty"] == "Eligible"
    assert queued["accepted_at"] is not None
    assert queued["resolution_options"] == []
    timetable = service.attendance_occurrences(db, substitute)
    assert [(row["occurrence_id"], row["date"], row["marking_mode"]) for row in timetable] == [
        (world["entry"].id, world["today"], "SUBSTITUTE")]
    with pytest.raises(HTTPException, match="active coverage proposal"):
        service.approve_candidate(db, hod, request.id, substitute.faculty_id)
    with pytest.raises(HTTPException, match="Accepted coverage"):
        service.decline_request(db, hod, request.id)
    with pytest.raises(HTTPException, match="Accepted coverage"):
        service.mark_unfilled(db, hod, request.id)
    response = TestClient(app).post("/api/timetable/operations/preview", headers=_headers(hod), json={
        "action": "preview_replacement_slot", "department": "CVR",
        "entry_id": world["entry"].id, "occurrence_date": str(world["today"]),
    })
    assert response.status_code == 409


def test_expired_proposals_are_read_only_and_cannot_be_assigned_or_responded_to(coverage_world, monkeypatch):
    world = coverage_world; db = world["db"]; hod = world["users"]["Department HOD"]
    substitute = world["users"]["Eligible"]; request = _approved_request(world)
    proposal, _ = service.approve_candidate(db, hod, request.id, substitute.faculty_id)
    db.commit()

    monkeypatch.setattr(service, "business_today", lambda: world["today"] + dt.timedelta(days=1))
    listed = service.my_assignments(db, substitute)
    assert [(row["id"], row["status"], row["expired"]) for row in listed] == [
        (proposal["assignment_id"], "EXPIRED", True)]
    for accept in (True, False):
        with pytest.raises(HTTPException, match="occurrence has expired") as error:
            service.respond_assignment(db, substitute, proposal["assignment_id"], accept)
        assert error.value.status_code == 409
    with pytest.raises(HTTPException, match="occurrence has expired") as error:
        service.approve_candidate(db, hod, request.id, substitute.faculty_id)
    assert error.value.status_code == 409
    assert db.get(CoverageAssignment, proposal["assignment_id"]).status == "PROPOSED"


def test_second_accept_is_rejected_without_changing_the_accepted_assignment(coverage_world):
    world = coverage_world; db = world["db"]; hod = world["users"]["Department HOD"]
    substitute = world["users"]["Eligible"]; request = _approved_request(world)
    proposal, _ = service.approve_candidate(db, hod, request.id, substitute.faculty_id)
    accepted, _ = service.respond_assignment(db, substitute, proposal["assignment_id"], True)
    with pytest.raises(HTTPException, match="already been resolved") as error:
        service.respond_assignment(db, substitute, proposal["assignment_id"], True)
    assert error.value.status_code == 409
    assert db.get(CoverageAssignment, proposal["assignment_id"]).status == accepted["status"] == "ACCEPTED"


def test_absence_draft_without_a_published_class_returns_a_clear_non_error_hint(coverage_world):
    world = coverage_world
    draft, _ = service.create_absence(world["db"], world["users"]["Original"], AbsenceCreate(
        starts_on=world["today"] + dt.timedelta(days=1),
        ends_on=world["today"] + dt.timedelta(days=1), reason_category="PERSONAL"))
    assert draft["status"] == "DRAFT"
    assert draft["scheduled_occurrence_count"] == 0


def test_personal_timetable_exposes_accepted_coverage_as_a_dated_entry(coverage_world):
    world = coverage_world; db = world["db"]; hod = world["users"]["Department HOD"]
    substitute = world["users"]["Eligible"]; request = _approved_request(world)
    proposal, _ = service.approve_candidate(db, hod, request.id, substitute.faculty_id)
    service.respond_assignment(db, substitute, proposal["assignment_id"], True); db.commit()
    now = dt.datetime.combine(world["today"], dt.time(8), service.TZ)
    entries = timetable_reads.accepted_coverage_entries(db, substitute.faculty_id, now)
    assert len(entries) == 1  # The read model supplies a dated entry, not a weekly slot.
    assert entries[0]["dated_coverage"] is True
    assert entries[0]["subject_code"] == "CVR101"
    assert entries[0]["coverage_status"] == "ACCEPTED"


def test_personal_timetable_date_schedule_and_current_next_use_accepted_coverage_only(coverage_world):
    world = coverage_world; db = world["db"]; hod = world["users"]["Department HOD"]
    substitute = world["users"]["Eligible"]; request = _approved_request(world)
    proposal, _ = service.approve_candidate(db, hod, request.id, substitute.faculty_id)
    service.respond_assignment(db, substitute, proposal["assignment_id"], True)
    _publish_coverage_snapshot(world)

    before = timetable_reads.personal(db, substitute, now=dt.datetime.combine(world["today"], dt.time(8), service.TZ),
                                      selected_date=world["today"])
    assert [entry["id"] for entry in before["weekly"]] == []  # never a recurring assignment
    assert [(entry["dated_coverage"], entry["subject_code"], entry["original_faculty"], entry["section"])
            for entry in before["date_schedule"]] == [(True, "CVR101", "Original", "CVR 3A / semester 5")]
    assert before["current"] is None
    assert before["next"]["dated_coverage"] is True

    during = timetable_reads.personal(db, substitute, now=dt.datetime.combine(world["today"], dt.time(9, 30), service.TZ),
                                      selected_date=world["today"])
    assert during["current"]["dated_coverage"] is True
    later = timetable_reads.personal(db, substitute, now=dt.datetime.combine(world["today"] + dt.timedelta(days=1), dt.time(9, 30), service.TZ),
                                     selected_date=world["today"] + dt.timedelta(days=1))
    assert later["current"] is None


def test_pending_or_rejected_coverage_is_not_in_substitute_date_schedule(coverage_world):
    world = coverage_world; db = world["db"]; hod = world["users"]["Department HOD"]
    substitute = world["users"]["Eligible"]; request = _approved_request(world)
    proposal, _ = service.approve_candidate(db, hod, request.id, substitute.faculty_id)
    _publish_coverage_snapshot(world)
    proposed = timetable_reads.personal(db, substitute, now=dt.datetime.combine(world["today"], dt.time(8), service.TZ),
                                        selected_date=world["today"])
    assert not [entry for entry in proposed["date_schedule"] if entry.get("dated_coverage")]
    service.respond_assignment(db, substitute, proposal["assignment_id"], False)
    rejected = timetable_reads.personal(db, substitute, now=dt.datetime.combine(world["today"], dt.time(8), service.TZ),
                                        selected_date=world["today"])
    assert not [entry for entry in rejected["date_schedule"] if entry.get("dated_coverage")]


def test_acceptance_expires_pending_occurrence_change_previews(coverage_world):
    world = coverage_world; db = world["db"]; hod = world["users"]["Department HOD"]
    substitute = world["users"]["Eligible"]; request = _approved_request(world)
    preview = tm.OperationPreview(
        id="coverage-preview", correlation_id="coverage-preview-correlation",
        action="preview_replacement_slot", state="PREVIEW", actor_id=hod.id, dept_code="CVR",
        request_json=json.dumps({"entry_id": world["entry"].id,
                                 "occurrence_date": str(world["today"])}),
        preview_json="{}", before_json="{}", token_hash="x" * 64,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10))
    db.add(preview); db.commit()
    proposal, _ = service.approve_candidate(db, hod, request.id, substitute.faculty_id)
    service.respond_assignment(db, substitute, proposal["assignment_id"], True); db.commit()
    assert db.get(tm.OperationPreview, preview.id).state == "EXPIRED"
    event = db.query(tm.OperationEvent).filter_by(preview_id=preview.id, phase="EXPIRED").one()
    assert json.loads(event.after_summary) == {"reason": "accepted_coverage"}


def test_pending_preview_read_reconciles_existing_accepted_coverage(coverage_world):
    world = coverage_world; db = world["db"]; hod = world["users"]["Department HOD"]
    substitute = world["users"]["Eligible"]; request = _approved_request(world)
    proposal, _ = service.approve_candidate(db, hod, request.id, substitute.faculty_id)
    service.respond_assignment(db, substitute, proposal["assignment_id"], True); db.commit()
    preview = tm.OperationPreview(
        id="11111111-1111-1111-1111-111111111111", correlation_id="22222222-2222-2222-2222-222222222222",
        action="preview_cancel_class", state="PREVIEW", actor_id=hod.id, dept_code="CVR",
        request_json=json.dumps({"entry_id": world["entry"].id,
                                 "occurrence_date": str(world["today"])}),
        preview_json="{}", before_json="{}", token_hash="x" * 64,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10))
    db.add(preview); db.commit()
    client = TestClient(app)
    assert client.get("/api/timetable/operations/pending", headers=_headers(hod)).json() == []
    db.expire_all()
    assert db.get(tm.OperationPreview, preview.id).state == "EXPIRED"
    assert client.post("/api/timetable/operations/confirm", headers=_headers(hod), json={
        "preview_id": preview.id, "confirmation_token": "x" * 43}).status_code == 409


def test_pending_preview_with_invalid_date_is_left_unchanged(coverage_world):
    world = coverage_world; db = world["db"]; hod = world["users"]["Department HOD"]
    preview = tm.OperationPreview(
        id="33333333-3333-3333-3333-333333333333", correlation_id="44444444-4444-4444-4444-444444444444",
        action="preview_cancel_class", state="PREVIEW", actor_id=hod.id, dept_code="CVR",
        request_json=json.dumps({"entry_id": world["entry"].id, "occurrence_date": "not-a-date"}),
        preview_json="{}", before_json="{}", token_hash="x" * 64,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10))
    db.add(preview); db.commit()
    pending = TestClient(app).get("/api/timetable/operations/pending", headers=_headers(hod))
    assert pending.status_code == 200
    assert [row["preview_id"] for row in pending.json()] == [preview.id]


def test_normal_attendance_occurrence_validation_and_holiday(coverage_world):
    world = coverage_world; db = world["db"]
    body = AttendanceSubmission(occurrence_id=world["entry"].id, date=world["today"],
        dept="WRONG", year=3, section="A", subject_code="CVR101", absent_usns=[])
    with pytest.raises(HTTPException, match="do not match"):
        service.submit_attendance(db, world["users"]["Original"], body)
    db.add(tm.Holiday(term_id=world["term"].id, date=world["today"], label="Closed")); db.commit()
    valid = body.model_copy(update={"dept": "CVR"})
    with pytest.raises(HTTPException, match="holiday"):
        service.submit_attendance(db, world["users"]["Original"], valid)


def test_normal_faculty_submission_and_duplicate_are_controlled(coverage_world):
    world = coverage_world; client = TestClient(app); original = world["users"]["Original"]
    payload = {"occurrence_id": world["entry"].id, "date": str(world["today"]),
               "dept": "CVR", "year": 3, "section": "A",
               "subject_code": "CVR101", "absent_usns": []}
    first = client.post("/api/faculty/attendance", headers=_headers(original), json=payload)
    assert first.status_code == 200 and first.json()["marking_mode"] == "NORMAL"
    duplicate = client.post("/api/faculty/attendance", headers=_headers(original), json=payload)
    assert duplicate.status_code == 409
    assert "correction workflow is not available" in duplicate.json()["detail"]
    assert client.post("/api/faculty/attendance", headers=_headers(original), json={
        key: value for key, value in payload.items() if key != "occurrence_id"
    }).status_code == 422
    assert client.post("/api/faculty/attendance",
        headers=_headers(world["users"]["Admin"]), json=payload).status_code == 403


def test_no_candidates_can_be_closed_unfilled_and_audit_is_private(coverage_world):
    world = coverage_world; request = _approved_request(world); db = world["db"]
    # Make the sole eligible candidate unavailable after request creation.
    db.add(tm.FacultyUnavailable(faculty_id=world["faculty"]["Eligible"].id,
                                 period_id=world["period"].id)); db.flush()
    assert service.candidates(db, world["users"]["Department HOD"], request.id,
                              mutate_status=False) == []
    result, _ = service.mark_unfilled(db, world["users"]["Department HOD"], request.id)
    db.commit(); assert result["status"] == "UNFILLED"
    serialized = " ".join(row.detail for row in db.query(CoverageAudit).all())
    assert "secret medical details" not in serialized


def test_database_prevents_two_active_coverage_slots_for_one_faculty(coverage_world):
    world = coverage_world; db = world["db"]; first = _approved_request(world)
    approved, _ = service.approve_candidate(
        db, world["users"]["Department HOD"], first.id,
        world["faculty"]["Eligible"].id)
    db.commit()
    second_absence = FacultyAbsence(
        faculty_id=world["faculty"]["Conflicted"].id,
        starts_on=world["today"], ends_on=world["today"],
        reason_category="OFFICIAL_DUTY", status="APPROVED",
        submitted_by=world["users"]["Conflicted"].id,
        reviewed_by=world["users"]["Department HOD"].id)
    db.add(second_absence); db.flush()
    conflicting_entry = db.query(tm.Entry).filter_by(
        faculty_id=world["faculty"]["Conflicted"].id).one()
    second = CoverageRequest(absence_id=second_absence.id,
        timetable_entry_id=conflicting_entry.id, timetable_run_id=conflicting_entry.run_id,
        term_id=world["term"].id, occurrence_date=world["today"],
        original_faculty_id=world["faculty"]["Conflicted"].id,
        status="APPROVED", requested_by=world["users"]["Conflicted"].id,
        approved_by=world["users"]["Department HOD"].id)
    db.add(second); db.commit()
    db.add(CoverageAssignment(coverage_request_id=second.id,
        substitute_faculty_id=world["faculty"]["Eligible"].id,
        timetable_entry_id=conflicting_entry.id, period_id=world["period"].id,
        occurrence_date=world["today"], status="PROPOSED"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.get(CoverageAssignment, approved["assignment_id"]).status == "PROPOSED"


def test_past_absence_and_ai_mutations_are_rejected_without_provider_routing(coverage_world, monkeypatch):
    world = coverage_world
    with pytest.raises(HTTPException, match="past date"):
        service.create_absence(world["db"], world["users"]["Original"], AbsenceCreate(
            starts_on=world["today"] - dt.timedelta(days=1),
            ends_on=world["today"], reason_category="OTHER"))
    assert assistant_routing.DISALLOWED.search("approve faculty coverage and choose a substitute")
    assert assistant_routing.DISALLOWED.search("show me the coverage candidates")
    called = False
    async def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not receive operational coverage requests")
    monkeypatch.setattr(ai_provider, "_groq_request", forbidden)
    response = TestClient(app).post("/api/chat",
        headers=_headers(world["users"]["Original"]),
        json={"message": "approve faculty coverage and choose a substitute"})
    assert response.status_code == 200
    assert response.json()["category"] == "sensitive_or_disallowed"
    assert called is False
