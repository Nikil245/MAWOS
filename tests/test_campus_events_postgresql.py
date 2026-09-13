"""Campus-event PostgreSQL checks restricted to an isolated mawos_test schema."""
import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.app.campus_events import notify_publication
from backend.app.database import Base
from backend.app.models import CampusEvent, Department, Notification, User
from backend.app.postgresql_safety import require_test_database_name


def test_postgresql_event_notification_is_owned_and_deduplicated():
    configured = os.getenv("MAWOS_POSTGRES_TEST_URL")
    if not configured:
        pytest.skip("Set MAWOS_POSTGRES_TEST_URL to the isolated mawos_test database")
    url = make_url(configured)
    require_test_database_name(url.database)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("Use Psycopg 3 for campus-event PostgreSQL tests")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            require_test_database_name(connection.execute(text("SELECT current_database()")).scalar_one())
            connection.rollback()
            transaction = connection.begin()
            schema = "campus_event_test_" + uuid.uuid4().hex
            connection.execute(text(f"CREATE SCHEMA {schema}"))
            connection.execute(text(f"SET LOCAL search_path TO {schema}"))
            Base.metadata.create_all(connection)
            with Session(connection, expire_on_commit=False) as db:
                db.add(Department(code="AIML", name="AI", intake=1))
                db.flush()
                admin = User(username="event-admin", password_hash="test", role="admin",
                             display_name="Admin", dept_code="AIML")
                student = User(username="event-student", password_hash="test", role="student",
                               display_name="Student", dept_code="AIML")
                db.add_all([admin, student])
                db.flush()
                event = CampusEvent(
                    title="PostgreSQL Event", event_date=dt.date.today(),
                    audience="STUDENT", department_code="AIML", status="PUBLISHED",
                    created_by_user_id=admin.id)
                db.add(event)
                db.flush()
                assert notify_publication(db, event) == 1
                assert notify_publication(db, event) == 0
                notice = db.query(Notification).one()
                assert notice.recipient_user_id == student.id
                assert notice.route == f"/events/{event.id}"
                assert "ix_campus_events_status_date" in {
                    item["name"] for item in inspect(connection).get_indexes("campus_events")}
            transaction.rollback()
    finally:
        engine.dispose()
