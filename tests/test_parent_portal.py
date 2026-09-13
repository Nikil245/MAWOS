import datetime as dt
import uuid

from fastapi.testclient import TestClient

from backend.app.auth import create_token, hash_password
from backend.app.main import app
from backend.app.models import Department, Notification, Parent, ParentStudent, Student, User


client = TestClient(app)


def headers(user):
    return {"Authorization": f"Bearer {create_token(user)}"}


def admin_user(db):
    user = User(username=f"parent.admin.{uuid.uuid4().hex[:8]}",
                password_hash=hash_password("Admin-password-1"), role="admin",
                display_name="Parent Admin")
    db.add(user); db.commit(); db.refresh(user)
    return user


def create_parent(db, admin, *, username, usns, password="Temporary-1!",
                  relationship="Guardian"):
    response = client.post("/api/admin/parents", headers=headers(admin), json={
        "full_name": f"Parent {username}", "username": username,
        "email": None, "mobile": "9000000000", "temporary_password": password,
        "students": [{"student_usn": usn.lower(), "relationship": relationship,
                      "is_primary": index == 0} for index, usn in enumerate(usns)],
    })
    assert response.status_code == 201, response.text
    return response.json()


def login_and_change(username, password="Temporary-1!"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "parent"
    assert response.json()["user"]["must_change_password"] is True
    auth = {"Authorization": f"Bearer {response.json()['token']}"}
    assert client.get("/api/parent/profile", headers=auth).status_code == 403
    assert client.post("/api/auth/change-password", headers=auth, json={
        "current_password": "wrong", "new_password": "Private-parent-2!"}).status_code == 400
    changed = client.post("/api/auth/change-password", headers=auth, json={
        "current_password": password, "new_password": "Private-parent-2!"})
    assert changed.status_code == 200
    return auth


def test_admin_only_creation_first_login_and_no_secret_leak(db):
    admin = admin_user(db)
    student = db.query(User).filter_by(role="student").first()
    denied = client.post("/api/admin/parents", headers=headers(student), json={})
    assert denied.status_code == 403

    username = f"guardian.{uuid.uuid4().hex[:8]}"
    created = create_parent(db, admin, username=username, usns=["4MT23AI001", "4MT23AI002"])
    assert created["generated_credentials"] is None
    assert "password_hash" not in str(created)
    account = db.query(User).filter_by(username=username).one()
    assert account.password_hash != "Temporary-1!"
    auth = login_and_change(username)
    profile = client.get("/api/parent/profile", headers=auth)
    assert profile.status_code == 200
    assert {row["usn"] for row in profile.json()["children"]} == {"4MT23AI001", "4MT23AI002"}
    db.expire_all()
    assert db.query(User).filter_by(username=username).one().must_change_password is False
    assert client.get("/api/student/dashboard", headers=auth).status_code == 403
    assert client.get("/api/metrics/summary", headers=auth).status_code == 403


def test_link_authorization_many_to_many_and_immediate_deactivation(db):
    admin = admin_user(db)
    first_name = f"multi.a.{uuid.uuid4().hex[:8]}"
    second_name = f"multi.b.{uuid.uuid4().hex[:8]}"
    first = create_parent(db, admin, username=first_name, usns=["4MT23AI001", "4MT23AI002"])
    second = create_parent(db, admin, username=second_name, usns=["4MT23AI001"])
    first_auth, second_auth = login_and_change(first_name), login_and_change(second_name)

    assert client.get("/api/parent/children/4mt23ai001/dashboard", headers=first_auth).status_code == 200
    assert client.get("/api/parent/children/4MT23AI002/dashboard", headers=first_auth).status_code == 200
    assert client.get("/api/parent/children/4MT23AI001/dashboard", headers=second_auth).status_code == 200
    forbidden = client.get("/api/parent/children/4MT23AI002/dashboard", headers=second_auth)
    assert forbidden.status_code == 403
    assert "Struggling Student" not in forbidden.text

    duplicate = client.post(f"/api/admin/parents/{first['id']}/students", headers=headers(admin), json={
        "student_usn": "4MT23AI001", "relationship": "Guardian", "is_primary": False})
    assert duplicate.status_code == 409

    mapping = next(row for row in first["students"] if row["usn"] == "4MT23AI002")
    disabled = client.patch(
        f"/api/admin/parents/{first['id']}/students/{mapping['mapping_id']}/active",
        headers=headers(admin), json={"active": False})
    assert disabled.status_code == 200
    assert client.get("/api/parent/children/4MT23AI002/dashboard", headers=first_auth).status_code == 403

    assert client.patch(f"/api/admin/parents/{second['id']}/active", headers=headers(admin),
                        json={"active": False}).status_code == 200
    assert client.get("/api/parent/profile", headers=second_auth).status_code == 403
    assert client.post("/api/auth/login", json={"username": second_name,
                       "password": "Private-parent-2!"}).status_code == 403


def test_generated_credentials_once_and_parent_event_notification_is_scoped(db):
    admin = admin_user(db)
    if db.get(Department, "CSE") is None:
        db.add(Department(code="CSE", name="Computer Science", intake=1))
        db.flush()
    if db.get(Student, "4MT23CS099") is None:
        db.add(Student(usn="4MT23CS099", name="CSE Student", dept_code="CSE",
                       year=3, semester=5, section="A", cgpa=8, backlogs=0))
    db.commit()
    username = f"event.parent.{uuid.uuid4().hex[:8]}"
    response = client.post("/api/admin/parents", headers=headers(admin), json={
        "full_name": "Event Parent", "username": username, "email": None, "mobile": None,
        "temporary_password": None,
        "students": [{"student_usn": "4MT23AI001", "relationship": "Mother", "is_primary": True}],
    })
    assert response.status_code == 201
    credentials = response.json()["generated_credentials"]
    assert credentials["username"] == username and len(credentials["temporary_password"]) >= 10
    listed = client.get("/api/admin/parents", headers=headers(admin)).json()
    assert "temporary_password" not in str(listed)
    parent_user = db.query(User).filter_by(username=username).one()
    other_name = f"event.cse.{uuid.uuid4().hex[:8]}"
    create_parent(db, admin, username=other_name, usns=["4MT23CS099"])
    other_user = db.query(User).filter_by(username=other_name).one()

    event = client.post("/api/admin/campus-events", headers=headers(admin), json={
        "title": "Parent AIML briefing", "description": "Read-only notice",
        "event_date": str(dt.date.today() + dt.timedelta(days=2)),
        "audience": "PARENT", "department_code": "AIML", "status": "PUBLISHED",
    })
    assert event.status_code == 201, event.text
    event_id = event.json()["id"]
    rows = db.query(Notification).filter_by(
        recipient_user_id=parent_user.id, event_key=f"campus_event_published:{event_id}").all()
    assert len(rows) == 1
    assert rows[0].route == f"/parent/events/{event_id}"
    assert db.query(Notification).filter_by(
        recipient_user_id=other_user.id,
        event_key=f"campus_event_published:{event_id}").count() == 0
    assert client.post(f"/api/admin/campus-events/{event_id}/publish",
                       headers=headers(admin)).status_code == 200
    assert db.query(Notification).filter_by(
        recipient_user_id=parent_user.id,
        event_key=f"campus_event_published:{event_id}").count() == 1
