"""Add recipient ownership, metadata and idempotency to notifications.

All changes are additive. Existing rows and legacy targeting columns are kept;
student rows are linked to their user account where an exact USN match exists.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260912_notifications"
down_revision = "20260912_placement_details"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if not {"notifications", "users"} <= tables:
        raise RuntimeError("Restore the MAWOS users and notifications tables before this revision")

    existing = {column["name"] for column in inspector.get_columns("notifications")}
    additions = [
        sa.Column("recipient_user_id", sa.Integer(), nullable=True),
        sa.Column("notification_type", sa.String(64), nullable=False, server_default="GENERAL"),
        sa.Column("route", sa.String(512), nullable=True),
        sa.Column("related_entity_type", sa.String(64), nullable=True),
        sa.Column("related_entity_id", sa.String(64), nullable=True),
        sa.Column("event_key", sa.String(255), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
    ]
    for column in additions:
        if column.name not in existing:
            op.add_column("notifications", column)

    inspector = sa.inspect(bind)
    foreign_keys = inspector.get_foreign_keys("notifications")
    if not any(fk.get("constrained_columns") == ["recipient_user_id"] for fk in foreign_keys):
        op.create_foreign_key(
            "fk_notifications_recipient_user", "notifications", "users",
            ["recipient_user_id"], ["id"])

    # Preserve every legacy row. Exact student rows become individually owned;
    # shared role rows remain stored but are no longer exposed as mutable rows.
    bind.execute(sa.text("""
        UPDATE notifications AS n
           SET recipient_user_id = u.id
          FROM users AS u
         WHERE n.recipient_user_id IS NULL
           AND n.usn IS NOT NULL
           AND u.usn = n.usn
    """))

    uniques = inspector.get_unique_constraints("notifications")
    if not any(set(item.get("column_names") or []) == {"recipient_user_id", "event_key"}
               for item in uniques):
        op.create_unique_constraint(
            "uq_notification_recipient_event", "notifications",
            ["recipient_user_id", "event_key"])

    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("notifications")}
    if "ix_notifications_recipient_unread_created" not in indexes:
        op.create_index(
            "ix_notifications_recipient_unread_created", "notifications",
            ["recipient_user_id", "created_at"],
            postgresql_where=sa.text("read = false"))


def downgrade():
    # Data-preserving rollback: keep additive notification ownership and metadata.
    pass
