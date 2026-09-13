from fastapi.testclient import TestClient

from backend.app.auth import create_token
from backend.app.database import get_session
from backend.app.main import app
from backend.app.models import Notification, User


def test_student_notification_read_state_is_scoped_and_persistent(db):
    student = db.query(User).filter_by(role="student", usn="4MT23AI001").order_by(User.id).first()
    other = db.query(User).filter_by(role="student", usn="4MT23AI002").order_by(User.id).first()
    mine = Notification(recipient_user_id=student.id, usn=student.usn, title="Mine", message="Read me", source_agent="test")
    other_note = Notification(recipient_user_id=other.id, usn=other.usn, title="Other", message="Private", source_agent="test")
    db.add_all([mine, other_note]); db.commit()
    app.dependency_overrides[get_session] = lambda: db
    try:
        client = TestClient(app); headers = {"Authorization": f"Bearer {create_token(student)}"}
        listed = client.get("/api/notifications", headers=headers)
        assert listed.status_code == 200 and listed.json()["unread_count"] >= 1
        assert client.patch(f"/api/notifications/{mine.id}/read", headers=headers).json() == {"id": mine.id, "read": True}
        db.expire_all(); assert db.get(Notification, mine.id).read is True and db.get(Notification, mine.id).read_at is not None
        assert client.patch(f"/api/notifications/{other_note.id}/read", headers=headers).status_code == 404
        assert db.get(Notification, other_note.id).read is False
        assert client.post("/api/notifications/read-all", headers=headers).status_code == 200
        assert client.get("/api/notifications", headers=headers).json()["unread_count"] == 0
    finally:
        app.dependency_overrides.pop(get_session, None)
