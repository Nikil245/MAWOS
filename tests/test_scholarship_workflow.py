import datetime as dt

import pytest
from fastapi import HTTPException

from backend.app import scholarships
from backend.app.models import (Faculty, Notification, Scholarship, ScholarshipApplication,
                                ScholarshipAssessment, Student, WorkflowEvent)
from backend.app.auth import create_token, hash_password
from backend.app.database import get_session
from backend.app.main import app
from backend.app.models import Department, User


def make_scholarship(db, **overrides):
    faculty = db.query(Faculty).first()
    values = dict(name="Merit Grant", provider="Foundation", amount=10000,
                  application_url="https://example.org/apply", opens_at=dt.datetime.now() - dt.timedelta(days=1),
                  closes_at=dt.datetime.now() + dt.timedelta(days=10), department_code="AIML",
                  created_by_faculty_id=faculty.id, status="PUBLISHED", published_at=dt.datetime.now(),
                  criteria='{"minimum_cgpa":7.0,"maximum_backlogs":0,"fee_clearance_required":true}')
    values.update(overrides); row = Scholarship(**values); db.add(row); db.commit(); return row


def test_deterministic_eligibility_and_missing_information(db):
    row = make_scholarship(db)
    good = db.get(Student, "4MT23AI001")
    result = scholarships.evaluate(db, row, good)
    assert result.eligibility_status == "ELIGIBLE"
    assert result.reason_codes == "[]"
    row.criteria = '{"minimum_attendance":75}'
    unknown_attendance = Student(usn="4MT23AI999", name="No Attendance", dept_code="AIML",
                                 year=3, semester=5, section="A", cgpa=8.0,
                                 backlogs=0, family_income=300000)
    db.add(unknown_attendance); db.flush()
    result = scholarships.evaluate(db, row, unknown_attendance)
    assert result.eligibility_status == "UNABLE_TO_DETERMINE"
    assert "MISSING_ATTENDANCE" in result.reason_codes


def test_lifecycle_rejects_invalid_transition(db):
    row = make_scholarship(db, status="DRAFT")
    with pytest.raises(HTTPException):
        scholarships._transition(row, "PUBLISHED")
    scholarships._transition(row, "PENDING_APPROVAL")
    scholarships._transition(row, "CHANGES_REQUESTED")
    scholarships._transition(row, "DRAFT")


def test_result_and_application_are_student_scoped(db):
    row = make_scholarship(db)
    good = db.get(Student, "4MT23AI001")
    assessment = scholarships.evaluate(db, row, good)
    db.add(ScholarshipApplication(scholarship_id=row.id, student_usn=good.usn))
    db.commit()
    assert db.query(ScholarshipAssessment).filter_by(scholarship_id=row.id, usn=good.usn).one().id == assessment.id
    with pytest.raises(Exception):
        db.add(ScholarshipApplication(scholarship_id=row.id, student_usn=good.usn)); db.commit()
    db.rollback()


