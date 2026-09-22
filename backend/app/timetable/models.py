"""Academic configuration and immutable version history; legacy slots are research-only."""
import datetime as dt
from sqlalchemy import (Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
                        ForeignKeyConstraint, Index, Integer, String, Text, Time, UniqueConstraint, text)
from sqlalchemy import event
from ..database import Base


def now():
    return dt.datetime.now(dt.timezone.utc)


class Term(Base):
    __tablename__ = 'tt_terms'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    starts_on = Column(Date, nullable=False)
    ends_on = Column(Date, nullable=False)
    __table_args__ = (CheckConstraint('starts_on <= ends_on', name='ck_tt_term_dates'),)


class PeriodDefinition(Base):
    __tablename__ = 'tt_periods'
    id = Column(Integer, primary_key=True)
    term_id = Column(Integer, ForeignKey('tt_terms.id'), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    period_index = Column(Integer, nullable=False)
    starts_at = Column(Time, nullable=False)
    ends_at = Column(Time, nullable=False)
    is_break = Column(Boolean, nullable=False, default=False)
    is_closed = Column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint('term_id', 'day_of_week', 'period_index', name='uq_tt_period'),
                      CheckConstraint('day_of_week BETWEEN 0 AND 6 AND period_index BETWEEN 0 AND 23 AND starts_at < ends_at', name='ck_tt_period'))


class Holiday(Base):
    __tablename__ = 'tt_holidays'
    id = Column(Integer, primary_key=True)
    term_id = Column(Integer, ForeignKey('tt_terms.id'), nullable=False)
    date = Column(Date, nullable=False)
    label = Column(String(100), nullable=False)
    __table_args__ = (UniqueConstraint('term_id', 'date', name='uq_tt_holiday'),)


class Section(Base):
    __tablename__ = 'tt_sections'
    id = Column(Integer, primary_key=True)
    term_id = Column(Integer, ForeignKey('tt_terms.id'), nullable=False)
    dept_code = Column(String(8), ForeignKey('departments.code'), nullable=False)
    year = Column(Integer, nullable=False)
    semester = Column(Integer, nullable=False)
    name = Column(String(4), nullable=False)
    size = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint('term_id', 'dept_code', 'year', 'semester', 'name', name='uq_tt_section'),
                      UniqueConstraint('id', 'term_id', 'dept_code', name='uq_tt_section_scope'),
                      CheckConstraint('size > 0 AND year BETWEEN 1 AND 4 AND semester BETWEEN 1 AND 8', name='ck_tt_section'))


class Room(Base):
    __tablename__ = 'tt_rooms'
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    # Department ownership prevents cross-department resource collisions by construction.
    dept_code = Column(String(8), ForeignKey('departments.code'), nullable=False)
    kind = Column(String(32), nullable=False)
    capacity = Column(Integer, nullable=False)
    __table_args__ = (CheckConstraint('capacity > 0', name='ck_tt_room_capacity'),)


class Qualification(Base):
    __tablename__ = 'tt_qualifications'
    faculty_id = Column(Integer, ForeignKey('faculty.id'), primary_key=True)
    subject_code = Column(String(16), ForeignKey('subjects.code'), primary_key=True)


class FacultyLimit(Base):
    __tablename__ = 'tt_faculty_limits'
    faculty_id = Column(Integer, ForeignKey('faculty.id'), primary_key=True)
    term_id = Column(Integer, ForeignKey('tt_terms.id'), primary_key=True)
    daily_limit = Column(Integer, nullable=False)
    weekly_limit = Column(Integer, nullable=False)
    __table_args__ = (CheckConstraint('daily_limit BETWEEN 1 AND 24 AND weekly_limit BETWEEN 1 AND 168', name='ck_tt_faculty_limits'),)


class FacultyUnavailable(Base):
    __tablename__ = 'tt_faculty_unavailable'
    faculty_id = Column(Integer, ForeignKey('faculty.id'), primary_key=True)
    period_id = Column(Integer, ForeignKey('tt_periods.id'), primary_key=True)


class RoomUnavailable(Base):
    __tablename__ = 'tt_room_unavailable'
    room_id = Column(Integer, ForeignKey('tt_rooms.id'), primary_key=True)
    period_id = Column(Integer, ForeignKey('tt_periods.id'), primary_key=True)


