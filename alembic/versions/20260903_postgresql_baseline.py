"""Baseline for the existing MAWOS PostgreSQL public schema.

This revision intentionally contains no DDL. After a verified backup and a
successful read-only schema comparison, record it with `alembic stamp head`.
Do not run `alembic upgrade` against the populated database for this baseline.
"""
from alembic import op


revision = "20260903_postgresql_baseline"
down_revision = "20260902_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
