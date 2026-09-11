"""ORM models v2 — the Shared Institutional Context Store for a full college:
5 departments x 4 years x 2 sections, faculty with teaching assignments,
admissions pipeline, timetables, marks, and the v1 research spine
(attendance, fees, eligibility, workflow audit)."""
import datetime as dt

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer,
    String, Text, UniqueConstraint, CheckConstraint, Index, func,
)
from sqlalchemy.orm import relationship

from .database import Base


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Department(Base):
    __tablename__ = "departments"
    code = Column(String(8), primary_key=True)        # AIML, CSE, ...
    name = Column(String(128), nullable=False)
    intake = Column(Integer, nullable=False, default=60)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), nullable=False)  # student|faculty|hod|principal|admin
    display_name = Column(String(128), nullable=False)
    usn = Column(String(16), ForeignKey("students.usn"), nullable=True)
    faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=True)
    dept_code = Column(String(8), ForeignKey("departments.code"), nullable=True)


class Student(Base):
    __tablename__ = "students"
    usn = Column(String(16), primary_key=True)         # 4MT23AM001
    name = Column(String(128), nullable=False)
    dept_code = Column(String(8), ForeignKey("departments.code"), nullable=False, index=True)
    year = Column(Integer, nullable=False)              # 1-4
    semester = Column(Integer, nullable=False)           # 1/3/5/7 (odd term)
    section = Column(String(4), nullable=False, default="A")
    cgpa = Column(Float, nullable=False)
    backlogs = Column(Integer, nullable=False, default=0)
    category = Column(String(16), nullable=False, default="GM")
    family_income = Column(Float, nullable=False, default=500000.0)
    admission_year = Column(Integer, nullable=False, default=2023)
    email = Column(String(128), nullable=True)
    phone = Column(String(16), nullable=True)
    status = Column(String(16), nullable=False, default="enrolled")
    is_synthetic = Column(Boolean, nullable=False, default=True)


class Faculty(Base):
    __tablename__ = "faculty"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    dept_code = Column(String(8), ForeignKey("departments.code"), nullable=False, index=True)
    designation = Column(String(64), nullable=False, default="Assistant Professor")
    email = Column(String(128), nullable=True)


class Subject(Base):
    __tablename__ = "subjects"
    code = Column(String(16), primary_key=True)          # 23AM51 ...
    name = Column(String(128), nullable=False)
    dept_code = Column(String(8), ForeignKey("departments.code"), nullable=False, index=True)
    semester = Column(Integer, nullable=False)
    credits = Column(Integer, nullable=False, default=4)  # = periods/week


class TeachingAssignment(Base):
    """Who teaches which subject to which section."""
    __tablename__ = "teaching_assignments"
    __table_args__ = (UniqueConstraint("subject_code", "dept_code", "year",
                                       "section", name="uq_teach"),)
    id = Column(Integer, primary_key=True)
    faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False, index=True)
    subject_code = Column(String(16), ForeignKey("subjects.code"), nullable=False)
    dept_code = Column(String(8), nullable=False)
    year = Column(Integer, nullable=False)
    section = Column(String(4), nullable=False)
    faculty = relationship("Faculty")
    subject = relationship("Subject")


class TimetableSlot(Base):
    __tablename__ = "timetable_slots"
    __table_args__ = (UniqueConstraint("dept_code", "year", "section",
                                       "day", "period", name="uq_tt_slot"),)
    id = Column(Integer, primary_key=True)
    dept_code = Column(String(8), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    section = Column(String(4), nullable=False)
    day = Column(Integer, nullable=False)      # 0=Mon .. 4=Fri
    period = Column(Integer, nullable=False)   # 0..5
    subject_code = Column(String(16), ForeignKey("subjects.code"), nullable=False)
    faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False)
    room = Column(String(16), nullable=False, default="")
    subject = relationship("Subject")
    faculty = relationship("Faculty")


class Application(Base):
    """Admissions pipeline record."""
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True)
    applicant_name = Column(String(128), nullable=False)
    email = Column(String(128), nullable=False)
    phone = Column(String(16), nullable=False)
    dept_code = Column(String(8), ForeignKey("departments.code"), nullable=False, index=True)
    category = Column(String(16), nullable=False, default="GM")
    tenth_pct = Column(Float, nullable=False)
    twelfth_pct = Column(Float, nullable=False)
    entrance_score = Column(Float, nullable=False)   # 0-200 (CET-style)
    family_income = Column(Float, nullable=False)
    # submitted | verified | merit_listed | seat_allotted | enrolled | rejected
    status = Column(String(16), nullable=False, default="submitted", index=True)
    merit_score = Column(Float, nullable=True)
    merit_rank = Column(Integer, nullable=True)
    allotted_usn = Column(String(16), nullable=True)
    notes = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class MarksRecord(Base):
    __tablename__ = "marks_records"
    __table_args__ = (UniqueConstraint("usn", "subject_code", "internal",
                                       name="uq_marks"),)
    id = Column(Integer, primary_key=True)
    usn = Column(String(16), ForeignKey("students.usn"), nullable=False, index=True)
    subject_code = Column(String(16), ForeignKey("subjects.code"), nullable=False)
    internal = Column(Integer, nullable=False)         # 1..3
    marks = Column(Float, nullable=False)
    max_marks = Column(Float, nullable=False, default=50.0)
    entered_by = Column(String(64), nullable=False, default="")


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (UniqueConstraint("usn", "subject_code", "date",
                                       name="uq_attendance_entry"),)
    id = Column(Integer, primary_key=True)
    usn = Column(String(16), ForeignKey("students.usn"), nullable=False, index=True)
    subject_code = Column(String(16), ForeignKey("subjects.code"), nullable=False)
    date = Column(Date, nullable=False)
    present = Column(Boolean, nullable=False)
    uploaded_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=utcnow)