class Requirement(Base):
    __tablename__ = 'tt_requirements'
    id = Column(Integer, primary_key=True)
    section_id = Column(Integer, ForeignKey('tt_sections.id'), nullable=False)
    assignment_id = Column(Integer, ForeignKey('teaching_assignments.id'), nullable=False)
    periods_per_week = Column(Integer, nullable=False)
    max_per_day = Column(Integer, nullable=False)
    block_length = Column(Integer, nullable=False, default=1)
    room_type = Column(String(32), nullable=False, default='classroom')
    preferred_room_type = Column(String(32), nullable=True)
    priority = Column(Integer, nullable=False, default=1)
    __table_args__ = (UniqueConstraint('section_id', 'assignment_id', name='uq_tt_requirement'),
                      CheckConstraint('periods_per_week BETWEEN 1 AND 168 AND max_per_day BETWEEN 1 AND 24 AND block_length BETWEEN 1 AND 24 AND periods_per_week % block_length = 0 AND block_length <= max_per_day AND priority BETWEEN 0 AND 10', name='ck_tt_requirement'))


class Run(Base):
    __tablename__ = 'tt_runs'
    id = Column(Integer, primary_key=True)
    term_id = Column(Integer, ForeignKey('tt_terms.id'), nullable=False)
    dept_code = Column(String(8), ForeignKey('departments.code'), nullable=False)
    status = Column(String(16), nullable=False)
    seed = Column(Integer, nullable=False)
    input_snapshot = Column(Text, nullable=False)
    input_hash = Column(String(64), nullable=False)
    metrics = Column(Text, nullable=False)
    conflicts = Column(Text, nullable=False)
    unplaced = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    validated_by = Column(Integer, ForeignKey('users.id'))
    published_by = Column(Integer, ForeignKey('users.id'))
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    validated_at = Column(DateTime(timezone=True))
    published_at = Column(DateTime(timezone=True))
    parent_run_id = Column(Integer, ForeignKey('tt_runs.id'))
    __table_args__ = (UniqueConstraint('id', 'term_id', 'dept_code', name='uq_tt_run_scope'),
                      CheckConstraint("status IN ('DRAFT','GENERATING','COMPLETE','PARTIAL','FAILED','PUBLISHED','ARCHIVED')", name='ck_tt_run_status'),
                      Index('uq_tt_published_scope', 'term_id', 'dept_code', unique=True,
                            postgresql_where=text("status = 'PUBLISHED'"), sqlite_where=text("status = 'PUBLISHED'")))


class Entry(Base):
    __tablename__ = 'tt_entries'
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey('tt_runs.id'), nullable=False)
    term_id = Column(Integer, ForeignKey('tt_terms.id'), nullable=False)
    dept_code = Column(String(8), ForeignKey('departments.code'), nullable=False)
    section_id = Column(Integer, ForeignKey('tt_sections.id'), nullable=False)
    requirement_id = Column(Integer, ForeignKey('tt_requirements.id'), nullable=False)
    subject_code = Column(String(16), ForeignKey('subjects.code'), nullable=False)
    faculty_id = Column(Integer, ForeignKey('faculty.id'), nullable=False)
    room_id = Column(Integer, ForeignKey('tt_rooms.id'), nullable=False)
    occurrence = Column(Integer, nullable=False)
    day_of_week = Column(Integer, nullable=False)
    period_index = Column(Integer, nullable=False)
    locked = Column(Boolean, nullable=False, default=False)
    __table_args__ = (
        ForeignKeyConstraint(['run_id','term_id','dept_code'], ['tt_runs.id','tt_runs.term_id','tt_runs.dept_code'], name='fk_tt_entry_run_scope'),
        ForeignKeyConstraint(['section_id','term_id','dept_code'], ['tt_sections.id','tt_sections.term_id','tt_sections.dept_code'], name='fk_tt_entry_section_scope'),
        ForeignKeyConstraint(['term_id','day_of_week','period_index'], ['tt_periods.term_id','tt_periods.day_of_week','tt_periods.period_index'], name='fk_tt_entry_period'),
        UniqueConstraint('run_id','section_id','day_of_week','period_index', name='uq_tt_entry_section'),
        UniqueConstraint('run_id','faculty_id','day_of_week','period_index', name='uq_tt_entry_faculty'),
        UniqueConstraint('run_id','room_id','day_of_week','period_index', name='uq_tt_entry_room'),
        CheckConstraint('occurrence >= 0', name='ck_tt_entry_occurrence'))


