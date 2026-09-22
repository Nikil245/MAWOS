"""Timetable solver + admissions pipeline + cascade + tool permissions (v2)."""
import asyncio
import datetime as dt
from collections import Counter

from backend.app.models import (
    Application, FeeRecord, Notification, Student, TimetableSlot, User,
    WorkflowEvent,
)


def test_timetable_generation_is_conflict_free(agents, db):
    before = db.query(TimetableSlot).count()
    result = agents["timetable_agent"].generate(db, "AIML")
    assert result["ok"] and result["proposal_only"] and result["unplaced"] == 0
    slots = result["proposed_slots"]
    assert db.query(TimetableSlot).count() == before
    # subject slot counts match credits (4 + 3)
    per_subject = Counter(s["subject_code"] for s in slots)
    assert per_subject["23AI51"] == 4 and per_subject["23AI52"] == 3
    # no faculty double-booking
    bookings = Counter((s["faculty_id"], s["day"], s["period"]) for s in slots)
    assert max(bookings.values()) == 1
    # CSV export renders
    csv = agents["timetable_agent"].csv_export(db, "AIML", 3, "A")
    assert "Timetable,AIML Year 3 Section A" in csv


def test_admissions_pipeline(agents, db):
    adm = agents["admission_agent"]
    v = adm.verify_all(db)
    assert v["verified"] >= 1 and v["rejected"] >= 1   # weak applicant rejected
    adm.run_merit(db)
    summary = asyncio.run(adm.allot_seats(db))
    assert summary["AIML"]["allotted"] >= 1
    app = db.query(Application).filter_by(status="seat_allotted").first()
    r = asyncio.run(adm.enrol(db, app.id))
    assert r["ok"]
    usn = r["usn"]
    assert db.get(Student, usn) is not None                      # student created
    assert db.query(User).filter_by(username=usn).first() is not None  # login
    assert db.query(FeeRecord).filter_by(usn=usn).count() == 1   # first fee
    note = db.query(Notification).filter_by(usn=usn).first()     # welcome msg
    assert note is not None and "Welcome" in note.title


def test_full_cascade_under_one_workflow(agents, db):
    records = []
    d, made = dt.date(2026, 2, 2), 0
    while made < 10:
        if d.weekday() < 5:
            records.append({"usn": "4MT23AI002", "subject_code": "23AI52",
                            "date": d.isoformat(), "present": made < 3})
            made += 1
        d += dt.timedelta(days=1)
    result = asyncio.run(agents["attendance_agent"].upload_attendance(
        db, "cascade-test", records))
    events = db.query(WorkflowEvent).filter_by(
        workflow_id=result["workflow_id"]).all()
    topics = {e.topic for e in events}
    assert {"attendance.uploaded", "attendance.updated", "exam.updated",
            "scholarship.updated", "placement.updated",
            "notification.sent"} <= topics
    assert max(e.elapsed_ms for e in events) < 2000
    note = (db.query(Notification).filter_by(usn="4MT23AI002")
              .filter(Notification.title.contains("shortage")).first())
    assert note is not None


def test_tool_permissions_lock_students_to_self(agents, db):
    from backend.app.agents import tools
    student1 = db.query(User).filter_by(username="4MT23AI001").first()
    # a student asking for another student's record still gets their OWN data
    result = tools.execute(db, agents, student1, "get_attendance",
                           {"usn": "4MT23AI002"})
    assert result.get("usn") == "4MT23AI001"
    # and role-gated tools refuse
    denied = tools.execute(db, agents, student1, "get_institution_analytics", {})
    assert "not permitted" in denied.get("error", "")
