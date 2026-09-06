"""Notification list authorization is enforced from the authenticated user."""
import datetime as dt

from fastapi.testclient import TestClient

from backend.app.auth import create_token, hash_password
from backend.app.main import app
from backend.app.models import Department, Notification, User


def _headers(user):
    return {"Authorization": f"Bearer {create_token(user)}"}


def _user(db, username, role, *, usn=None, dept="AIML"):
    user = User(username=username, password_hash=hash_password("x"), role=role,
                display_name=username, usn=usn, dept_code=dept)
    db.add(user)
    db.commit()
    return user


def _notice(db, title, **target):
    note = Notification(title=title, message=f"{title} message",
                        source_agent="notification_agent", **target)
    db.add(note)
    db.commit()
    return note


def test_notification_list_rejects_unauthenticated_requests():
    assert TestClient(app).get("/api/notifications").status_code == 401


def test_personal_notifications_are_visible_only_to_the_authenticated_student(db):
    intended = _user(db, "notice.intended", "student", usn="4MT23AI001")
    other = _user(db, "notice.other", "student", usn="4MT23AI002")
    _notice(db, "Personal for intended", usn="4MT23AI001")

    intended_response = TestClient(app).get("/api/notifications", headers=_headers(intended))
    other_response = TestClient(app).get("/api/notifications", headers=_headers(other))

    assert intended_response.status_code == 200
    assert "Personal for intended" in {item["title"] for item in intended_response.json()["notifications"]}
    assert "Personal for intended" not in {item["title"] for item in other_response.json()["notifications"]}


def test_role_and_department_visibility_uses_server_side_user_identity(db):
    if db.get(Department, "CSE") is None:
        db.add(Department(code="CSE", name="Computer Science", intake=2))
        db.commit()
    aiml_faculty = _user(db, "notice.aiml.faculty", "faculty", dept="AIML")
    cse_faculty = _user(db, "notice.cse.faculty", "faculty", dept="CSE")
    admin = _user(db, "notice.admin", "admin", dept="AIML")
    _notice(db, "AIML faculty", audience_role="faculty", dept_code="AIML")
    _notice(db, "CSE faculty", audience_role="faculty", dept_code="CSE")
    _notice(db, "All faculty", audience_role="faculty")
    _notice(db, "Admin only", audience_role="admin")

    client = TestClient(app)
    aiml = client.get("/api/notifications?usn=4MT23AI002&role=admin&dept_code=CSE", headers=_headers(aiml_faculty))
    cse = client.get("/api/notifications", headers=_headers(cse_faculty))
    admin_response = client.get("/api/notifications", headers=_headers(admin))

    assert {item["title"] for item in aiml.json()["notifications"]} == {"AIML faculty", "All faculty"}
    assert {item["title"] for item in cse.json()["notifications"]} == {"CSE faculty", "All faculty"}
    assert {item["title"] for item in admin_response.json()["notifications"]} == {"Admin only"}


def test_notification_get_is_read_only_and_returns_explicit_fields(db):
    user = _user(db, "notice.readonly", "student", usn="4MT23AI001")
    note = _notice(db, "Unread notice", usn="4MT23AI001", read=False)
    note.created_at = dt.datetime(2026, 1, 2, 3, 4, 5)
    db.commit()

    response = TestClient(app).get("/api/notifications", headers=_headers(user))
    db.refresh(note)

    assert response.status_code == 200
    payload = response.json()
    assert payload["unread_count"] == sum(not item["read"] for item in payload["notifications"])
    item = next(item for item in payload["notifications"] if item["id"] == note.id)
    assert {"id", "title", "message", "source_agent", "at", "read"} <= item.keys()
    assert note.read is False
