"""Add confirmed timetable operations, occurrence exceptions, and immutable audit.

Revision ID: 20260921_timetable_operations
Revises: 20260916_faculty_coverage
"""
from alembic import op
import sqlalchemy as sa

revision = "20260921_timetable_operations"
down_revision = "20260916_faculty_coverage"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("tt_operation_previews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("correlation_id", sa.String(36), nullable=False, unique=True),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="PREVIEW"),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("dept_code", sa.String(8), sa.ForeignKey("departments.code"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("tt_terms.id")),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("tt_runs.id")),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("preview_json", sa.Text(), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('PREVIEW','CONFIRMED','EXPIRED')", name="ck_tt_operation_preview_state"))
    op.create_index("ix_tt_operation_actor_created", "tt_operation_previews", ["actor_id", "created_at"])
    op.create_index("ix_tt_operation_scope_state", "tt_operation_previews", ["dept_code", "state"])

    op.create_table("tt_operation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("preview_id", sa.String(36), sa.ForeignKey("tt_operation_previews.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("dept_code", sa.String(8), sa.ForeignKey("departments.code"), nullable=False),
        sa.Column("requested_action", sa.Text(), nullable=False),
        sa.Column("affected_records", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("before_summary", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("after_summary", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("phase IN ('PREVIEWED','CONFIRMED','REJECTED','EXPIRED')", name="ck_tt_operation_event_phase"),
        sa.UniqueConstraint("preview_id", "phase", name="uq_tt_operation_event_phase"))
    op.create_index("ix_tt_operation_event_correlation", "tt_operation_events", ["correlation_id"])
    op.create_index("ix_tt_operation_event_scope_created", "tt_operation_events", ["dept_code", "created_at"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION prevent_tt_operation_event_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              RAISE EXCEPTION 'tt_operation_events is append-only';
            END $$
        """)
        op.execute("""
            CREATE TRIGGER trg_tt_operation_events_append_only
            BEFORE UPDATE OR DELETE ON tt_operation_events
            FOR EACH ROW EXECUTE FUNCTION prevent_tt_operation_event_mutation()
        """)

    op.create_table("tt_occurrence_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timetable_entry_id", sa.Integer(), sa.ForeignKey("tt_entries.id"), nullable=False),
        sa.Column("timetable_run_id", sa.Integer(), sa.ForeignKey("tt_runs.id"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("tt_terms.id"), nullable=False),
        sa.Column("dept_code", sa.String(8), sa.ForeignKey("departments.code"), nullable=False),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("replacement_date", sa.Date()),
        sa.Column("replacement_period_index", sa.Integer()),
        sa.Column("replacement_room_id", sa.Integer(), sa.ForeignKey("tt_rooms.id")),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("applied_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("action IN ('RESCHEDULED','CANCELLED')", name="ck_tt_occurrence_change_action"),
        sa.CheckConstraint("(action = 'CANCELLED' AND replacement_date IS NULL AND replacement_period_index IS NULL AND replacement_room_id IS NULL) OR (action = 'RESCHEDULED' AND replacement_date IS NOT NULL AND replacement_period_index IS NOT NULL AND replacement_room_id IS NOT NULL)", name="ck_tt_occurrence_change_target"),
        sa.UniqueConstraint("timetable_entry_id", "occurrence_date", name="uq_tt_occurrence_change_source"))
    op.create_index("ix_tt_occurrence_change_target", "tt_occurrence_changes", ["replacement_date", "replacement_period_index"])
    op.create_index("uq_tt_occurrence_change_entry_target_date", "tt_occurrence_changes",
                    ["timetable_entry_id", "replacement_date"], unique=True,
                    postgresql_where=sa.text("action = 'RESCHEDULED'"),
                    sqlite_where=sa.text("action = 'RESCHEDULED'"))
    op.create_index("ix_tt_occurrence_change_scope_date", "tt_occurrence_changes", ["dept_code", "occurrence_date"])
    op.create_index("ix_tt_occurrence_change_correlation", "tt_occurrence_changes", ["correlation_id"])


def downgrade():
    # Timetable operation/audit history is intentionally retained.
    pass
