"""Add versioned academic timetables; no legacy data conversion or seeding.

Development migration tests must target mawos_test exclusively.
"""
from alembic import op
import sqlalchemy as sa

revision = '20260908_timetable'
down_revision = '20260907_scholarship_workflow'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('tt_terms',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('starts_on', sa.Date(), nullable=False),
    sa.Column('ends_on', sa.Date(), nullable=False),
    sa.CheckConstraint('starts_on <= ends_on', name='ck_tt_term_dates'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('tt_holidays',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('term_id', sa.Integer(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('label', sa.String(length=100), nullable=False),
    sa.ForeignKeyConstraint(['term_id'], ['tt_terms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('term_id', 'date', name='uq_tt_holiday')
    )
    op.create_table('tt_periods',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('term_id', sa.Integer(), nullable=False),
    sa.Column('day_of_week', sa.Integer(), nullable=False),
    sa.Column('period_index', sa.Integer(), nullable=False),
    sa.Column('starts_at', sa.Time(), nullable=False),
    sa.Column('ends_at', sa.Time(), nullable=False),
    sa.Column('is_break', sa.Boolean(), nullable=False),
    sa.Column('is_closed', sa.Boolean(), nullable=False),
    sa.CheckConstraint('day_of_week BETWEEN 0 AND 6 AND period_index BETWEEN 0 AND 23 AND starts_at < ends_at', name='ck_tt_period'),
    sa.ForeignKeyConstraint(['term_id'], ['tt_terms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('term_id', 'day_of_week', 'period_index', name='uq_tt_period')
    )
    op.create_table('tt_rooms',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('dept_code', sa.String(length=8), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('capacity', sa.Integer(), nullable=False),
    sa.CheckConstraint('capacity > 0', name='ck_tt_room_capacity'),
    sa.ForeignKeyConstraint(['dept_code'], ['departments.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('tt_sections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('term_id', sa.Integer(), nullable=False),
    sa.Column('dept_code', sa.String(length=8), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('semester', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=4), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.CheckConstraint('size > 0 AND year BETWEEN 1 AND 4 AND semester BETWEEN 1 AND 8', name='ck_tt_section'),
    sa.ForeignKeyConstraint(['dept_code'], ['departments.code'], ),
    sa.ForeignKeyConstraint(['term_id'], ['tt_terms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'term_id', 'dept_code', name='uq_tt_section_scope'),
    sa.UniqueConstraint('term_id', 'dept_code', 'year', 'semester', 'name', name='uq_tt_section')
    )
    op.create_table('tt_faculty_limits',
    sa.Column('faculty_id', sa.Integer(), nullable=False),
    sa.Column('term_id', sa.Integer(), nullable=False),
    sa.Column('daily_limit', sa.Integer(), nullable=False),
    sa.Column('weekly_limit', sa.Integer(), nullable=False),
    sa.CheckConstraint('daily_limit BETWEEN 1 AND 24 AND weekly_limit BETWEEN 1 AND 168', name='ck_tt_faculty_limits'),
    sa.ForeignKeyConstraint(['faculty_id'], ['faculty.id'], ),
    sa.ForeignKeyConstraint(['term_id'], ['tt_terms.id'], ),
    sa.PrimaryKeyConstraint('faculty_id', 'term_id')
    )
    op.create_table('tt_faculty_unavailable',
    sa.Column('faculty_id', sa.Integer(), nullable=False),
    sa.Column('period_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['faculty_id'], ['faculty.id'], ),
    sa.ForeignKeyConstraint(['period_id'], ['tt_periods.id'], ),
    sa.PrimaryKeyConstraint('faculty_id', 'period_id')
    )
    op.create_table('tt_qualifications',
    sa.Column('faculty_id', sa.Integer(), nullable=False),
    sa.Column('subject_code', sa.String(length=16), nullable=False),
    sa.ForeignKeyConstraint(['faculty_id'], ['faculty.id'], ),
    sa.ForeignKeyConstraint(['subject_code'], ['subjects.code'], ),
    sa.PrimaryKeyConstraint('faculty_id', 'subject_code')
    )
    op.create_table('tt_room_unavailable',
    sa.Column('room_id', sa.Integer(), nullable=False),
    sa.Column('period_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['period_id'], ['tt_periods.id'], ),
    sa.ForeignKeyConstraint(['room_id'], ['tt_rooms.id'], ),
    sa.PrimaryKeyConstraint('room_id', 'period_id')
    )
    op.create_table('tt_requirements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('section_id', sa.Integer(), nullable=False),
    sa.Column('assignment_id', sa.Integer(), nullable=False),
    sa.Column('periods_per_week', sa.Integer(), nullable=False),
    sa.Column('max_per_day', sa.Integer(), nullable=False),
    sa.Column('block_length', sa.Integer(), nullable=False),
    sa.Column('room_type', sa.String(length=32), nullable=False),
    sa.Column('preferred_room_type', sa.String(length=32), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.CheckConstraint('periods_per_week BETWEEN 1 AND 168 AND max_per_day BETWEEN 1 AND 24 AND block_length BETWEEN 1 AND 24 AND periods_per_week % block_length = 0 AND block_length <= max_per_day AND priority BETWEEN 0 AND 10', name='ck_tt_requirement'),
    sa.ForeignKeyConstraint(['assignment_id'], ['teaching_assignments.id'], ),
    sa.ForeignKeyConstraint(['section_id'], ['tt_sections.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('section_id', 'assignment_id', name='uq_tt_requirement')
    )
    op.create_table('tt_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('term_id', sa.Integer(), nullable=False),
    sa.Column('dept_code', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('seed', sa.Integer(), nullable=False),
    sa.Column('input_snapshot', sa.Text(), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('metrics', sa.Text(), nullable=False),
    sa.Column('conflicts', sa.Text(), nullable=False),
    sa.Column('unplaced', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('validated_by', sa.Integer(), nullable=True),
    sa.Column('published_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('parent_run_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("status IN ('DRAFT','GENERATING','COMPLETE','PARTIAL','FAILED','PUBLISHED','ARCHIVED')", name='ck_tt_run_status'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['dept_code'], ['departments.code'], ),
    sa.ForeignKeyConstraint(['parent_run_id'], ['tt_runs.id'], ),
    sa.ForeignKeyConstraint(['published_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['term_id'], ['tt_terms.id'], ),
    sa.ForeignKeyConstraint(['validated_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'term_id', 'dept_code', name='uq_tt_run_scope')
    )
    op.create_index('uq_tt_published_scope', 'tt_runs', ['term_id', 'dept_code'], unique=True, postgresql_where=sa.text("status = 'PUBLISHED'"), sqlite_where=sa.text("status = 'PUBLISHED'"))
    op.create_table('tt_audit',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('term_id', sa.Integer(), nullable=True),
    sa.Column('dept_code', sa.String(length=8), nullable=True),
    sa.Column('actor_id', sa.Integer(), nullable=False),
    sa.Column('event', sa.String(length=48), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['dept_code'], ['departments.code'], ),
    sa.ForeignKeyConstraint(['run_id'], ['tt_runs.id'], ),
    sa.ForeignKeyConstraint(['term_id'], ['tt_terms.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('tt_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('term_id', sa.Integer(), nullable=False),
    sa.Column('dept_code', sa.String(length=8), nullable=False),
    sa.Column('section_id', sa.Integer(), nullable=False),
    sa.Column('requirement_id', sa.Integer(), nullable=False),
    sa.Column('subject_code', sa.String(length=16), nullable=False),
    sa.Column('faculty_id', sa.Integer(), nullable=False),
    sa.Column('room_id', sa.Integer(), nullable=False),
    sa.Column('occurrence', sa.Integer(), nullable=False),
    sa.Column('day_of_week', sa.Integer(), nullable=False),
    sa.Column('period_index', sa.Integer(), nullable=False),
    sa.Column('locked', sa.Boolean(), nullable=False),
    sa.CheckConstraint('occurrence >= 0', name='ck_tt_entry_occurrence'),
    sa.ForeignKeyConstraint(['dept_code'], ['departments.code'], ),
    sa.ForeignKeyConstraint(['faculty_id'], ['faculty.id'], ),
    sa.ForeignKeyConstraint(['requirement_id'], ['tt_requirements.id'], ),
    sa.ForeignKeyConstraint(['room_id'], ['tt_rooms.id'], ),
    sa.ForeignKeyConstraint(['run_id', 'term_id', 'dept_code'], ['tt_runs.id', 'tt_runs.term_id', 'tt_runs.dept_code'], name='fk_tt_entry_run_scope'),
    sa.ForeignKeyConstraint(['run_id'], ['tt_runs.id'], ),
    sa.ForeignKeyConstraint(['section_id', 'term_id', 'dept_code'], ['tt_sections.id', 'tt_sections.term_id', 'tt_sections.dept_code'], name='fk_tt_entry_section_scope'),
    sa.ForeignKeyConstraint(['section_id'], ['tt_sections.id'], ),
    sa.ForeignKeyConstraint(['subject_code'], ['subjects.code'], ),
    sa.ForeignKeyConstraint(['term_id', 'day_of_week', 'period_index'], ['tt_periods.term_id', 'tt_periods.day_of_week', 'tt_periods.period_index'], name='fk_tt_entry_period'),
    sa.ForeignKeyConstraint(['term_id'], ['tt_terms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'faculty_id', 'day_of_week', 'period_index', name='uq_tt_entry_faculty'),
    sa.UniqueConstraint('run_id', 'room_id', 'day_of_week', 'period_index', name='uq_tt_entry_room'),
    sa.UniqueConstraint('run_id', 'section_id', 'day_of_week', 'period_index', name='uq_tt_entry_section')
    )


def downgrade():
    op.drop_table('tt_entries')
    op.drop_table('tt_audit')
    op.drop_table('tt_runs')
    op.drop_table('tt_requirements')
    op.drop_table('tt_room_unavailable')
    op.drop_table('tt_qualifications')
    op.drop_table('tt_faculty_unavailable')
    op.drop_table('tt_faculty_limits')
    op.drop_table('tt_sections')
    op.drop_table('tt_rooms')
    op.drop_table('tt_periods')
    op.drop_table('tt_holidays')
    op.drop_table('tt_terms')
