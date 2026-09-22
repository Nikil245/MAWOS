"""Scope checks, snapshot construction and atomic version workflow."""
from contextlib import contextmanager
import hashlib
import json
import logging
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from ..models import Department, Faculty, Student, Subject, TeachingAssignment
from ..notifications import notify_role
from . import contracts as c, models as m
from .validation import preflight, validate

log = logging.getLogger(__name__)
EDITABLE = {'DRAFT', 'COMPLETE', 'PARTIAL', 'FAILED'}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def scope(user, dept, *, write=False):
    if user.role in ('principal', 'admin'):
        return
    if user.role != 'hod' or user.dept_code != dept:
        raise HTTPException(404, 'Timetable scope not found.')


def term(db, term_id):
    obj = db.get(m.Term, term_id)
    if obj is None:
        raise HTTPException(404, 'Academic term not found.')
    return obj


def get_run(db, user, run_id, *, write=False):
    obj = db.query(m.Run).filter_by(id=run_id).populate_existing().first()
    if obj is None:
        raise HTTPException(404, 'Timetable run not found.')
    scope(user, obj.dept_code, write=write)
    return obj


@contextmanager
def atomic(db):
    """Serialize timetable mutations across all API workers; commit exactly once.

    The lock also serializes configuration changes with final publication checks.
    It is held only for DB work, never for CPU solving.
    """
    try:
        acquire_mutation_lock(db)
        yield
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        log.exception('Timetable transaction failed')
        raise HTTPException(409, 'Timetable could not be saved. Refresh and retry.') from None
    except Exception:
        db.rollback()
        log.exception('Timetable operation failed')
        raise HTTPException(500, 'Timetable operation failed. Retry or contact an administrator.') from None


def acquire_mutation_lock(db):
    """Serialize a caller-managed timetable mutation transaction."""
    if db.bind.dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(734091208)'))


def audit(db, user, event, *, run=None, term_id=None, dept=None, detail=None):
    db.add(m.Audit(actor_id=user.id, event=event, run_id=run.id if run else None,
                   term_id=run.term_id if run else term_id, dept_code=run.dept_code if run else dept,
                   detail=encoded(detail or {})))


def mutable_term(db, term_id):
    term(db, term_id)
    if db.query(m.Run).filter(m.Run.term_id == term_id, m.Run.status.in_(['PUBLISHED', 'ARCHIVED'])).first():
        raise HTTPException(409, 'This term has publication history. Configure a new term to change scheduling inputs.')


def entry_contract(e):
    return c.Entry(requirement_id=e.requirement_id, occurrence=e.occurrence, section_id=e.section_id,
                   subject=e.subject_code, faculty_id=e.faculty_id, room_id=e.room_id,
                   day=e.day_of_week, period_index=e.period_index, locked=e.locked)


def run_entries(db, run):
    return db.query(m.Entry).filter_by(run_id=run.id).order_by(m.Entry.id).all()


