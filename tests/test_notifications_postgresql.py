"""Notification persistence checks confined to an isolated mawos_test schema."""
import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.app.database import Base
from backend.app.models import Department, Notification, Student, User
from backend.app.placement.schemas import DriveInput
from backend.app.placement.service import PlacementService
from backend.app.postgresql_safety import require_test_database_name


@pytest.fixture()
def pg_notification_db():
    configured = os.getenv("MAWOS_POSTGRES_TEST_URL")
    if not configured:
        pytest.skip("Set MAWOS_POSTGRES_TEST_URL to the isolated mawos_test database")
    url = make_url(configured)
    require_test_database_name(url.database)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("Use Psycopg 3 for notification PostgreSQL tests")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        require_test_database_name(connection.execute(text("SELECT current_database()")).scalar_one())
        connection.rollback()
        transaction = connection.begin()
        schema = "notification_test_" + uuid.uuid4().hex
        connection.execute(text(f"CREATE SCHEMA {schema}"))
        connection.execute(text(f"SET LOCAL search_path TO {schema}"))
        Base.metadata.create_all(connection)
        with Session(connection, expire_on_commit=False) as db:
            yield db, connection
        transaction.rollback()
    engine.dispose()


def test_postgresql_open_drive_recipient_scope_and_idempotency(pg_notification_db):
    db, connection = pg_notification_db
    db.add_all([
        Department(code="AIML", name="AI", intake=2),
        Department(code="CSE", name="CSE", intake=2),
    ]); db.flush()
    students = [
        Student(usn="PG-AI4", name="AI Final", dept_code="AIML", year=4, semester=8,
                section="A", cgpa=8, backlogs=0),
        Student(usn="PG-CS4", name="CSE Final", dept_code="CSE", year=4, semester=8,
                section="A", cgpa=8, backlogs=0),
        Student(usn="PG-AI3", name="AI Third", dept_code="AIML", year=3, semester=6,
                section="A", cgpa=8, backlogs=0),
    ]
    db.add_all(students); db.flush()
    for student in students:
        db.add(User(username=student.usn, password_hash="test", role="student",
                    display_name=student.name, usn=student.usn, dept_code=student.dept_code))
    db.flush()
    service = PlacementService()
    data = DriveInput(company="Oracle", role="Engineer", package_lpa=8,
                      drive_date=dt.date.today(), departments=" aiml, AIML ")
    drive = service.save_drive(db, data)
    service.save_drive(db, data.model_copy(update={"company": "Oracle edited"}), drive["id"])
    db.flush()
    notices = db.query(Notification).filter_by(notification_type="PLACEMENT_DRIVE_OPENED").all()
    assert [notice.usn for notice in notices] == ["PG-AI4"]
    assert {index["name"] for index in inspect(connection).get_indexes("notifications")} >= {
        "ix_notifications_recipient_unread_created"}
