"""Add parent accounts, student links, and first-login password state.

This revision is additive and does not create, seed, update, or delete parent rows.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260914_parent_portal"
down_revision = "20260913_campus_events"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if not {"users", "students"} <= tables:
        raise RuntimeError("Restore the MAWOS users and students tables before this revision")

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "must_change_password" not in user_columns:
        op.add_column("users", sa.Column("must_change_password", sa.Boolean(),
                                         nullable=False, server_default=sa.false()))
    role_checks = inspector.get_check_constraints("users")
    for constraint in role_checks:
        sqltext = (constraint.get("sqltext") or "").lower()
        if "role" in sqltext and "parent" not in sqltext and constraint.get("name"):
            op.drop_constraint(constraint["name"], "users", type_="check")
    if not any("role" in (item.get("sqltext") or "").lower()
               and "parent" in (item.get("sqltext") or "").lower()
               for item in role_checks):
        op.create_check_constraint(
            "ck_users_role", "users",
            "role IN ('student','faculty','hod','principal','admin','parent')")

    if "parents" not in tables:
        op.create_table(
            "parents",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("full_name", sa.String(128), nullable=False),
            sa.Column("email", sa.String(128), nullable=True),
            sa.Column("mobile", sa.String(20), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.UniqueConstraint("user_id", name="uq_parents_user_id"),
            sa.UniqueConstraint("email", name="uq_parents_email"),
        )
        op.create_index("ix_parents_user_id", "parents", ["user_id"], unique=True)

    tables = set(sa.inspect(bind).get_table_names())
    if "parent_students" not in tables:
        op.create_table(
            "parent_students",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("parent_id", sa.Integer(), sa.ForeignKey("parents.id"), nullable=False),
            sa.Column("student_usn", sa.String(16), sa.ForeignKey("students.usn"), nullable=False),
            sa.Column("relationship", sa.String(16), nullable=False),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("relationship IN ('Father','Mother','Guardian','Other')",
                               name="ck_parent_students_relationship"),
        )
        op.create_index("ix_parent_students_parent_id", "parent_students", ["parent_id"])
        op.create_index("ix_parent_students_student_active", "parent_students",
                        ["student_usn", "active"])
        op.create_index("uq_parent_students_active", "parent_students",
                        ["parent_id", "student_usn"], unique=True,
                        postgresql_where=sa.text("active = true"))


def downgrade():
    # Deliberately preserve accounts and links. Removing these structures would
    # delete identity/history data, which is outside MAWOS rollback policy.
    pass
