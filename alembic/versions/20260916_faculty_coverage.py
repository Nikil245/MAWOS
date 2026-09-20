"""Faculty absence, runtime coverage, and occurrence-bound attendance.

Revision ID: 20260916_faculty_coverage
Revises: 20260915_library
"""
from alembic import op
import sqlalchemy as sa

revision = "20260916_faculty_coverage"
down_revision = "20260915_library"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("faculty_absences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("faculty_id", sa.Integer(), sa.ForeignKey("faculty.id"), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("period_id", sa.Integer(), sa.ForeignKey("tt_periods.id")),
        sa.Column("reason_category", sa.String(24), nullable=False),
        sa.Column("private_note", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("submitted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("reason_category IN ('MEDICAL','OFFICIAL_DUTY','PERSONAL','OTHER')", name="ck_faculty_absence_reason"),
        sa.CheckConstraint("status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CANCELLED')", name="ck_faculty_absence_status"),
        sa.CheckConstraint("starts_on <= ends_on", name="ck_faculty_absence_dates"))
    op.create_index("ix_faculty_absence_faculty_dates", "faculty_absences", ["faculty_id", "starts_on", "ends_on"])
    op.create_index("ix_faculty_absence_status_dates", "faculty_absences", ["status", "starts_on", "ends_on"])

    op.create_table("coverage_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("absence_id", sa.Integer(), sa.ForeignKey("faculty_absences.id"), nullable=False),
        sa.Column("timetable_entry_id", sa.Integer(), sa.ForeignKey("tt_entries.id"), nullable=False),
        sa.Column("timetable_run_id", sa.Integer(), sa.ForeignKey("tt_runs.id"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("tt_terms.id"), nullable=False),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column("original_faculty_id", sa.Integer(), sa.ForeignKey("faculty.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("safe_reason", sa.String(256)),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('PENDING','CANDIDATES_AVAILABLE','APPROVED','DECLINED','UNFILLED','CANCELLED')", name="ck_coverage_request_status"))
    op.create_index("ix_coverage_request_status_date", "coverage_requests", ["status", "occurrence_date"])
    op.create_index("uq_active_coverage_occurrence", "coverage_requests", ["timetable_entry_id", "occurrence_date"], unique=True,
                    postgresql_where=sa.text("status IN ('PENDING','CANDIDATES_AVAILABLE','APPROVED')"))

    op.create_table("coverage_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("coverage_request_id", sa.Integer(), sa.ForeignKey("coverage_requests.id"), nullable=False),
        sa.Column("substitute_faculty_id", sa.Integer(), sa.ForeignKey("faculty.id"), nullable=False),
        sa.Column("timetable_entry_id", sa.Integer(), sa.ForeignKey("tt_entries.id"), nullable=False),
        sa.Column("period_id", sa.Integer(), sa.ForeignKey("tt_periods.id"), nullable=False),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PROPOSED"),
        sa.Column("proposed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("responded_at", sa.DateTime()),
        sa.Column("cancelled_at", sa.DateTime()),
        sa.CheckConstraint("status IN ('PROPOSED','ACCEPTED','DECLINED','CANCELLED')", name="ck_coverage_assignment_status"),
        sa.UniqueConstraint("coverage_request_id", "substitute_faculty_id", name="uq_coverage_request_candidate"))
    op.create_index("ix_coverage_assignment_candidate_status", "coverage_assignments", ["substitute_faculty_id", "status"])
    op.create_index("uq_active_coverage_request_assignment", "coverage_assignments",
                    ["coverage_request_id"], unique=True,
                    postgresql_where=sa.text("status IN ('PROPOSED','ACCEPTED')"))
    op.create_index("uq_active_coverage_faculty_period", "coverage_assignments",
                    ["substitute_faculty_id", "occurrence_date", "period_id"], unique=True,
                    postgresql_where=sa.text("status IN ('PROPOSED','ACCEPTED')"))

    op.create_table("attendance_sheets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timetable_entry_id", sa.Integer(), sa.ForeignKey("tt_entries.id"), nullable=False),
        sa.Column("timetable_run_id", sa.Integer(), sa.ForeignKey("tt_runs.id"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("tt_terms.id"), nullable=False),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column("original_faculty_id", sa.Integer(), sa.ForeignKey("faculty.id"), nullable=False),
        sa.Column("marking_faculty_id", sa.Integer(), sa.ForeignKey("faculty.id"), nullable=False),
        sa.Column("coverage_assignment_id", sa.Integer(), sa.ForeignKey("coverage_assignments.id")),
        sa.Column("marking_mode", sa.String(16), nullable=False),
        sa.Column("submitted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("marking_mode IN ('NORMAL','SUBSTITUTE')", name="ck_attendance_sheet_mode"),
        sa.UniqueConstraint("timetable_entry_id", "occurrence_date", name="uq_attendance_sheet_occurrence"))

    op.add_column("attendance_records", sa.Column("attendance_sheet_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_attendance_record_sheet", "attendance_records", "attendance_sheets", ["attendance_sheet_id"], ["id"])
    op.drop_constraint("uq_attendance_entry", "attendance_records", type_="unique")
    op.create_unique_constraint("uq_attendance_sheet_student", "attendance_records", ["attendance_sheet_id", "usn"])
    op.create_index("ix_attendance_records_attendance_sheet_id", "attendance_records", ["attendance_sheet_id"])
    op.create_index("uq_attendance_legacy_entry", "attendance_records", ["usn", "subject_code", "date"], unique=True,
                    postgresql_where=sa.text("attendance_sheet_id IS NULL"))

    op.create_table("coverage_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("absence_id", sa.Integer(), sa.ForeignKey("faculty_absences.id")),
        sa.Column("coverage_request_id", sa.Integer(), sa.ForeignKey("coverage_requests.id")),
        sa.Column("timetable_entry_id", sa.Integer(), sa.ForeignKey("tt_entries.id")),
        sa.Column("occurrence_date", sa.Date()),
        sa.Column("from_status", sa.String(24)), sa.Column("to_status", sa.String(24)),
        sa.Column("reason_category", sa.String(24)),
        sa.Column("detail", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))


def downgrade():
    # Operational history is deliberately retained; downgrades never delete it.
    pass
