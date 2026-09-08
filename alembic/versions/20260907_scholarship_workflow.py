"""Add the approved scholarship workflow tables and versioned results.

This migration is to be upgraded/downgraded on mawos_test during development;
do not apply it to the populated live mawos database without an approved change.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260907_scholarship_workflow"
down_revision = "20260903_postgresql_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("scholarships",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(160), nullable=False), sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("amount", sa.Float(), nullable=False), sa.Column("application_url", sa.String(512), nullable=False),
        sa.Column("opens_at", sa.DateTime(), nullable=False), sa.Column("closes_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("created_by_faculty_id", sa.Integer(), sa.ForeignKey("faculty.id"), nullable=False),
        sa.Column("department_code", sa.String(8), sa.ForeignKey("departments.code"), nullable=False),
        sa.Column("approved_by_hod_id", sa.Integer(), sa.ForeignKey("faculty.id")), sa.Column("approval_comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("rejection_reason", sa.Text(), nullable=False, server_default=""), sa.Column("official_document_reference", sa.String(512), nullable=False, server_default=""),
        sa.Column("criteria_version", sa.Integer(), nullable=False, server_default="1"), sa.Column("criteria", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("published_at", sa.DateTime()), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_scholarships_status", "scholarships", ["status"]); op.create_index("ix_scholarships_department_code", "scholarships", ["department_code"]); op.create_index("ix_scholarships_created_by_faculty_id", "scholarships", ["created_by_faculty_id"])
    op.create_table("scholarship_applications", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("scholarship_id", sa.Integer(), sa.ForeignKey("scholarships.id"), nullable=False), sa.Column("student_usn", sa.String(16), sa.ForeignKey("students.usn"), nullable=False), sa.Column("application_status", sa.String(24), nullable=False, server_default="SUBMITTED"), sa.Column("applied_at", sa.DateTime(), nullable=False), sa.Column("external_reference", sa.String(256), nullable=False, server_default=""), sa.Column("verified_by", sa.Integer(), sa.ForeignKey("faculty.id")), sa.Column("updated_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("scholarship_id", "student_usn", name="uq_scholarship_application"))
    op.create_index("ix_scholarship_applications_scholarship_id", "scholarship_applications", ["scholarship_id"]); op.create_index("ix_scholarship_applications_student_usn", "scholarship_applications", ["student_usn"])
    op.add_column("scholarship_assessments", sa.Column("scholarship_id", sa.Integer(), nullable=True)); op.add_column("scholarship_assessments", sa.Column("eligibility_status", sa.String(24), nullable=True)); op.add_column("scholarship_assessments", sa.Column("reason_codes", sa.Text(), nullable=False, server_default="[]")); op.add_column("scholarship_assessments", sa.Column("criteria_version", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_assessment_scholarship", "scholarship_assessments", "scholarships", ["scholarship_id"], ["id"]); op.create_index("ix_scholarship_assessments_scholarship_id", "scholarship_assessments", ["scholarship_id"])


def downgrade():
    op.drop_index("ix_scholarship_assessments_scholarship_id", table_name="scholarship_assessments"); op.drop_constraint("fk_assessment_scholarship", "scholarship_assessments", type_="foreignkey")
    op.drop_column("scholarship_assessments", "criteria_version"); op.drop_column("scholarship_assessments", "reason_codes"); op.drop_column("scholarship_assessments", "eligibility_status"); op.drop_column("scholarship_assessments", "scholarship_id")
    op.drop_index("ix_scholarship_applications_student_usn", table_name="scholarship_applications"); op.drop_index("ix_scholarship_applications_scholarship_id", table_name="scholarship_applications"); op.drop_table("scholarship_applications")
    op.drop_index("ix_scholarships_created_by_faculty_id", table_name="scholarships"); op.drop_index("ix_scholarships_department_code", table_name="scholarships"); op.drop_index("ix_scholarships_status", table_name="scholarships"); op.drop_table("scholarships")