class AttendanceSummary(Base):
    __tablename__ = "attendance_summary"
    __table_args__ = (UniqueConstraint("usn", "subject_code",
                                       name="uq_attendance_summary"),)
    id = Column(Integer, primary_key=True)
    usn = Column(String(16), ForeignKey("students.usn"), nullable=False, index=True)
    subject_code = Column(String(16), ForeignKey("subjects.code"), nullable=False)
    classes_held = Column(Integer, nullable=False, default=0)
    classes_attended = Column(Integer, nullable=False, default=0)
    percentage = Column(Float, nullable=False, default=0.0)
    shortage = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class FeeRecord(Base):
    __tablename__ = "fee_records"
    id = Column(Integer, primary_key=True)
    usn = Column(String(16), ForeignKey("students.usn"), nullable=False, index=True)
    fee_type = Column(String(32), nullable=False)
    amount_due = Column(Float, nullable=False)
    amount_paid = Column(Float, nullable=False, default=0.0)
    due_date = Column(Date, nullable=False)
    paid_date = Column(Date, nullable=True)
    fine = Column(Float, nullable=False, default=0.0)
    status = Column(String(16), nullable=False, default="pending")


class ExamSchedule(Base):
    __tablename__ = "exam_schedules"
    id = Column(Integer, primary_key=True)
    subject_code = Column(String(16), ForeignKey("subjects.code"), nullable=False)
    dept_code = Column(String(8), nullable=False, index=True)
    semester = Column(Integer, nullable=False)
    exam_date = Column(Date, nullable=False)
    session = Column(String(16), nullable=False, default="FN")


class HallTicket(Base):
    __tablename__ = "hall_tickets"
    __table_args__ = (UniqueConstraint("usn", "semester", name="uq_hall_ticket"),)
    id = Column(Integer, primary_key=True)
    usn = Column(String(16), ForeignKey("students.usn"), nullable=False, index=True)
    semester = Column(Integer, nullable=False)
    eligible = Column(Boolean, nullable=False)
    reasons = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ScholarshipAssessment(Base):
    __tablename__ = "scholarship_assessments"
    __table_args__ = (UniqueConstraint("usn", "scheme", name="uq_scholarship_scheme"),)
    id = Column(Integer, primary_key=True)
    usn = Column(String(16), ForeignKey("students.usn"), nullable=False, index=True)
    scheme = Column(String(64), nullable=False, default="Merit-cum-Means")
    status = Column(String(16), nullable=False)
    ml_score = Column(Float, nullable=True)
    reasons = Column(Text, nullable=False, default="")
    assessed_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    # Legacy rows use ``scheme``. Workflow rows are keyed by a published
    # scholarship and retain the criteria revision that produced the verdict.
    scholarship_id = Column(Integer, ForeignKey("scholarships.id"), nullable=True, index=True)
    eligibility_status = Column(String(24), nullable=True)
    reason_codes = Column(Text, nullable=False, default="[]", server_default="[]")
    criteria_version = Column(Integer, nullable=True)


class Scholarship(Base):
    __tablename__ = "scholarships"
    id = Column(Integer, primary_key=True)
    name = Column(String(160), nullable=False)
    provider = Column(String(160), nullable=False)
    description = Column(Text, nullable=False, default="", server_default="")
    amount = Column(Float, nullable=False)
    application_url = Column(String(512), nullable=False)
    opens_at = Column(DateTime, nullable=False)
    closes_at = Column(DateTime, nullable=False)
    status = Column(String(24), nullable=False, default="DRAFT", server_default="DRAFT", index=True)
    created_by_faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False, index=True)
    department_code = Column(String(8), ForeignKey("departments.code"), nullable=False, index=True)
    approved_by_hod_id = Column(Integer, ForeignKey("faculty.id"), nullable=True)
    approval_comment = Column(Text, nullable=False, default="", server_default="")
    rejection_reason = Column(Text, nullable=False, default="", server_default="")
    official_document_reference = Column(String(512), nullable=False, default="", server_default="")
    criteria_version = Column(Integer, nullable=False, default=1, server_default="1")
    # JSON text keeps this schema portable to SQLite unit tests and PostgreSQL.
    criteria = Column(Text, nullable=False, default="{}", server_default="{}")
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    creator = relationship("Faculty", foreign_keys=[created_by_faculty_id])