def snapshot(db, term_id, dept, locked=()):
    academic_term = term(db, term_id)
    if db.get(Department, dept) is None:
        raise HTTPException(404, 'Department not found.')
    periods = db.query(m.PeriodDefinition).filter_by(term_id=term_id).order_by(m.PeriodDefinition.day_of_week, m.PeriodDefinition.period_index).all()
    section_rows = db.query(m.Section).filter_by(term_id=term_id, dept_code=dept).order_by(m.Section.id).all()
    section_map = {s.id: s for s in section_rows}
    reqs = db.query(m.Requirement).filter(m.Requirement.section_id.in_(section_map)).order_by(m.Requirement.id).all()
    faculty = db.query(Faculty).filter_by(dept_code=dept).order_by(Faculty.id).all()
    rooms = db.query(m.Room).filter_by(dept_code=dept).order_by(m.Room.id).all()
    period_map = {p.id: (p.day_of_week, p.period_index) for p in periods}
    errors = []
    def bad(code, message, rid=None):
        errors.append(c.Issue(code=code, message=message, requirement_id=rid))
    converted = []
    assignment_ids = set()
    for r in reqs:
        s = section_map[r.section_id]
        a = db.get(TeachingAssignment, r.assignment_id)
        subject = db.get(Subject, a.subject_code) if a else None
        teacher = db.get(Faculty, a.faculty_id) if a else None
        if (not a or not subject or not teacher or
            (a.dept_code, a.year, a.section) != (dept, s.year, s.name) or
            subject.dept_code != dept or subject.semester != s.semester or teacher.dept_code != dept):
            bad('assignment_scope', 'Review assignment: faculty, subject, semester and section must belong to this department.', r.id)
            continue
        assignment_ids.add((s.id, a.id))
        converted.append(c.Requirement(id=r.id, section_id=s.id, subject=a.subject_code, faculty_id=a.faculty_id,
                         periods=r.periods_per_week, max_per_day=r.max_per_day, block_length=r.block_length,
                         room_type=r.room_type, preferred_room_type=r.preferred_room_type, priority=r.priority))
    for s in section_rows:
        expected = (db.query(TeachingAssignment).join(Subject, TeachingAssignment.subject_code == Subject.code)
                    .filter(TeachingAssignment.dept_code == dept, TeachingAssignment.year == s.year,
                            TeachingAssignment.section == s.name, Subject.semester == s.semester).all())
        if not expected:
            bad('missing_assignments', f'Configure teaching assignments for year {s.year}, semester {s.semester}, section {s.name}.')
        for a in expected:
            if (s.id, a.id) not in assignment_ids:
                bad('missing_requirement', f'Configure weekly requirements for assignment {a.id} in section {s.id}.')
    cohorts = db.query(Student.year, Student.semester, Student.section).filter_by(dept_code=dept, status='enrolled').distinct().all()
    configured = {(s.year, s.semester, s.name) for s in section_rows}
    for cohort in cohorts:
        if tuple(cohort) not in configured:
            bad('missing_section', f'Configure enrolled year {cohort.year}, semester {cohort.semester}, section {cohort.section}.')
    for s in section_rows:
        enrolled = db.query(Student).filter_by(dept_code=dept, year=s.year, semester=s.semester, section=s.name, status='enrolled').count()
        if s.size < enrolled:
            bad('section_size', f'Section {s.id} size must cover its {enrolled} enrolled students.')
    teacher_contracts = []
    assigned = {r.faculty_id for r in converted}
    for f in faculty:
        limits = db.get(m.FacultyLimit, (f.id, term_id))
        if not limits:
            if f.id in assigned:
                bad('missing_limits', f'Configure daily and weekly load limits for faculty {f.id}.')
            continue
        qualifications = db.query(m.Qualification).filter_by(faculty_id=f.id).order_by(m.Qualification.subject_code).all()
        unavailable = db.query(m.FacultyUnavailable).filter_by(faculty_id=f.id).all()
        teacher_contracts.append(c.Teacher(id=f.id, daily_limit=limits.daily_limit, weekly_limit=limits.weekly_limit,
                                 subjects=tuple(q.subject_code for q in qualifications),
                                 unavailable=tuple(sorted(period_map[u.period_id] for u in unavailable if u.period_id in period_map))))
    room_contracts = []
    for room in rooms:
        unavailable = db.query(m.RoomUnavailable).filter_by(room_id=room.id).all()
        room_contracts.append(c.Room(id=room.id, kind=room.kind, capacity=room.capacity,
                              unavailable=tuple(sorted(period_map[u.period_id] for u in unavailable if u.period_id in period_map))))
    data = c.SolverInput(periods=tuple(c.Period(day=p.day_of_week, index=p.period_index,
                         start=p.starts_at.hour*60+p.starts_at.minute, end=p.ends_at.hour*60+p.ends_at.minute,
                         closed=p.is_break or p.is_closed) for p in periods),
                         sections=tuple(c.Section(id=s.id, size=s.size) for s in section_rows),
                         teachers=tuple(teacher_contracts), rooms=tuple(room_contracts), requirements=tuple(converted), locked=tuple(locked))
    metadata = {'term': {'id': term_id, 'name': academic_term.name, 'starts_on': str(academic_term.starts_on), 'ends_on': str(academic_term.ends_on)},
                'dept': dept, 'sections': {s.id: {'year': s.year, 'semester': s.semester, 'name': s.name} for s in section_rows},
                'faculty': {f.id: f.name for f in faculty}, 'rooms': {r.id: r.name for r in rooms},
                'subjects': {r.subject: db.get(Subject, r.subject).name for r in converted},
                'holidays': [str(h.date) for h in db.query(m.Holiday).filter_by(term_id=term_id).order_by(m.Holiday.date)]}
    bundle = {'data': data.model_dump(mode='json', exclude={'locked'}), 'metadata': metadata}
    errors.extend(preflight(data))
    return data, bundle, errors


