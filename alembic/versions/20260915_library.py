"""Physical library and dedicated librarian accounts (additive, history preserving).

Revision ID: 20260915_library
Revises: 20260914_parent_portal
"""
from alembic import op
import sqlalchemy as sa

revision = '20260915_library'
down_revision = '20260914_parent_portal'
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column(name, sa.DateTime(), nullable=False, server_default=sa.func.now())
            for name in ('created_at', 'updated_at')]


def upgrade():
    bind = op.get_bind()
    checks = sa.inspect(bind).get_check_constraints('users')
    for check in checks:
        if 'role' in (check.get('sqltext') or '').lower() and check['name']:
            op.drop_constraint(check['name'], 'users', type_='check')
    op.create_check_constraint('ck_users_role', 'users',
        "role IN ('student','faculty','hod','principal','admin','parent','librarian')")
    tables = set(sa.inspect(bind).get_table_names())
    # A downgrade retains records, so re-upgrade deliberately tolerates these tables.
    if 'books' not in tables:
        op.create_table('books', sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('isbn', sa.String(32), nullable=False, unique=True),
            sa.Column('title', sa.String(256), nullable=False),
            sa.Column('author', sa.String(256), nullable=False),
            sa.Column('publisher', sa.String(256)),
            sa.Column('category', sa.String(128), nullable=False),
            sa.Column('description', sa.Text()),
            sa.Column('total_copies', sa.Integer(), nullable=False),
            sa.Column('available_copies', sa.Integer(), nullable=False),
            sa.Column('popularity_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
            *timestamps(),
            sa.CheckConstraint('total_copies >= 0 AND available_copies >= 0 AND available_copies <= total_copies', name='ck_books_stock'),
            sa.CheckConstraint('popularity_count >= 0', name='ck_books_popularity'))
        op.create_index('ix_books_active_title', 'books', ['is_active', 'title'])
        op.create_index('ix_books_category', 'books', ['category'])
    if 'book_departments' not in tables:
        op.create_table('book_departments',
            sa.Column('book_id', sa.Integer(), sa.ForeignKey('books.id'), primary_key=True),
            sa.Column('department_code', sa.String(8), sa.ForeignKey('departments.code'), primary_key=True))
    if 'book_reservations' not in tables:
        op.create_table('book_reservations', sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('student_usn', sa.String(16), sa.ForeignKey('students.usn'), nullable=False),
            sa.Column('book_id', sa.Integer(), sa.ForeignKey('books.id'), nullable=False),
            sa.Column('slip_code', sa.String(6), nullable=False, unique=True),
            sa.Column('status', sa.String(20), nullable=False),
            sa.Column('requested_at', sa.DateTime(), nullable=False),
            sa.Column('pickup_deadline', sa.DateTime(), nullable=False),
            sa.Column('collected_at', sa.DateTime()), sa.Column('cancelled_at', sa.DateTime()),
            sa.Column('expired_at', sa.DateTime()), *timestamps(),
            sa.CheckConstraint("status IN ('PENDING_PICKUP','COLLECTED','CANCELLED','EXPIRED')", name='ck_book_reservations_status'),
            sa.CheckConstraint("slip_code ~ '^[0-9]{6}$'", name='ck_book_reservations_slip'),
            sa.CheckConstraint('pickup_deadline > requested_at', name='ck_book_reservations_deadline'))
        op.create_index('uq_book_reservations_pending', 'book_reservations', ['student_usn', 'book_id'], unique=True,
                        postgresql_where=sa.text("status = 'PENDING_PICKUP'"))
        op.create_index('ix_book_reservations_expiry', 'book_reservations', ['status', 'pickup_deadline'])
        op.create_index('ix_book_reservations_student_usn', 'book_reservations', ['student_usn'])
        op.create_index('ix_book_reservations_book_id', 'book_reservations', ['book_id'])
    if 'book_issues' not in tables:
        op.create_table('book_issues', sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('student_usn', sa.String(16), sa.ForeignKey('students.usn'), nullable=False),
            sa.Column('book_id', sa.Integer(), sa.ForeignKey('books.id'), nullable=False),
            sa.Column('reservation_id', sa.Integer(), sa.ForeignKey('book_reservations.id'), unique=True),
            sa.Column('status', sa.String(20), nullable=False),
            sa.Column('issued_at', sa.DateTime(), nullable=False), sa.Column('due_at', sa.DateTime(), nullable=False),
            sa.Column('return_requested_at', sa.DateTime()), sa.Column('returned_at', sa.DateTime()),
            sa.Column('return_rejection_reason', sa.String(1000)), *timestamps(),
            sa.CheckConstraint("status IN ('ISSUED','RETURN_PENDING','RETURNED')", name='ck_book_issues_status'),
            sa.CheckConstraint('due_at > issued_at', name='ck_book_issues_due'))
        op.create_index('ix_book_issues_active_due', 'book_issues', ['status', 'due_at'])
        op.create_index('ix_book_issues_student_status', 'book_issues', ['student_usn', 'status'])
        op.create_index('ix_book_issues_book_id', 'book_issues', ['book_id'])
    if 'library_fines' not in tables:
        op.create_table('library_fines', sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('student_usn', sa.String(16), sa.ForeignKey('students.usn'), nullable=False),
            sa.Column('source_type', sa.String(24), nullable=False), sa.Column('source_id', sa.Integer(), nullable=False),
            sa.Column('amount', sa.Numeric(12, 2), nullable=False), sa.Column('reason', sa.String(1000), nullable=False),
            sa.Column('status', sa.String(8), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('paid_at', sa.DateTime()), sa.Column('collected_by_user_id', sa.Integer(), sa.ForeignKey('users.id')),
            sa.UniqueConstraint('source_type', 'source_id', name='uq_library_fines_source'),
            sa.CheckConstraint("source_type IN ('OVERDUE_RETURN','MISSED_PICKUP')", name='ck_library_fines_source'),
            sa.CheckConstraint("status IN ('UNPAID','PAID')", name='ck_library_fines_status'),
            sa.CheckConstraint('amount >= 0 AND source_id > 0', name='ck_library_fines_amount'))
        op.create_index('ix_library_fines_student_status', 'library_fines', ['student_usn', 'status'])
    if 'book_reviews' not in tables:
        op.create_table('book_reviews', sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('student_usn', sa.String(16), sa.ForeignKey('students.usn'), nullable=False),
            sa.Column('book_id', sa.Integer(), sa.ForeignKey('books.id'), nullable=False),
            sa.Column('issue_id', sa.Integer(), sa.ForeignKey('book_issues.id'), nullable=False, unique=True),
            sa.Column('rating', sa.Integer(), nullable=False), sa.Column('comment', sa.String(2000)), *timestamps(),
            sa.CheckConstraint('rating >= 1 AND rating <= 5', name='ck_book_reviews_rating'),
            sa.CheckConstraint('length(comment) <= 2000', name='ck_book_reviews_comment'))
        op.create_index('ix_book_reviews_book_id', 'book_reviews', ['book_id'])
    if 'librarian_accounts' not in tables:
        op.create_table('librarian_accounts',
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
            sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    # Same retention policy as parent_portal: no deletion of lending, fines,
    # reviews, identity or inventory history, and no invalidating existing roles.
    pass
