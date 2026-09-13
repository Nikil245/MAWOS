"""Add private job-document metadata and placement lifecycle details.

This revision is additive and deliberately does not reconcile data. The
separate audited maintenance command performs the legacy OPEN-state repair.
Downgrade retains schema/data so rollback never destroys placement documents
or descriptions; re-upgrade is idempotent.
"""
from alembic import op
import sqlalchemy as sa

revision = '20260912_placement_details'
down_revision = '20260911_placement'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'placement_drives' not in inspector.get_table_names():
        raise RuntimeError('Apply 20260911_placement before this revision')
    existing = {column['name'] for column in inspector.get_columns('placement_drives')}
    additions = [
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('application_url', sa.String(2048), nullable=True),
        sa.Column('job_document_storage_key', sa.String(128), nullable=True),
        sa.Column('job_document_original_name', sa.String(255), nullable=True),
        sa.Column('job_document_content_type', sa.String(128), nullable=True),
        sa.Column('job_document_size_bytes', sa.Integer(), nullable=True),
        sa.Column('job_document_sha256', sa.String(64), nullable=True),
        sa.Column('job_document_uploaded_at', sa.DateTime(), nullable=True),
        sa.Column('job_document_uploaded_by', sa.Integer(), nullable=True),
        sa.Column('cancellation_reason', sa.Text(), nullable=True),
    ]
    for column in additions:
        if column.name not in existing:
            op.add_column('placement_drives', column)
    inspector = sa.inspect(bind)
    foreign_keys = inspector.get_foreign_keys('placement_drives')
    if not any(fk.get('name') == 'fk_placement_document_uploader' or
               fk.get('constrained_columns') == ['job_document_uploaded_by']
               for fk in foreign_keys):
        op.create_foreign_key('fk_placement_document_uploader', 'placement_drives',
                              'users', ['job_document_uploaded_by'], ['id'])


def downgrade():
    # Retain every additive field and its references. Alembic only moves the
    # version marker, matching the data-preserving placement migration policy.
    pass
