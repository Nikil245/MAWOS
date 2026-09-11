"""Add placement lifecycle/outcomes without changing existing placement rows.

Downgrade is deliberately schema-retaining: data and additive columns/tables
remain available to the previous application. Re-upgrade is idempotent. No
table or column is dropped by either direction.
"""
from alembic import op
import sqlalchemy as sa

revision = '20260911_placement'
down_revision = '20260908_timetable'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not {'placement_drives', 'placement_shortlists', 'students'} <= set(inspector.get_table_names()):
        raise RuntimeError('Restore the existing MAWOS baseline before the placement migration')
    columns = {c['name'] for c in inspector.get_columns('placement_drives')}
    additions = [
        sa.Column('status', sa.String(24), nullable=False, server_default='OPEN'),
        sa.Column('requires_fee_clearance', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('application_deadline', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text("timezone('UTC', now())")),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text("timezone('UTC', now())")),
    ]
    for column in additions:
        if column.name not in columns:
            op.add_column('placement_drives', column)
    if 'model_version' not in {c['name'] for c in inspector.get_columns('placement_shortlists')}:
        op.add_column('placement_shortlists', sa.Column('model_version', sa.String(16), nullable=True))

    checks = {c['name'] for c in inspector.get_check_constraints('placement_drives')}
    if 'ck_placement_drive_status' not in checks:
        op.create_check_constraint('ck_placement_drive_status', 'placement_drives',
            "status IN ('DRAFT','OPEN','SHORTLIST_GENERATED','CLOSED','CANCELLED')")
    unique = inspector.get_unique_constraints('placement_shortlists')
    if not any(set(c['column_names']) == {'drive_id', 'usn'} for c in unique):
        duplicate = bind.execute(sa.text('SELECT 1 FROM placement_shortlists GROUP BY drive_id, usn HAVING count(*) > 1 LIMIT 1')).first()
        if duplicate:
            raise RuntimeError('Duplicate placement shortlist pairs require administrator review; no rows were removed')
        op.create_unique_constraint('uq_shortlist_entry', 'placement_shortlists', ['drive_id', 'usn'])

    if 'placement_outcomes' not in inspector.get_table_names():
        op.create_table('placement_outcomes',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('drive_id', sa.Integer(), sa.ForeignKey('placement_drives.id'), nullable=False),
            sa.Column('usn', sa.String(16), sa.ForeignKey('students.usn'), nullable=False),
            sa.Column('outcome_status', sa.String(24), nullable=False),
            sa.Column('package_offered', sa.Float(), nullable=True),
            sa.Column('decided_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('drive_id', 'usn', name='uq_placement_outcome'),
            sa.CheckConstraint("outcome_status IN ('OFFER_MADE','OFFER_ACCEPTED','OFFER_DECLINED','REJECTED')", name='ck_placement_outcome_status'))

    for table, name, columns in [
        ('placement_drives', 'ix_placement_drive_status_date', ['status', 'drive_date']),
        ('placement_shortlists', 'ix_placement_shortlists_usn', ['usn']),
        ('placement_outcomes', 'ix_placement_outcome_usn_status', ['usn', 'outcome_status']),
    ]:
        if name not in {i['name'] for i in sa.inspect(bind).get_indexes(table)}:
            op.create_index(name, table, columns)


def downgrade():
    # Retain all placement schema/data. Alembic only rolls back version tracking.
    pass
