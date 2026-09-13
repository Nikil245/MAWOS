"""Campus event lifecycle, visibility, and notification contracts."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.auth import create_token
from backend.app.campus_events import india_today
from backend.app.database import Base, get_session
from backend.app.main import app
from backend.app.models import CampusEvent, Department, Notification, User


@pytest.fixture()
def events_setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    db.add_all([
        Department(code="AIML", name="AI", intake=60),
        Department(code="CSE", name="CSE", intake=60),
    ])
    db.flush()
    users = {}
    for name, role, dept in [
        ("admin", "admin", "AIML"), ("student", "student", "AIML"),
        ("faculty", "faculty", "AIML"), ("hod", "hod", "AIML"),
        ("principal", "principal", "AIML"), ("outside", "student", "CSE"),
    ]:
        user = User(username=f"event.{name}", password_hash="test", role=role,
                    display_name=name, dept_code=dept)
        db.add(user)
        users[name] = user
    db.commit()
    app.dependency_overrides[get_session] = lambda: db
    yield db, users, TestClient(app)
    app.dependency_overrides.pop(get_session, None)
    db.close()
    engine.dispose()


def headers(user):
    return {"Authorization": f"Bearer {create_token(user)}"}


def payload(**changes):
    values = {
        "title": "Founders Day",
        "description": "Campus celebration",
        "event_date": india_today().isoformat(),
        "start_time": "10:00:00",
        "end_time": "12:00:00",
        "venue": "Main Auditorium",
        "organizer": "Student Affairs",
        "audience": "ALL",
        "department_code": None,
        "status": "DRAFT",
    }
    values.update(changes)
    return values


def test_admin_only_create_edit_publish_cancel_and_idempotent_notifications(events_setup):
    db, users, client = events_setup
    for role in ("student", "faculty", "hod", "principal"):
        assert client.post("/api/admin/campus-events", json=payload(),
                           headers=headers(users[role])).status_code == 403
    created = client.post("/api/admin/campus-events", json=payload(
        audience=" student, faculty, STUDENT ", department_code="aiml"),
        headers=headers(users["admin"]))
    assert created.status_code == 201
    event_id = created.json()["id"]
    assert created.json()["audience"] == "STUDENT,FACULTY"
    assert client.get("/api/campus-events", headers=headers(users["student"])).json()["today"] == []
    edited = client.put(f"/api/admin/campus-events/{event_id}", json={
        key: value for key, value in payload(title="Founders Day Updated",
                                             audience="STUDENT,FACULTY",
                                             department_code="AIML").items()
        if key != "status"}, headers=headers(users["admin"]))
    assert edited.status_code == 200 and edited.json()["title"] == "Founders Day Updated"
    assert client.post(f"/api/admin/campus-events/{event_id}/publish", json={},
                       headers=headers(users["admin"])).status_code == 200
    assert client.post(f"/api/admin/campus-events/{event_id}/publish", json={},
                       headers=headers(users["admin"])).status_code == 200
    published = db.query(Notification).filter_by(
        notification_type="CAMPUS_EVENT_PUBLISHED").all()
    assert {row.recipient_user_id for row in published} == {
        users["student"].id, users["faculty"].id}
    assert len(published) == 2
    visible = client.get("/api/campus-events", headers=headers(users["student"])).json()
    assert [row["id"] for row in visible["today"]] == [event_id]
    assert client.get(f"/api/campus-events/{event_id}",
                      headers=headers(users["outside"])).status_code == 404
    assert client.post(f"/api/admin/campus-events/{event_id}/cancel",
                       json={"reason": "Weather"}, headers=headers(users["admin"])).status_code == 200
    assert client.post(f"/api/admin/campus-events/{event_id}/cancel",
                       json={"reason": "Weather"}, headers=headers(users["admin"])).status_code == 200
    assert client.get("/api/campus-events", headers=headers(users["student"])).json()["today"] == []
    assert db.query(Notification).filter_by(
        notification_type="CAMPUS_EVENT_CANCELLED").count() == 2


def test_today_upcoming_past_status_and_audience_filtering(events_setup):
    db, users, client = events_setup
    today = india_today()
    rows = [
        CampusEvent(title="Past", event_date=today - dt.timedelta(days=1),
                    audience="ALL", status="PUBLISHED", created_by_user_id=users["admin"].id),
        CampusEvent(title="Today", event_date=today, start_time=dt.time(9),
                    audience="STUDENT", status="PUBLISHED", created_by_user_id=users["admin"].id),
        CampusEvent(title="Future later", event_date=today + dt.timedelta(days=3),
                    start_time=dt.time(11), audience="ALL", status="PUBLISHED",
                    created_by_user_id=users["admin"].id),
        CampusEvent(title="Future first", event_date=today + dt.timedelta(days=1),
                    start_time=dt.time(12), audience="ALL", status="PUBLISHED",
                    created_by_user_id=users["admin"].id),
        CampusEvent(title="Draft", event_date=today, audience="ALL", status="DRAFT",
                    created_by_user_id=users["admin"].id),
        CampusEvent(title="Cancelled", event_date=today, audience="ALL", status="CANCELLED",
                    created_by_user_id=users["admin"].id),
    ]
    db.add_all(rows)
    db.commit()
    student = client.get("/api/campus-events", headers=headers(users["student"])).json()
    assert [row["title"] for row in student["today"]] == ["Today"]
    assert [row["title"] for row in student["upcoming"]] == ["Future first", "Future later"]
    assert "Past" not in {row["title"] for row in student["events"]}
    faculty = client.get("/api/campus-events", headers=headers(users["faculty"])).json()
    assert faculty["today"] == []


@pytest.mark.parametrize("changes", [
    {"title": ""},
    {"end_time": "09:00:00"},
    {"audience": "ALL,STUDENT"},
    {"audience": "UNKNOWN"},
    {"department_code": "NOPE"},
])
def test_event_validation(events_setup, changes):
    _, users, client = events_setup
    response = client.post("/api/admin/campus-events", json=payload(**changes),
                           headers=headers(users["admin"]))
    assert response.status_code == 422