class Audit(Base):
    __tablename__ = 'tt_audit'
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey('tt_runs.id'))
    term_id = Column(Integer, ForeignKey('tt_terms.id'))
    dept_code = Column(String(8), ForeignKey('departments.code'))
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    event = Column(String(48), nullable=False)
    detail = Column(Text, nullable=False, default='{}')
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class OperationPreview(Base):
    """Short-lived capability created by a validated timetable preview."""
    __tablename__ = 'tt_operation_previews'
    id = Column(String(36), primary_key=True)
    correlation_id = Column(String(36), nullable=False, unique=True)
    action = Column(String(48), nullable=False)
    state = Column(String(16), nullable=False, default='PREVIEW')
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    dept_code = Column(String(8), ForeignKey('departments.code'), nullable=False)
    term_id = Column(Integer, ForeignKey('tt_terms.id'))
    run_id = Column(Integer, ForeignKey('tt_runs.id'))
    request_json = Column(Text, nullable=False)
    preview_json = Column(Text, nullable=False)
    before_json = Column(Text, nullable=False, default='{}')
    token_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    confirmed_at = Column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("state IN ('PREVIEW','CONFIRMED','EXPIRED')", name='ck_tt_operation_preview_state'),
        Index('ix_tt_operation_actor_created', 'actor_id', 'created_at'),
        Index('ix_tt_operation_scope_state', 'dept_code', 'state'),
    )


class OperationEvent(Base):
    """Append-only security audit for previews and their confirmed effects."""
    __tablename__ = 'tt_operation_events'
    id = Column(Integer, primary_key=True)
    correlation_id = Column(String(36), nullable=False)
    preview_id = Column(String(36), ForeignKey('tt_operation_previews.id'), nullable=False)
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    action = Column(String(48), nullable=False)
    phase = Column(String(16), nullable=False)
    dept_code = Column(String(8), ForeignKey('departments.code'), nullable=False)
    requested_action = Column(Text, nullable=False)
    affected_records = Column(Text, nullable=False, default='[]')
    before_summary = Column(Text, nullable=False, default='{}')
    after_summary = Column(Text, nullable=False, default='{}')
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (
        CheckConstraint("phase IN ('PREVIEWED','CONFIRMED','REJECTED','EXPIRED')", name='ck_tt_operation_event_phase'),
        UniqueConstraint('preview_id', 'phase', name='uq_tt_operation_event_phase'),
        Index('ix_tt_operation_event_correlation', 'correlation_id'),
        Index('ix_tt_operation_event_scope_created', 'dept_code', 'created_at'),
    )


@event.listens_for(OperationEvent, 'before_update')
@event.listens_for(OperationEvent, 'before_delete')
def _operation_event_is_append_only(*_):
    raise ValueError('Timetable operation events are append-only.')


class OccurrenceChange(Base):
    """Immutable dated exception over a published weekly timetable entry."""
    __tablename__ = 'tt_occurrence_changes'
    id = Column(Integer, primary_key=True)
    timetable_entry_id = Column(Integer, ForeignKey('tt_entries.id'), nullable=False)
    timetable_run_id = Column(Integer, ForeignKey('tt_runs.id'), nullable=False)
    term_id = Column(Integer, ForeignKey('tt_terms.id'), nullable=False)
    dept_code = Column(String(8), ForeignKey('departments.code'), nullable=False)
    occurrence_date = Column(Date, nullable=False)
    action = Column(String(16), nullable=False)
    replacement_date = Column(Date)
    replacement_period_index = Column(Integer)
    replacement_room_id = Column(Integer, ForeignKey('tt_rooms.id'))
    correlation_id = Column(String(36), nullable=False)
    applied_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    applied_at = Column(DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (
        CheckConstraint("action IN ('RESCHEDULED','CANCELLED')", name='ck_tt_occurrence_change_action'),
        CheckConstraint("(action = 'CANCELLED' AND replacement_date IS NULL AND replacement_period_index IS NULL AND replacement_room_id IS NULL) OR (action = 'RESCHEDULED' AND replacement_date IS NOT NULL AND replacement_period_index IS NOT NULL AND replacement_room_id IS NOT NULL)", name='ck_tt_occurrence_change_target'),
        UniqueConstraint('timetable_entry_id', 'occurrence_date', name='uq_tt_occurrence_change_source'),
        Index('ix_tt_occurrence_change_correlation', 'correlation_id'),
        Index('ix_tt_occurrence_change_target', 'replacement_date', 'replacement_period_index'),
        Index('uq_tt_occurrence_change_entry_target_date', 'timetable_entry_id',
              'replacement_date', unique=True,
              postgresql_where=text("action = 'RESCHEDULED'"),
              sqlite_where=text("action = 'RESCHEDULED'")),
        Index('ix_tt_occurrence_change_scope_date', 'dept_code', 'occurrence_date'),
    )
