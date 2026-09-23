"""Invalidate dated reschedules outside active academic period definitions.

Revision ID: 20260923_validate_replacement_periods
Revises: 20260921_timetable_operations
"""
from alembic import op
import sqlalchemy as sa


revision = "20260923_validate_replacement_periods"
down_revision = "20260921_timetable_operations"
branch_labels = None
depends_on = None


def upgrade():
    # Alembic creates this column as VARCHAR(32), but this revision ID is
    # longer. Do this first: Alembic records the revision only after upgrade()
    # returns, within the same PostgreSQL DDL transaction.
    op.alter_column("alembic_version", "version_num", schema="public",
                    existing_type=sa.String(length=32), type_=sa.String(length=64),
                    existing_nullable=False)
    op.add_column("tt_occurrence_changes", sa.Column("invalidated_at", sa.DateTime(timezone=True)))
    op.add_column("tt_occurrence_changes", sa.Column("invalidation_reason", sa.String(256)))
    op.drop_constraint("uq_tt_occurrence_change_source", "tt_occurrence_changes", type_="unique")
    op.drop_index("uq_tt_occurrence_change_entry_target_date", table_name="tt_occurrence_changes")
    op.create_index("uq_active_tt_occurrence_change_source", "tt_occurrence_changes",
                    ["timetable_entry_id", "occurrence_date"], unique=True,
                    postgresql_where=sa.text("invalidated_at IS NULL"))
    op.create_index("uq_tt_occurrence_change_entry_target_date", "tt_occurrence_changes",
                    ["timetable_entry_id", "replacement_date"], unique=True,
                    postgresql_where=sa.text("action = 'RESCHEDULED' AND invalidated_at IS NULL"))
    # Preserve the original confirmed change and audit trail, but disable any
    # target absent from the active, teaching-period configuration.
    op.execute("""
        UPDATE tt_occurrence_changes AS occurrence_change
        SET invalidated_at = CURRENT_TIMESTAMP,
            invalidation_reason = 'Replacement period is not an active configured teaching period.'
        WHERE occurrence_change.action = 'RESCHEDULED'
          AND NOT EXISTS (
            SELECT 1 FROM tt_periods AS period
            WHERE period.term_id = occurrence_change.term_id
              AND period.day_of_week = ((EXTRACT(DOW FROM occurrence_change.replacement_date)::integer + 6) % 7)
              AND period.period_index = occurrence_change.replacement_period_index
              AND period.is_break = FALSE
              AND period.is_closed = FALSE
          )
    """)


def downgrade():
    # Invalidated rows and their audit history are intentionally retained.
    pass
