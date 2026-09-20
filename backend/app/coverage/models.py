"""Normalized operational records for dated faculty absence and class coverage."""
from sqlalchemy import (CheckConstraint, Column, Date, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, text)

from ..database import Base
from ..models import utcnow


class FacultyAbsence(Base):
    __tablename__ = "faculty_absences"
    __table_args__ = (
        CheckConstraint("reason_category IN ('MEDICAL','OFFICIAL_DUTY','PERSONAL','OTHER')",
                        name="ck_faculty_absence_reason"),
        CheckConstraint("status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CANCELLED')",
                        name="ck_faculty_absence_status"),
        CheckConstraint("starts_on <= ends_on", name="ck_faculty_absence_dates"),
        Index("ix_faculty_absence_faculty_dates", "faculty_id", "starts_on", "ends_on"),
        Index("ix_faculty_absence_status_dates", "status", "starts_on", "ends_on"),
    )
    id = Column(Integer, primary_key=True)
    faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False)
    starts_on = Column(Date, nullable=False)
    ends_on = Column(Date, nullable=False)
    period_id = Column(Integer, ForeignKey("tt_periods.id"), nullable=True)
    reason_category = Column(String(24), nullable=False)
    private_note = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="DRAFT", server_default="DRAFT")
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class CoverageRequest(Base):
    __tablename__ = "coverage_requests"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','CANDIDATES_AVAILABLE','APPROVED','DECLINED','UNFILLED','CANCELLED')",
                        name="ck_coverage_request_status"),
        Index("uq_active_coverage_occurrence", "timetable_entry_id", "occurrence_date",
              unique=True,
              postgresql_where=text("status IN ('PENDING','CANDIDATES_AVAILABLE','APPROVED')"),
              sqlite_where=text("status IN ('PENDING','CANDIDATES_AVAILABLE','APPROVED')")),
        Index("ix_coverage_request_status_date", "status", "occurrence_date"),
    )
    id = Column(Integer, primary_key=True)
    absence_id = Column(Integer, ForeignKey("faculty_absences.id"), nullable=False)
    timetable_entry_id = Column(Integer, ForeignKey("tt_entries.id"), nullable=False)
    timetable_run_id = Column(Integer, ForeignKey("tt_runs.id"), nullable=False)
    term_id = Column(Integer, ForeignKey("tt_terms.id"), nullable=False)
    occurrence_date = Column(Date, nullable=False)
    original_faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False)
    status = Column(String(24), nullable=False, default="PENDING", server_default="PENDING")
    safe_reason = Column(String(256), nullable=True)
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class CoverageAssignment(Base):
    __tablename__ = "coverage_assignments"
    __table_args__ = (
        CheckConstraint("status IN ('PROPOSED','ACCEPTED','DECLINED','CANCELLED')",
                        name="ck_coverage_assignment_status"),
        UniqueConstraint("coverage_request_id", "substitute_faculty_id",
                         name="uq_coverage_request_candidate"),
        Index("uq_active_coverage_request_assignment", "coverage_request_id",
              unique=True,
              postgresql_where=text("status IN ('PROPOSED','ACCEPTED')"),
              sqlite_where=text("status IN ('PROPOSED','ACCEPTED')")),
        Index("uq_active_coverage_faculty_period", "substitute_faculty_id",
              "occurrence_date", "period_id", unique=True,
              postgresql_where=text("status IN ('PROPOSED','ACCEPTED')"),
              sqlite_where=text("status IN ('PROPOSED','ACCEPTED')")),
        Index("ix_coverage_assignment_candidate_status", "substitute_faculty_id", "status"),
    )
    id = Column(Integer, primary_key=True)
    coverage_request_id = Column(Integer, ForeignKey("coverage_requests.id"), nullable=False)
    substitute_faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False)
    timetable_entry_id = Column(Integer, ForeignKey("tt_entries.id"), nullable=False)
    period_id = Column(Integer, ForeignKey("tt_periods.id"), nullable=False)
    occurrence_date = Column(Date, nullable=False)
    status = Column(String(16), nullable=False, default="PROPOSED", server_default="PROPOSED")
    proposed_at = Column(DateTime, nullable=False, default=utcnow)
    responded_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)


class AttendanceSheet(Base):
    __tablename__ = "attendance_sheets"
    __table_args__ = (
        CheckConstraint("marking_mode IN ('NORMAL','SUBSTITUTE')",
                        name="ck_attendance_sheet_mode"),
        UniqueConstraint("timetable_entry_id", "occurrence_date",
                         name="uq_attendance_sheet_occurrence"),
    )
    id = Column(Integer, primary_key=True)
    timetable_entry_id = Column(Integer, ForeignKey("tt_entries.id"), nullable=False)
    timetable_run_id = Column(Integer, ForeignKey("tt_runs.id"), nullable=False)
    term_id = Column(Integer, ForeignKey("tt_terms.id"), nullable=False)
    occurrence_date = Column(Date, nullable=False)
    original_faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False)
    marking_faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False)
    coverage_assignment_id = Column(Integer, ForeignKey("coverage_assignments.id"), nullable=True)
    marking_mode = Column(String(16), nullable=False)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    submitted_at = Column(DateTime, nullable=False, default=utcnow)


class CoverageAudit(Base):
    __tablename__ = "coverage_audit"
    id = Column(Integer, primary_key=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(64), nullable=False)
    absence_id = Column(Integer, ForeignKey("faculty_absences.id"), nullable=True)
    coverage_request_id = Column(Integer, ForeignKey("coverage_requests.id"), nullable=True)
    timetable_entry_id = Column(Integer, ForeignKey("tt_entries.id"), nullable=True)
    occurrence_date = Column(Date, nullable=True)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=True)
    reason_category = Column(String(24), nullable=True)
    detail = Column(Text, nullable=False, default="{}", server_default=text("'{}'"))
    created_at = Column(DateTime, nullable=False, default=utcnow)