def describe_run(db, run, *, entries=True):
    result = {key: getattr(run, key) for key in ('id', 'term_id', 'dept_code', 'status', 'seed', 'input_hash', 'created_by', 'validated_by', 'published_by', 'created_at', 'validated_at', 'published_at', 'parent_run_id')}
    result.update(metrics=json.loads(run.metrics), conflicts=json.loads(run.conflicts), unplaced=json.loads(run.unplaced))
    if entries:
        bundle = json.loads(run.input_snapshot)
        result['configuration'] = bundle
        result['entries'] = [dict(entry_contract(e).model_dump(), id=e.id) for e in run_entries(db, run)]
    return result


def stage_persist(db, user, term_id, dept, seed, data, bundle, result, elapsed_ms,
                  parent_id=None, parent_fingerprint=None, *, initial_draft=False):
    """Stage a generated version in the caller's locked transaction."""
    scope(user, dept, write=True)
    _, current, errors = snapshot(db, term_id, dept)
    if digest(current) != digest(bundle) or errors:
        raise HTTPException(409, 'Configuration changed during generation. Run preflight and generate again.')
    if parent_id:
        parent = get_run(db, user, parent_id, write=True)
        if parent.status not in EDITABLE or digest([e.model_dump() for e in map(entry_contract, run_entries(db, parent))]) != parent_fingerprint:
            raise HTTPException(409, 'Source draft changed during generation. Refresh and retry.')
    hard = validate(data, result.entries)
    partial_hard = validate(data, result.entries, complete=False)
    if partial_hard:
        raise HTTPException(422, 'Generated entries failed independent validation; no run was saved.')
    metrics = {k: getattr(result, k) for k in ('steps', 'search_conflicts', 'backtracks', 'restarts', 'score', 'termination')}
    metrics.update(duration_ms=elapsed_ms, placed=len(result.entries), required=sum(r.periods for r in data.requirements), hard_violations=len(hard))
    # Operation-confirmed generation is deliberately staged as DRAFT.  A
    # separate reviewed validation changes it to COMPLETE/PARTIAL; direct HOD
    # generation retains its established immediately-validated workflow.
    status = 'DRAFT' if initial_draft else ('COMPLETE' if not hard else 'PARTIAL')
    run = m.Run(term_id=term_id, dept_code=dept, status=status, seed=seed,
                input_snapshot=encoded(bundle), input_hash=digest(bundle), metrics=encoded(metrics),
                conflicts=encoded([i.model_dump() for i in hard]), unplaced=encoded([u.model_dump() for u in result.unplaced]),
                created_by=user.id, validated_by=None if initial_draft else user.id,
                validated_at=None if initial_draft else m.now(), parent_run_id=parent_id)
    db.add(run)
    db.flush()
    for e in result.entries:
        db.add(m.Entry(run_id=run.id, term_id=term_id, dept_code=dept, requirement_id=e.requirement_id,
                       occurrence=e.occurrence, section_id=e.section_id, subject_code=e.subject, faculty_id=e.faculty_id,
                       room_id=e.room_id, day_of_week=e.day, period_index=e.period_index, locked=e.locked))
    audit(db, user, 'timetable.generated', run=run, detail=metrics)
    if not initial_draft:
        audit(db, user, 'timetable.validated', run=run, detail={'hard_violations': len(hard)})
    return run


def persist(db, user, term_id, dept, seed, data, bundle, result, elapsed_ms, parent_id=None, parent_fingerprint=None):
    with atomic(db):
        run = stage_persist(db, user, term_id, dept, seed, data, bundle, result,
                            elapsed_ms, parent_id, parent_fingerprint)
    return describe_run(db, run)