class ScholarshipApplication(Base):
    __tablename__ = "scholarship_applications"
    __table_args__ = (UniqueConstraint("scholarship_id", "student_usn",
                                       name="uq_scholarship_application"),)
    id = Column(Integer, primary_key=True)
    scholarship_id = Column(Integer, ForeignKey("scholarships.id"), nullable=False, index=True)
    student_usn = Column(String(16), ForeignKey("students.usn"), nullable=False, index=True)
    application_status = Column(String(24), nullable=False, default="SUBMITTED", server_default="SUBMITTED")
    applied_at = Column(DateTime, default=utcnow, nullable=False)
    external_reference = Column(String(256), nullable=False, default="", server_default="")
    verified_by = Column(Integer, ForeignKey("faculty.id"), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PlacementDrive(Base):
    __tablename__ = "placement_drives"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','OPEN','SHORTLIST_GENERATED','CLOSED','CANCELLED')", name="ck_placement_drive_status"),
        Index("ix_placement_drive_status_date", "status", "drive_date"),
    )
    id = Column(Integer, primary_key=True)
    company = Column(String(128), nullable=False)
    role = Column(String(128), nullable=False)
    package_lpa = Column(Float, nullable=False)
    min_cgpa = Column(Float, nullable=False, default=6.0)
    max_backlogs = Column(Integer, nullable=False, default=0)
    min_attendance = Column(Float, nullable=False, default=75.0)
    drive_date = Column(Date, nullable=False)
    departments = Column(String(64), nullable=False, default="ALL")  # csv of codes
    status = Column(String(24), nullable=False, default="OPEN", server_default="OPEN")
    requires_fee_clearance = Column(Boolean, nullable=False, default=False, server_default="false")
    application_deadline = Column(Date, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now())


class PlacementShortlist(Base):
    __tablename__ = "placement_shortlists"
    __table_args__ = (UniqueConstraint("drive_id", "usn", name="uq_shortlist_entry"),)
    id = Column(Integer, primary_key=True)
    drive_id = Column(Integer, ForeignKey("placement_drives.id"), nullable=False)
    usn = Column(String(16), ForeignKey("students.usn"), nullable=False, index=True)
    eligible = Column(Boolean, nullable=False)
    ml_probability = Column(Float, nullable=True)
    model_version = Column(String(16), nullable=True)
    reasons = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    drive = relationship("PlacementDrive")


class PlacementOutcome(Base):
    __tablename__ = "placement_outcomes"
    __table_args__ = (
        UniqueConstraint("drive_id", "usn", name="uq_placement_outcome"),
        CheckConstraint("outcome_status IN ('OFFER_MADE','OFFER_ACCEPTED','OFFER_DECLINED','REJECTED')", name="ck_placement_outcome_status"),
        Index("ix_placement_outcome_usn_status", "usn", "outcome_status"),
    )
    id = Column(Integer, primary_key=True)
    drive_id = Column(Integer, ForeignKey("placement_drives.id"), nullable=False)
    usn = Column(String(16), ForeignKey("students.usn"), nullable=False)
    outcome_status = Column(String(24), nullable=False)
    package_offered = Column(Float, nullable=True)
    decided_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    usn = Column(String(16), nullable=True, index=True)
    audience_role = Column(String(16), nullable=True)
    dept_code = Column(String(8), nullable=True)
    channel = Column(String(16), nullable=False, default="in-app")
    title = Column(String(256), nullable=False)
    message = Column(Text, nullable=False)
    source_agent = Column(String(32), nullable=False)
    created_at = Column(DateTime, default=utcnow)
    read = Column(Boolean, nullable=False, default=False)


class WorkflowEvent(Base):
    """Audit log of every bus event — powers propagation metrics + trace UI."""
    __tablename__ = "workflow_events"
    id = Column(Integer, primary_key=True)
    workflow_id = Column(String(36), nullable=False, index=True)
    topic = Column(String(64), nullable=False)
    agent = Column(String(32), nullable=False)
    hop = Column(Integer, nullable=False, default=0)
    payload = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=utcnow)
    elapsed_ms = Column(Float, nullable=False, default=0.0)


class IntentLog(Base):
    """Every orchestrator decision — routing accuracy / LLM-vs-fallback metrics."""
    __tablename__ = "intent_logs"
    id = Column(Integer, primary_key=True)
    query = Column(Text, nullable=False)
    predicted_intent = Column(String(64), nullable=False)   # tool name in LLM mode
    method = Column(String(16), nullable=False)              # llm | keyword
    latency_ms = Column(Float, nullable=False, default=0.0)
    expected_intent = Column(String(64), nullable=True)
    correct = Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=utcnow)

# Register the narrowly scoped timetable version/configuration tables.
from .timetable import models as timetable_models  # noqa: E402,F401