def test_api_authorization_lifecycle_and_rollback(db):
    """Role and ownership checks are server-derived; rejected writes rollback."""
    from fastapi.testclient import TestClient
    faculty = db.query(Faculty).first()
    other = Faculty(name="Other", dept_code="AIML"); hod = Faculty(name="HOD", dept_code="AIML")
    db.add_all([other, hod]); db.flush()
    creator = User(username="sch.fac", password_hash=hash_password("x"), role="faculty", display_name="Faculty", faculty_id=faculty.id, dept_code="AIML")
    outsider = User(username="sch.other", password_hash=hash_password("x"), role="faculty", display_name="Other", faculty_id=other.id, dept_code="AIML")
    hod_user = User(username="sch.hod", password_hash=hash_password("x"), role="hod", display_name="HOD", faculty_id=hod.id, dept_code="AIML")
    db.add_all([creator, outsider, hod_user]); db.commit()
    app.dependency_overrides[get_session] = lambda: db
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_token(creator)}"}
    now = dt.datetime.now(); payload = {"name":"API Grant","provider":"Trust","description":"","amount":1,"application_url":"https://example.org","opens_at":(now + dt.timedelta(days=1)).isoformat(),"closes_at":(now + dt.timedelta(days=2)).isoformat(),"department_code":"AIML","criteria":{}}
    try:
        before = db.query(Scholarship).count()
        bad = {**payload, "application_url": "javascript:bad"}
        assert client.post("/api/faculty/scholarships", json=bad, headers=headers).status_code == 422
        assert db.query(Scholarship).count() == before
        created = client.post("/api/faculty/scholarships", json=payload, headers=headers)
        assert created.status_code == 201; scholarship_id = created.json()["id"]
        assert client.post(f"/api/faculty/scholarships/{scholarship_id}/submit", headers=headers).status_code == 200
        other_headers = {"Authorization": f"Bearer {create_token(outsider)}"}
        assert client.put(f"/api/faculty/scholarships/{scholarship_id}", json=payload, headers=other_headers).status_code == 403
        hod_headers = {"Authorization": f"Bearer {create_token(hod_user)}"}
        assert client.post(f"/api/hod/scholarships/{scholarship_id}/request-changes", json={"comment":"Add source"}, headers=hod_headers).status_code == 200
        assert client.put(f"/api/faculty/scholarships/{scholarship_id}", json=payload, headers=headers).status_code == 200
        assert client.post(f"/api/faculty/scholarships/{scholarship_id}/submit", headers=headers).status_code == 200
        assert client.post(f"/api/hod/scholarships/{scholarship_id}/approve", json={"comment":""}, headers=hod_headers).status_code == 200
        assert client.put(f"/api/faculty/scholarships/{scholarship_id}", json=payload, headers=headers).status_code == 409
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_hod_approval_long_title_serializes_and_refreshes(db):
    """Regression for PostgreSQL's legacy varchar(64) assessment scheme."""
    from fastapi.testclient import TestClient
    faculty = db.query(Faculty).first()
    hod_faculty = Faculty(name="Approval HOD", dept_code="AIML")
    hod = User(username="approval.hod", password_hash=hash_password("x"), role="hod",
               display_name="Approval HOD", faculty_id=None, dept_code="AIML")
    db.add_all([hod_faculty, hod]); db.flush(); hod.faculty_id = hod_faculty.id
    title = "SBI Platinum Jubilee Asha Scholarship for Undergraduate Students 2026"
    row = Scholarship(name=title, provider="Foundation", description="", amount=1,
                      application_url="https://example.org", opens_at=dt.datetime.now() - dt.timedelta(days=1),
                      closes_at=dt.datetime.now() + dt.timedelta(days=2), department_code="AIML",
                      created_by_faculty_id=faculty.id, status="PENDING_APPROVAL", criteria="{}")
    db.add(row); db.commit()
    app.dependency_overrides[get_session] = lambda: db
    try:
        client = TestClient(app); headers = {"Authorization": f"Bearer {create_token(hod)}"}
        approved = client.post(f"/api/hod/scholarships/{row.id}/approve", json={"comment": ""}, headers=headers)
        assert approved.status_code == 200
        assert approved.json()["status"] == "PUBLISHED"
        assert approved.json()["impact"]["total_applicable_students"] >= 1
        assert db.get(Scholarship, row.id).published_at is not None
        assessments = db.query(ScholarshipAssessment).filter_by(scholarship_id=row.id).all()
        assert assessments and all(len(a.scheme) <= 64 for a in assessments)
        assert db.query(Notification).filter(Notification.source_agent == "scholarship_service").count() >= 1
        assert db.query(WorkflowEvent).filter(WorkflowEvent.topic.in_(["scholarship.approve", "scholarship.publish"])).count() >= 2
        refreshed = client.get("/api/hod/scholarships", headers=headers)
        assert refreshed.status_code == 200
        assert any(item["id"] == row.id for item in refreshed.json()["scholarships"])
        assert client.post(f"/api/hod/scholarships/{row.id}/approve", json={}, headers=headers).status_code == 409
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_hod_approval_updates_existing_assessment_and_handles_missing_data(db):
    from fastapi.testclient import TestClient
    faculty = db.query(Faculty).first(); hod_faculty = Faculty(name="Assessment HOD", dept_code="AIML")
    db.add(hod_faculty); db.flush()
    hod = User(username="assessment.hod", password_hash=hash_password("x"), role="hod", display_name="HOD", faculty_id=hod_faculty.id, dept_code="AIML")
    row = Scholarship(name="Attendance Grant", provider="Foundation", description="", amount=1,
                      application_url="https://example.org", opens_at=dt.datetime.now() - dt.timedelta(days=1), closes_at=dt.datetime.now() + dt.timedelta(days=2), department_code="AIML", created_by_faculty_id=faculty.id, status="PENDING_APPROVAL", criteria='{"minimum_attendance":75}')
    db.add_all([hod, row]); db.flush()
    student = Student(usn="4MT23AI998", name="No Attendance", dept_code="AIML",
                      year=3, semester=5, section="A", cgpa=8.0, backlogs=0,
                      family_income=300000)
    db.add(student); db.flush()
    existing = ScholarshipAssessment(scholarship_id=row.id, usn=student.usn, scheme="legacy-unique-key", status="ELIGIBLE", criteria_version=1)
    db.add(existing); db.commit(); app.dependency_overrides[get_session] = lambda: db
    try:
        response = TestClient(app).post(f"/api/hod/scholarships/{row.id}/approve", json={}, headers={"Authorization": f"Bearer {create_token(hod)}"})
        assert response.status_code == 200
        assessment = db.query(ScholarshipAssessment).filter_by(scholarship_id=row.id, usn=student.usn).one()
        assert assessment.id == existing.id
        assert assessment.eligibility_status == "UNABLE_TO_DETERMINE"
        assert assessment.status == "UNDETERMINED"
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.parametrize("failure_target", ["evaluate_applicable", "_notify"])
def test_hod_approval_rolls_back_on_evaluation_or_notification_failure(db, monkeypatch, failure_target):
    from fastapi.testclient import TestClient
    faculty = db.query(Faculty).first(); hod_faculty = Faculty(name=f"Failure HOD {failure_target}", dept_code="AIML")
    db.add(hod_faculty); db.flush()
    hod = User(username=f"failure.hod.{failure_target}", password_hash=hash_password("x"), role="hod", display_name="HOD", faculty_id=hod_faculty.id, dept_code="AIML")
    row = Scholarship(name=f"Failure {failure_target}", provider="Foundation", description="", amount=1, application_url="https://example.org", opens_at=dt.datetime.now() - dt.timedelta(days=1), closes_at=dt.datetime.now() + dt.timedelta(days=2), department_code="AIML", created_by_faculty_id=faculty.id, status="PENDING_APPROVAL", criteria="{}")
    db.add_all([hod, row]); db.commit(); app.dependency_overrides[get_session] = lambda: db
    def fail(*args, **kwargs): raise RuntimeError("injected failure")
    monkeypatch.setattr(scholarships, failure_target, fail)
    try:
        response = TestClient(app, raise_server_exceptions=False).post(f"/api/hod/scholarships/{row.id}/approve", json={}, headers={"Authorization": f"Bearer {create_token(hod)}"})
        assert response.status_code == 500
        assert response.json()["detail"] == "Scholarship workflow could not be completed; no changes were saved."
        db.expire_all(); persisted = db.get(Scholarship, row.id)
        assert persisted.status == "PENDING_APPROVAL" and persisted.published_at is None
        assert db.query(ScholarshipAssessment).filter_by(scholarship_id=row.id).count() == 0
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_wrong_department_hod_cannot_approve(db):
    from fastapi.testclient import TestClient
    faculty = db.query(Faculty).first(); outside = Faculty(name="Outside HOD", dept_code="CSE")
    db.add(outside); db.flush()
    hod = User(username="outside.hod", password_hash=hash_password("x"), role="hod", display_name="Outside", faculty_id=outside.id, dept_code="CSE")
    row = Scholarship(name="Department Grant", provider="Foundation", description="", amount=1, application_url="https://example.org", opens_at=dt.datetime.now() - dt.timedelta(days=1), closes_at=dt.datetime.now() + dt.timedelta(days=2), department_code="AIML", created_by_faculty_id=faculty.id, status="PENDING_APPROVAL", criteria="{}")
    db.add_all([hod, row]); db.commit(); app.dependency_overrides[get_session] = lambda: db
    try:
        response = TestClient(app).post(f"/api/hod/scholarships/{row.id}/approve", json={}, headers={"Authorization": f"Bearer {create_token(hod)}"})
        assert response.status_code == 403
        assert db.get(Scholarship, row.id).status == "PENDING_APPROVAL"
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_student_application_creates_owned_notification(db):
    from fastapi.testclient import TestClient
    student_user = (db.query(User).filter_by(role="student", usn="4MT23AI001")
                    .order_by(User.id).first())
    row = make_scholarship(db, name="Application Notice Grant")
    scholarships.evaluate(db, row, db.get(Student, student_user.usn)); db.commit()
    app.dependency_overrides[get_session] = lambda: db
    try:
        response = TestClient(app).post(
            f"/api/student/scholarships/{row.id}/apply", json={"external_reference": "REF-1"},
            headers={"Authorization": f"Bearer {create_token(student_user)}"})
        assert response.status_code == 201
        notice = db.query(Notification).filter_by(
            recipient_user_id=student_user.id, notification_type="SCHOLARSHIP_APPLICATION").one()
        assert notice.route == "/student/scholarships"
        assert notice.related_entity_id == str(row.id)
    finally:
        app.dependency_overrides.pop(get_session, None)
