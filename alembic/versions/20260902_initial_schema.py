"""Create the historical MAWOS core schema for an empty PostgreSQL database.

This predecessor is intentionally before the former no-op baseline.  Existing
institutions already stamped at ``20260903_postgresql_baseline`` or later do
not run it; Alembic only executes it for an empty database.  Later revisions
remain responsible for their additive workflows.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260902_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("departments",
        sa.Column("code", sa.String(8), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("intake", sa.Integer(), nullable=False, server_default="60"))
    op.create_table("students",
        sa.Column("usn", sa.String(16), primary_key=True), sa.Column("name", sa.String(128), nullable=False),
        sa.Column("dept_code", sa.String(8), sa.ForeignKey("departments.code"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False), sa.Column("semester", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(4), nullable=False, server_default="A"), sa.Column("cgpa", sa.Float(), nullable=False),
        sa.Column("backlogs", sa.Integer(), nullable=False, server_default="0"), sa.Column("category", sa.String(16), nullable=False, server_default="GM"),
        sa.Column("family_income", sa.Float(), nullable=False, server_default="500000"), sa.Column("admission_year", sa.Integer(), nullable=False, server_default="2023"),
        sa.Column("email", sa.String(128)), sa.Column("phone", sa.String(16)), sa.Column("status", sa.String(16), nullable=False, server_default="enrolled"),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index("ix_students_dept_code", "students", ["dept_code"])
    op.create_table("faculty",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(128), nullable=False),
        sa.Column("dept_code", sa.String(8), sa.ForeignKey("departments.code"), nullable=False),
        sa.Column("designation", sa.String(64), nullable=False, server_default="Assistant Professor"), sa.Column("email", sa.String(128)))
    op.create_index("ix_faculty_dept_code", "faculty", ["dept_code"])
    op.create_table("subjects",
        sa.Column("code", sa.String(16), primary_key=True), sa.Column("name", sa.String(128), nullable=False),
        sa.Column("dept_code", sa.String(8), sa.ForeignKey("departments.code"), nullable=False),
        sa.Column("semester", sa.Integer(), nullable=False), sa.Column("credits", sa.Integer(), nullable=False, server_default="4"))
    op.create_index("ix_subjects_dept_code", "subjects", ["dept_code"])
    op.create_table("users",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False), sa.Column("role", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False), sa.Column("usn", sa.String(16), sa.ForeignKey("students.usn")),
        sa.Column("faculty_id", sa.Integer(), sa.ForeignKey("faculty.id")), sa.Column("dept_code", sa.String(8), sa.ForeignKey("departments.code")),
        sa.CheckConstraint("role IN ('student','faculty','hod','principal','admin')", name="ck_users_role"))
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_table("teaching_assignments",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("faculty_id", sa.Integer(), sa.ForeignKey("faculty.id"), nullable=False),
        sa.Column("subject_code", sa.String(16), sa.ForeignKey("subjects.code"), nullable=False), sa.Column("dept_code", sa.String(8), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False), sa.Column("section", sa.String(4), nullable=False),
        sa.UniqueConstraint("subject_code", "dept_code", "year", "section", name="uq_teach"))
    op.create_index("ix_teaching_assignments_faculty_id", "teaching_assignments", ["faculty_id"])
    op.create_table("timetable_slots",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("dept_code", sa.String(8), nullable=False), sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(4), nullable=False), sa.Column("day", sa.Integer(), nullable=False), sa.Column("period", sa.Integer(), nullable=False),
        sa.Column("subject_code", sa.String(16), sa.ForeignKey("subjects.code"), nullable=False), sa.Column("faculty_id", sa.Integer(), sa.ForeignKey("faculty.id"), nullable=False),
        sa.Column("room", sa.String(16), nullable=False, server_default=""), sa.UniqueConstraint("dept_code", "year", "section", "day", "period", name="uq_tt_slot"))
    op.create_index("ix_timetable_slots_dept_code", "timetable_slots", ["dept_code"])
    op.create_table("applications",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("applicant_name", sa.String(128), nullable=False), sa.Column("email", sa.String(128), nullable=False),
        sa.Column("phone", sa.String(16), nullable=False), sa.Column("dept_code", sa.String(8), sa.ForeignKey("departments.code"), nullable=False),
        sa.Column("category", sa.String(16), nullable=False, server_default="GM"), sa.Column("tenth_pct", sa.Float(), nullable=False), sa.Column("twelfth_pct", sa.Float(), nullable=False),
        sa.Column("entrance_score", sa.Float(), nullable=False), sa.Column("family_income", sa.Float(), nullable=False), sa.Column("status", sa.String(16), nullable=False, server_default="submitted"),
        sa.Column("merit_score", sa.Float()), sa.Column("merit_rank", sa.Integer()), sa.Column("allotted_usn", sa.String(16)), sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime()), sa.Column("updated_at", sa.DateTime()))
    op.create_index("ix_applications_dept_code", "applications", ["dept_code"]); op.create_index("ix_applications_status", "applications", ["status"])
    op.create_table("marks_records", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("usn", sa.String(16), sa.ForeignKey("students.usn"), nullable=False), sa.Column("subject_code", sa.String(16), sa.ForeignKey("subjects.code"), nullable=False), sa.Column("internal", sa.Integer(), nullable=False), sa.Column("marks", sa.Float(), nullable=False), sa.Column("max_marks", sa.Float(), nullable=False, server_default="50"), sa.Column("entered_by", sa.String(64), nullable=False, server_default=""), sa.UniqueConstraint("usn", "subject_code", "internal", name="uq_marks"))
    op.create_index("ix_marks_records_usn", "marks_records", ["usn"])
    op.create_table("attendance_records", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("usn", sa.String(16), sa.ForeignKey("students.usn"), nullable=False), sa.Column("subject_code", sa.String(16), sa.ForeignKey("subjects.code"), nullable=False), sa.Column("date", sa.Date(), nullable=False), sa.Column("present", sa.Boolean(), nullable=False), sa.Column("uploaded_by", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime()), sa.UniqueConstraint("usn", "subject_code", "date", name="uq_attendance_entry"))
    op.create_index("ix_attendance_records_usn", "attendance_records", ["usn"])
    op.create_table("attendance_summary", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("usn", sa.String(16), sa.ForeignKey("students.usn"), nullable=False), sa.Column("subject_code", sa.String(16), sa.ForeignKey("subjects.code"), nullable=False), sa.Column("classes_held", sa.Integer(), nullable=False, server_default="0"), sa.Column("classes_attended", sa.Integer(), nullable=False, server_default="0"), sa.Column("percentage", sa.Float(), nullable=False, server_default="0"), sa.Column("shortage", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("updated_at", sa.DateTime()), sa.UniqueConstraint("usn", "subject_code", name="uq_attendance_summary"))
    op.create_index("ix_attendance_summary_usn", "attendance_summary", ["usn"])
    op.create_table("fee_records", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("usn", sa.String(16), sa.ForeignKey("students.usn"), nullable=False), sa.Column("fee_type", sa.String(32), nullable=False), sa.Column("amount_due", sa.Float(), nullable=False), sa.Column("amount_paid", sa.Float(), nullable=False, server_default="0"), sa.Column("due_date", sa.Date(), nullable=False), sa.Column("paid_date", sa.Date()), sa.Column("fine", sa.Float(), nullable=False, server_default="0"), sa.Column("status", sa.String(16), nullable=False, server_default="pending"))
    op.create_index("ix_fee_records_usn", "fee_records", ["usn"])
    op.create_table("exam_schedules", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("subject_code", sa.String(16), sa.ForeignKey("subjects.code"), nullable=False), sa.Column("dept_code", sa.String(8), nullable=False), sa.Column("semester", sa.Integer(), nullable=False), sa.Column("exam_date", sa.Date(), nullable=False), sa.Column("session", sa.String(16), nullable=False, server_default="FN"))
    op.create_index("ix_exam_schedules_dept_code", "exam_schedules", ["dept_code"])
    op.create_table("hall_tickets", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("usn", sa.String(16), sa.ForeignKey("students.usn"), nullable=False), sa.Column("semester", sa.Integer(), nullable=False), sa.Column("eligible", sa.Boolean(), nullable=False), sa.Column("reasons", sa.Text(), nullable=False, server_default=""), sa.Column("updated_at", sa.DateTime()), sa.UniqueConstraint("usn", "semester", name="uq_hall_ticket"))
    op.create_index("ix_hall_tickets_usn", "hall_tickets", ["usn"])
    op.create_table("scholarship_assessments", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("usn", sa.String(16), sa.ForeignKey("students.usn"), nullable=False), sa.Column("scheme", sa.String(64), nullable=False, server_default="Merit-cum-Means"), sa.Column("status", sa.String(16), nullable=False), sa.Column("ml_score", sa.Float()), sa.Column("reasons", sa.Text(), nullable=False, server_default=""), sa.Column("assessed_at", sa.DateTime()), sa.UniqueConstraint("usn", "scheme", name="uq_scholarship_scheme"))
    op.create_index("ix_scholarship_assessments_usn", "scholarship_assessments", ["usn"])
    op.create_table("placement_drives", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("company", sa.String(128), nullable=False), sa.Column("role", sa.String(128), nullable=False), sa.Column("package_lpa", sa.Float(), nullable=False), sa.Column("min_cgpa", sa.Float(), nullable=False, server_default="6"), sa.Column("max_backlogs", sa.Integer(), nullable=False, server_default="0"), sa.Column("min_attendance", sa.Float(), nullable=False, server_default="75"), sa.Column("drive_date", sa.Date(), nullable=False), sa.Column("departments", sa.String(64), nullable=False, server_default="ALL"))
    op.create_table("placement_shortlists", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("drive_id", sa.Integer(), sa.ForeignKey("placement_drives.id"), nullable=False), sa.Column("usn", sa.String(16), sa.ForeignKey("students.usn"), nullable=False), sa.Column("eligible", sa.Boolean(), nullable=False), sa.Column("ml_probability", sa.Float()), sa.Column("reasons", sa.Text(), nullable=False, server_default=""), sa.Column("updated_at", sa.DateTime()))
    op.create_index("ix_placement_shortlists_usn", "placement_shortlists", ["usn"])
    op.create_table("notifications", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("usn", sa.String(16)), sa.Column("audience_role", sa.String(16)), sa.Column("dept_code", sa.String(8)), sa.Column("channel", sa.String(16), nullable=False, server_default="in-app"), sa.Column("title", sa.String(256), nullable=False), sa.Column("message", sa.Text(), nullable=False), sa.Column("source_agent", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()), sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_notifications_usn", "notifications", ["usn"])
    op.create_table("workflow_events", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("workflow_id", sa.String(36), nullable=False), sa.Column("topic", sa.String(64), nullable=False), sa.Column("agent", sa.String(32), nullable=False), sa.Column("hop", sa.Integer(), nullable=False, server_default="0"), sa.Column("payload", sa.Text(), nullable=False, server_default="{}"), sa.Column("created_at", sa.DateTime()), sa.Column("elapsed_ms", sa.Float(), nullable=False, server_default="0"))
    op.create_index("ix_workflow_events_workflow_id", "workflow_events", ["workflow_id"])
    op.create_table("intent_logs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("query", sa.Text(), nullable=False), sa.Column("predicted_intent", sa.String(64), nullable=False), sa.Column("method", sa.String(16), nullable=False), sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0"), sa.Column("expected_intent", sa.String(64)), sa.Column("correct", sa.Boolean()), sa.Column("created_at", sa.DateTime()))


def downgrade() -> None:
    for table in ("intent_logs", "workflow_events", "notifications", "placement_shortlists", "placement_drives", "scholarship_assessments", "hall_tickets", "exam_schedules", "fee_records", "attendance_summary", "attendance_records", "marks_records", "applications", "timetable_slots", "teaching_assignments", "users", "subjects", "faculty", "students", "departments"):
        op.drop_table(table)
