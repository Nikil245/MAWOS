"""Add the campus event announcement calendar.

The revision is additive and does not modify or remove existing application data.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260913_campus_events"
down_revision = "20260912_notifications"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "campus_events" not in tables:
        if not {"users", "departments"} <= tables:
            raise RuntimeError("Restore MAWOS users and departments before creating campus events")
        op.create_table(
            "campus_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("event_date", sa.Date(), nullable=False),
            sa.Column("start_time", sa.Time(), nullable=True),
            sa.Column("end_time", sa.Time(), nullable=True),
            sa.Column("venue", sa.String(200), nullable=True),
            sa.Column("organizer", sa.String(200), nullable=True),
            sa.Column("audience", sa.String(128), nullable=False, server_default="ALL"),
            sa.Column("department_code", sa.String(8), sa.ForeignKey("departments.code"), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
            sa.Column("cancellation_reason", sa.Text(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("timezone('UTC', now())")),
            sa.Column("updated_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("timezone('UTC', now())")),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "status IN ('DRAFT','PUBLISHED','CANCELLED')",
                name="ck_campus_event_status"),
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("campus_events")}
    if "ix_campus_events_status_date" not in indexes:
        op.create_index(
            "ix_campus_events_status_date", "campus_events",
            ["status", "event_date", "start_time"])


def downgrade():
    # Retain event records and schema so rollback cannot destroy announcements.
    pass