def check_run(db, run):
    stored = json.loads(run.input_snapshot)
    # Parent locks are carried as actual locked entries in the version.
    entries = tuple(map(entry_contract, run_entries(db, run)))
    locked = tuple(e for e in entries if e.locked)
    data, current, errors = snapshot(db, run.term_id, run.dept_code, locked)
    if digest(current) != run.input_hash:
        errors.append(c.Issue(code='stale_configuration', message='Configuration changed. Generate a new draft before publishing.'))
    errors.extend(validate(data, entries))
    # Also validate against generation-time inputs so changing configuration cannot legitimize tampering.
    errors.extend(validate(c.SolverInput.model_validate(stored['data']).model_copy(update={'locked': locked}), entries))
    return list({(e.code, e.message, e.requirement_id): e for e in errors}.values())


def stage_validate_run(db, user, run_id):
    """Validate a draft inside a caller-owned mutation transaction."""
    run = get_run(db, user, run_id, write=True)
    if run.status not in EDITABLE:
        raise HTTPException(409, 'Only drafts can be validated for publication.')
    issues = check_run(db, run)
    run.status = 'COMPLETE' if not issues else 'PARTIAL'
    run.conflicts = encoded([i.model_dump() for i in issues])
    run.validated_by, run.validated_at = user.id, m.now()
    metrics = json.loads(run.metrics)
    metrics['hard_violations'] = len(issues)
    run.metrics = encoded(metrics)
    audit(db, user, 'timetable.validated', run=run, detail={'hard_violations': len(issues)})
    return run


def validate_run(db, user, run_id):
    with atomic(db):
        run = stage_validate_run(db, user, run_id)
    return describe_run(db, run)


def lock_entry(db, user, run_id, entry_id, locked):
    with atomic(db):
        run = get_run(db, user, run_id, write=True)
        if run.status not in EDITABLE:
            raise HTTPException(409, 'Published and archived versions are immutable.')
        entries = run_entries(db, run)
        entry = next((e for e in entries if e.id == entry_id), None)
        if entry is None:
            raise HTTPException(404, 'Draft entry not found.')
        data = c.SolverInput.model_validate(json.loads(run.input_snapshot)['data'])
        if validate(data, tuple(map(entry_contract, entries)), complete=False):
            raise HTTPException(409, 'Fix invalid entries before changing locks.')
        for e in entries:
            if (e.requirement_id, e.occurrence) == (entry.requirement_id, entry.occurrence):
                e.locked = locked
        audit(db, user, 'timetable.locked' if locked else 'timetable.unlocked', run=run,
              detail={'requirement_id': entry.requirement_id, 'occurrence': entry.occurrence})
    return describe_run(db, run)


def stage_publish(db, user, run_id):
    """Stage a validated publication in the caller's transaction."""
    run = get_run(db, user, run_id, write=user.role == 'hod')
    if run.status != 'COMPLETE':
        raise HTTPException(409, 'Only a COMPLETE draft can be published.')
    issues = check_run(db, run)
    if issues:
        raise HTTPException(409, 'Publication validation failed. Validate the draft to review conflicts.')
    previous = db.query(m.Run).filter_by(term_id=run.term_id, dept_code=run.dept_code, status='PUBLISHED').all()
    for old in previous:
        old.status = 'ARCHIVED'
        audit(db, user, 'timetable.archived', run=old, detail={'replaced_by': run.id})
    db.flush()  # Archive before assigning the partial unique-index key.
    run.status, run.published_by, run.published_at = 'PUBLISHED', user.id, m.now()
    run.validated_by, run.validated_at = user.id, m.now()
    audit(db, user, 'timetable.published', run=run)
    term_row = term(db, run.term_id)
    common = dict(
        title='Timetable published',
        message=f'{term_row.name} timetable version {run.id} is now published for {run.dept_code}.',
        notification_type='TIMETABLE_PUBLISHED', source_agent='timetable_service',
        event_key=f'timetable_published:{run.id}',
        related_entity_type='timetable_run', related_entity_id=run.id)
    notify_role(db, 'student', dept=run.dept_code, route='/student/timetable', **common)
    notify_role(db, 'faculty', dept=run.dept_code, route='/faculty/timetable', **common)
    return run


def publish(db, user, run_id):
    with atomic(db):
        run = stage_publish(db, user, run_id)
    return describe_run(db, run)
