"""Test fixtures v2 — isolated SQLite DB, minimal college, all agents live."""
import os
import sys
import tempfile
from pathlib import Path

_tmpdir = tempfile.mkdtemp(prefix="mawos_test_")
# Retain the supplied live URL only for the PostgreSQL integration guard.  The
# application itself is always pointed at SQLite during pytest collection, so
# imports and ordinary tests cannot connect to the populated database.
_live_database_url = os.environ.get("MAWOS_DATABASE_URL")
if _live_database_url:
    os.environ["MAWOS_LIVE_DATABASE_URL_FOR_TEST_GUARD"] = _live_database_url
os.environ["MAWOS_ENV"] = "test"
os.environ["MAWOS_JWT_SECRET"] = "mawos-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz"
os.environ["MAWOS_SEED_DEMO_DATA"] = "false"
os.environ["MAWOS_DATABASE_MODE"] = "external"
os.environ["MAWOS_DATABASE_URL"] = f"sqlite:///{Path(_tmpdir) / 'test.db'}"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import datetime as dt  # noqa: E402

import pytest  # noqa: E402

from backend.app.auth import hash_password  # noqa: E402
from backend.app.database import Base, SessionLocal, engine  # noqa: E402
from backend.app.models import (  # noqa: E402
    Application, Department, Faculty, FeeRecord, Student, Subject,
    TeachingAssignment, User,
)


@pytest.fixture(scope="session")
def agents():
    Base.metadata.create_all(bind=engine)
    from backend.app.agents import get_agents
    return get_agents()


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="session", autouse=True)
def base_data():
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    today = dt.date.today()
    s.add(Department(code="AIML", name="AI & ML", intake=2))
    s.add_all([
        Subject(code="23AI51", name="Machine Learning", dept_code="AIML",
                semester=5, credits=4),
        Subject(code="23AI52", name="DBMS", dept_code="AIML",
                semester=5, credits=3),
    ])
    fac = Faculty(name="Test Prof", dept_code="AIML")
    s.add(fac)
    s.flush()
    s.add_all([
        TeachingAssignment(faculty_id=fac.id, subject_code="23AI51",
                           dept_code="AIML", year=3, section="A"),
        TeachingAssignment(faculty_id=fac.id, subject_code="23AI52",
                           dept_code="AIML", year=3, section="A"),
    ])
    s.add_all([
        Student(usn="4MT23AI001", name="Good Student", dept_code="AIML",
                year=3, semester=5, section="A", cgpa=8.5, backlogs=0,
                family_income=300000),
        Student(usn="4MT23AI002", name="Struggling Student", dept_code="AIML",
                year=3, semester=5, section="A", cgpa=5.5, backlogs=3,
                family_income=900000),
    ])
    s.add_all([
        FeeRecord(usn="4MT23AI002", fee_type="tuition", amount_due=85000,
                  due_date=today - dt.timedelta(days=60), status="pending"),
        FeeRecord(usn="4MT23AI001", fee_type="tuition", amount_due=85000,
                  amount_paid=85000, due_date=today - dt.timedelta(days=60),
                  paid_date=today - dt.timedelta(days=61), status="paid"),
    ])
    s.add_all([
        User(username="4MT23AI001", password_hash=hash_password("x"),
             role="student", display_name="Good Student", usn="4MT23AI001",
             dept_code="AIML"),
        User(username="4MT23AI002", password_hash=hash_password("x"),
             role="student", display_name="Struggling Student",
             usn="4MT23AI002", dept_code="AIML"),
    ])
    s.add(Application(applicant_name="Bright Applicant",
                      email="a@x.com", phone="9000000001", dept_code="AIML",
                      category="GM", tenth_pct=92, twelfth_pct=90,
                      entrance_score=160, family_income=400000))
    s.add(Application(applicant_name="Weak Applicant",
                      email="b@x.com", phone="9000000002", dept_code="AIML",
                      category="GM", tenth_pct=50, twelfth_pct=40,   # below 45%
                      entrance_score=30, family_income=400000))
    s.commit()
    s.close()
