"""JWT-authorized academic timetable endpoints. All GET handlers are read-only."""
import datetime as dt
import logging
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session
from ..auth import require_role
from ..database import get_session
from ..models import Department, Faculty, Subject, TeachingAssignment, User
from . import contracts as c, models as m, service as s
from .bootstrap import BootstrapOptions, controlled_bootstrap
from .runner import run_solver, SolverBusy, SolverTimeout

router = APIRouter(prefix='/api', tags=['academic timetable'])
hod = require_role('hod')
admin = require_role('admin')
reviewer = require_role('hod', 'admin', 'principal')
faculty_role = require_role('faculty', 'hod')


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TermBody(Body):
    name: str = Field(min_length=1, max_length=100)
    starts_on: dt.date
    ends_on: dt.date

    @model_validator(mode='after')
    def dates(self):
        if self.ends_on < self.starts_on or (self.ends_on-self.starts_on).days > 366:
            raise ValueError('A term must last between 1 and 367 days.')
        return self


class PeriodBody(Body):
    day_of_week: int = Field(ge=0, le=6)
    period_index: int = Field(ge=0, le=23)
    starts_at: dt.time
    ends_at: dt.time
    is_break: bool = False
    is_closed: bool = False

    @model_validator(mode='after')
    def timings(self):
        if (self.starts_at.tzinfo or self.ends_at.tzinfo or self.starts_at >= self.ends_at
            or self.starts_at.second or self.ends_at.second or self.starts_at.microsecond or self.ends_at.microsecond):
            raise ValueError('Use ordered local institution times with minute precision.')
        return self


class PeriodsBody(Body):
    periods: list[PeriodBody] = Field(min_length=1, max_length=168)


class HolidayBody(Body):
    date: dt.date
    label: str = Field(min_length=1, max_length=100)


class RoomBody(Body):
    name: str = Field(min_length=1, max_length=64)
    dept_code: str = Field(min_length=1, max_length=8)
    kind: str = Field(min_length=1, max_length=32)
    capacity: int = Field(gt=0, le=10000)


class SectionBody(Body):
    year: int = Field(ge=1, le=4)
    semester: int = Field(ge=1, le=8)
    name: str = Field(pattern=r'^[A-Z0-9]{1,4}$')
    size: int = Field(gt=0, le=10000)

    @model_validator(mode='after')
    def semester_year(self):
        if (self.semester+1)//2 != self.year:
            raise ValueError('Semester must belong to the selected year.')
        return self


class RequirementBody(Body):
    section_id: int
    assignment_id: int
    periods_per_week: int = Field(gt=0, le=168)
    max_per_day: int = Field(gt=0, le=24)
    block_length: int = Field(gt=0, le=24, default=1)
    room_type: str = Field(min_length=1, max_length=32, default='classroom')
    preferred_room_type: str | None = Field(default=None, max_length=32)
    priority: int = Field(ge=0, le=10, default=1)

    @model_validator(mode='after')
    def block(self):
        if self.periods_per_week % self.block_length or self.block_length > self.max_per_day:
            raise ValueError('Weekly demand must divide into complete blocks within the daily limit.')
        return self


class StaffingBody(Body):
    subject_code: str
    faculty_id: int
    year: int = Field(ge=1, le=4)
    section: str = Field(pattern=r'^[A-Z0-9]{1,4}$')


class QualificationBody(Body):
    faculty_id: int
    subject_code: str


class LimitsBody(Body):
    faculty_id: int
    daily_limit: int = Field(gt=0, le=24)
    weekly_limit: int = Field(gt=0, le=168)


class AvailabilityBody(Body):
    unavailable_period_ids: list[int] = Field(max_length=168)


class GenerateBody(Body):
    seed: int = Field(default=7, ge=0, le=2147483647)
    max_steps: int = Field(default=500000, ge=100, le=2000000)
    restarts: int = Field(default=2, ge=0, le=5)
    optimize_passes: int = Field(default=100, ge=0, le=2000)
    parent_run_id: int | None = None


class LockBody(Body):
    locked: bool


class BootstrapBody(Body):
    apply: bool = False
    confirm_apply: bool = False
    preview_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    department: str | None = Field(default=None, min_length=1, max_length=8)
    term_id: int | None = Field(default=None, ge=1)
    fixed_weekly_periods: int | None = Field(default=None, ge=1, le=168)
    use_subject_credits: bool = False
    max_per_day: int = Field(default=2, ge=1, le=24)
    block_length: int = Field(default=1, ge=1, le=24)
    room_type: str = Field(default='classroom', min_length=1, max_length=32)
    faculty_daily_limit: int | None = Field(default=None, ge=1, le=24)
    faculty_weekly_limit: int | None = Field(default=None, ge=1, le=168)
    create_placeholder_rooms: bool = False
    confirm_placeholder_rooms: bool = False

    @model_validator(mode='after')
    def explicit_choices(self):
        if self.apply and not self.confirm_apply:
            raise ValueError('Applying bootstrap changes requires explicit confirmation.')
        if self.apply and not self.preview_hash:
            raise ValueError('Applying bootstrap changes requires the latest dry-run preview hash.')
        if (self.faculty_daily_limit is None) != (self.faculty_weekly_limit is None):
            raise ValueError('Faculty daily and weekly limits must be supplied together.')
        if self.create_placeholder_rooms and not self.confirm_placeholder_rooms:
            raise ValueError('Placeholder rooms require explicit confirmation.')
        return self


def row(obj):
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


def department_faculty(db, user, faculty_id):
    f = db.get(Faculty, faculty_id)
    if f is None or f.dept_code != user.dept_code:
        raise HTTPException(404, 'Faculty not found.')
    return f


def period_ids(db, term_id, ids):
    valid = {p.id for p in db.query(m.PeriodDefinition).filter_by(term_id=term_id)}
    if not set(ids) <= valid:
        raise HTTPException(404, 'Period not found in this term.')
    return set(ids)


@router.get('/timetable/terms')
def terms(user: User = Depends(require_role('student', 'faculty', 'hod', 'admin', 'principal')), db: Session = Depends(get_session)):
    return [row(t) for t in db.query(m.Term).order_by(m.Term.starts_on.desc())]


@router.post('/admin/timetable/terms')
def create_term(body: TermBody, user: User = Depends(admin), db: Session = Depends(get_session)):
    with s.atomic(db):
        if db.query(m.Term).filter(m.Term.starts_on <= body.ends_on, m.Term.ends_on >= body.starts_on).first():
            raise HTTPException(409, 'Academic term dates must not overlap another term.')
        obj = m.Term(**body.model_dump())
        db.add(obj)
        db.flush()
        s.audit(db, user, 'timetable.term_configured', term_id=obj.id)
    return row(obj)


@router.get('/admin/timetable/configuration')
def institution_config(user: User = Depends(admin), db: Session = Depends(get_session)):
    return {'departments': [row(d) for d in db.query(Department).order_by(Department.code)],
            'rooms': [row(r) for r in db.query(m.Room).order_by(m.Room.name)]}


@router.get('/timetable/terms/{term_id}/periods')
def get_periods(term_id: int, user: User = Depends(require_role('faculty', 'hod', 'admin', 'principal')), db: Session = Depends(get_session)):
    s.term(db, term_id)
    return {'periods': [row(p) for p in db.query(m.PeriodDefinition).filter_by(term_id=term_id).order_by(m.PeriodDefinition.day_of_week, m.PeriodDefinition.period_index)],
            'holidays': [row(h) for h in db.query(m.Holiday).filter_by(term_id=term_id).order_by(m.Holiday.date)]}


@router.put('/admin/timetable/terms/{term_id}/periods')
def set_periods(term_id: int, body: PeriodsBody, user: User = Depends(admin), db: Session = Depends(get_session)):
    with s.atomic(db):
        s.mutable_term(db, term_id)
        keys = [(p.day_of_week, p.period_index) for p in body.periods]
        if len(set(keys)) != len(keys):
            raise HTTPException(422, 'Period day/index pairs must be unique.')
        for day in range(7):
            ps = sorted((p for p in body.periods if p.day_of_week == day), key=lambda p: p.period_index)
            if any(a.ends_at > b.starts_at for a, b in zip(ps, ps[1:])):
                raise HTTPException(422, 'Period times must follow index order without overlap.')
        old = {(p.day_of_week, p.period_index): p for p in db.query(m.PeriodDefinition).filter_by(term_id=term_id)}
        # Omitted definitions become closed so references and old drafts remain intact.
        for key, obj in old.items():
            if key not in keys:
                obj.is_closed = True
        for p in body.periods:
            obj = old.get((p.day_of_week, p.period_index))
            if obj is None:
                db.add(m.PeriodDefinition(term_id=term_id, **p.model_dump()))
            else:
                for k, v in p.model_dump().items():
                    setattr(obj, k, v)
        s.audit(db, user, 'timetable.periods_configured', term_id=term_id)
    return get_periods(term_id, user, db)


@router.post('/admin/timetable/terms/{term_id}/holidays')
def holiday(term_id: int, body: HolidayBody, user: User = Depends(admin), db: Session = Depends(get_session)):
    with s.atomic(db):
        s.mutable_term(db, term_id)
        t = s.term(db, term_id)
        if not t.starts_on <= body.date <= t.ends_on:
            raise HTTPException(422, 'Holiday must fall within the academic term.')
        obj = db.query(m.Holiday).filter_by(term_id=term_id, date=body.date).first()
        if obj:
            obj.label = body.label
        else:
            obj = m.Holiday(term_id=term_id, **body.model_dump())
            db.add(obj)
        s.audit(db, user, 'timetable.holiday_configured', term_id=term_id)
    return row(obj)


@router.post('/admin/timetable/rooms')
def create_room(body: RoomBody, user: User = Depends(admin), db: Session = Depends(get_session)):
    with s.atomic(db):
        if not db.get(Department, body.dept_code):
            raise HTTPException(404, 'Department not found.')
        obj = m.Room(**body.model_dump())
        db.add(obj)
        s.audit(db, user, 'timetable.room_configured', dept=body.dept_code)
    return row(obj)


@router.get('/hod/timetable/terms/{term_id}/configuration')
def configuration(term_id: int, user: User = Depends(hod), db: Session = Depends(get_session)):
    s.scope(user, user.dept_code, write=True)
    s.term(db, term_id)
    sections = db.query(m.Section).filter_by(term_id=term_id, dept_code=user.dept_code).order_by(m.Section.id).all()
    faculty = db.query(Faculty).filter_by(dept_code=user.dept_code).order_by(Faculty.id).all()
    return {'sections': [row(o) for o in sections],
            'requirements': [row(o) for o in db.query(m.Requirement).filter(m.Requirement.section_id.in_([x.id for x in sections])).order_by(m.Requirement.id)],
            'assignments': [row(o) for o in db.query(TeachingAssignment).filter_by(dept_code=user.dept_code).order_by(TeachingAssignment.id)],
            'faculty': [row(o) for o in faculty],
            'subjects': [row(o) for o in db.query(Subject).filter_by(dept_code=user.dept_code).order_by(Subject.code)],
            'rooms': [dict(row(o), unavailable_period_ids=[a.period_id for a in db.query(m.RoomUnavailable).join(m.PeriodDefinition, m.RoomUnavailable.period_id == m.PeriodDefinition.id).filter(m.RoomUnavailable.room_id == o.id, m.PeriodDefinition.term_id == term_id)]) for o in db.query(m.Room).filter_by(dept_code=user.dept_code).order_by(m.Room.id)],
            'qualifications': [row(o) for o in db.query(m.Qualification).filter(m.Qualification.faculty_id.in_([f.id for f in faculty]))],
            'limits': [row(o) for o in db.query(m.FacultyLimit).filter(m.FacultyLimit.term_id == term_id, m.FacultyLimit.faculty_id.in_([f.id for f in faculty]))],
            **get_periods(term_id, user, db)}


@router.post('/timetable/terms/{term_id}/bootstrap')
def bootstrap_configuration(term_id: int, body: BootstrapBody,
                            user: User = Depends(require_role('hod', 'admin')),
                            db: Session = Depends(get_session)):
    """Preview first, then explicitly import authoritative academic mappings."""
    if body.term_id is not None and body.term_id != term_id:
        raise HTTPException(422, 'The request term does not match the timetable URL.')
    requested = body.department.upper() if body.department else None
    if user.role == 'hod':
        if requested and requested != user.dept_code:
            raise HTTPException(404, 'Timetable scope not found.')
        departments = [user.dept_code]
    else:
        departments = [requested] if requested else None
    options = BootstrapOptions(weekly_periods=body.fixed_weekly_periods,
        use_subject_credits=body.use_subject_credits, max_per_day=body.max_per_day,
        block_length=body.block_length, room_type=body.room_type,
        faculty_daily_limit=body.faculty_daily_limit,
        faculty_weekly_limit=body.faculty_weekly_limit,
        create_placeholder_rooms=body.create_placeholder_rooms,
        confirm_placeholder_rooms=body.confirm_placeholder_rooms)
    if not body.apply:
        with db.no_autoflush:
            return controlled_bootstrap(db, term_id, departments=departments,
                                        apply=False, options=options)
    with s.atomic(db):
        preview = controlled_bootstrap(db, term_id, departments=departments,
                                       apply=False, options=options)
        if preview['preview_hash'] != body.preview_hash:
            raise HTTPException(409, 'Bootstrap inputs changed. Run a new dry-run preview before applying.')
        result = controlled_bootstrap(db, term_id, departments=departments,
                                      apply=True, options=options)
        s.audit(db, user, 'timetable.bootstrap_applied', term_id=term_id,
                dept=user.dept_code if user.role == 'hod' else requested,
                detail={'departments': [d['code'] for d in result['departments']],
                        'created': result['created']})
    return result


@router.post('/hod/timetable/terms/{term_id}/sections')
def section(term_id: int, body: SectionBody, user: User = Depends(hod), db: Session = Depends(get_session)):
    with s.atomic(db):
        s.mutable_term(db, term_id)
        obj = db.query(m.Section).filter_by(term_id=term_id, dept_code=user.dept_code, year=body.year, semester=body.semester, name=body.name).first()
        if obj:
            obj.size = body.size
        else:
            obj = m.Section(term_id=term_id, dept_code=user.dept_code, **body.model_dump())
            db.add(obj)
        s.audit(db, user, 'timetable.section_configured', term_id=term_id, dept=user.dept_code)
    return row(obj)


@router.post('/hod/timetable/assignments')
def staffing(body: StaffingBody, user: User = Depends(hod), db: Session = Depends(get_session)):
    with s.atomic(db):
        department_faculty(db, user, body.faculty_id)
        subject = db.get(Subject, body.subject_code)
        if not subject or subject.dept_code != user.dept_code or (subject.semester+1)//2 != body.year:
            raise HTTPException(404, 'Subject not found for this department and year.')
        obj = db.query(TeachingAssignment).filter_by(dept_code=user.dept_code, subject_code=body.subject_code, year=body.year, section=body.section).first()
        if obj and obj.faculty_id != body.faculty_id:
            referenced = db.query(m.Entry).join(m.Run, m.Entry.run_id == m.Run.id).join(m.Requirement, m.Entry.requirement_id == m.Requirement.id).filter(m.Requirement.assignment_id == obj.id, m.Run.status.in_(['PUBLISHED','ARCHIVED'])).first()
            if referenced:
                raise HTTPException(409, 'This teaching assignment has published history and cannot be reassigned.')
            obj.faculty_id = body.faculty_id
        elif not obj:
            obj = TeachingAssignment(dept_code=user.dept_code, **body.model_dump())
            db.add(obj)
        s.audit(db, user, 'timetable.assignment_configured', dept=user.dept_code)
    return row(obj)


@router.post('/hod/timetable/qualifications')
def qualification(body: QualificationBody, user: User = Depends(hod), db: Session = Depends(get_session)):
    with s.atomic(db):
        department_faculty(db, user, body.faculty_id)
        subject = db.get(Subject, body.subject_code)
        if not subject or subject.dept_code != user.dept_code:
            raise HTTPException(404, 'Subject not found.')
        obj = db.get(m.Qualification, (body.faculty_id, body.subject_code))
        if not obj:
            obj = m.Qualification(**body.model_dump())
            db.add(obj)
        s.audit(db, user, 'timetable.qualification_configured', dept=user.dept_code)
    return row(obj)


@router.put('/hod/timetable/terms/{term_id}/faculty-limits')
def limits(term_id: int, body: LimitsBody, user: User = Depends(hod), db: Session = Depends(get_session)):
    with s.atomic(db):
        s.mutable_term(db, term_id)
        department_faculty(db, user, body.faculty_id)
        obj = db.get(m.FacultyLimit, (body.faculty_id, term_id))
        if not obj:
            obj = m.FacultyLimit(term_id=term_id, **body.model_dump())
            db.add(obj)
        else:
            obj.daily_limit, obj.weekly_limit = body.daily_limit, body.weekly_limit
        s.audit(db, user, 'timetable.load_configured', term_id=term_id, dept=user.dept_code)
    return row(obj)


@router.post('/hod/timetable/terms/{term_id}/requirements')
def requirement(term_id: int, body: RequirementBody, user: User = Depends(hod), db: Session = Depends(get_session)):
    with s.atomic(db):
        s.mutable_term(db, term_id)
        section = db.get(m.Section, body.section_id)
        assignment = db.get(TeachingAssignment, body.assignment_id)
        if not section or section.term_id != term_id or section.dept_code != user.dept_code or not assignment:
            raise HTTPException(404, 'Section or teaching assignment not found.')
        department_faculty(db, user, assignment.faculty_id)
        subject = db.get(Subject, assignment.subject_code)
        if ((assignment.dept_code, assignment.year, assignment.section) != (user.dept_code, section.year, section.name)
            or not subject or subject.dept_code != user.dept_code or subject.semester != section.semester):
            raise HTTPException(404, 'Teaching assignment not found for this section and semester.')
        obj = db.query(m.Requirement).filter_by(section_id=body.section_id, assignment_id=body.assignment_id).first()
        if obj:
            for k, v in body.model_dump().items():
                setattr(obj, k, v)
        else:
            obj = m.Requirement(**body.model_dump())
            db.add(obj)
        s.audit(db, user, 'timetable.requirement_configured', term_id=term_id, dept=user.dept_code)
    return row(obj)


@router.get('/faculty/timetable/terms/{term_id}/availability')
def get_availability(term_id: int, user: User = Depends(faculty_role), db: Session = Depends(get_session)):
    if not user.faculty_id:
        raise HTTPException(404, 'Faculty profile not found.')
    department_faculty(db, user, user.faculty_id)
    ps = get_periods(term_id, user, db)
    valid = {p['id'] for p in ps['periods']}
    return {**ps, 'unavailable_period_ids': sorted(a.period_id for a in db.query(m.FacultyUnavailable).filter_by(faculty_id=user.faculty_id) if a.period_id in valid)}


@router.put('/faculty/timetable/terms/{term_id}/availability')
def set_availability(term_id: int, body: AvailabilityBody, user: User = Depends(faculty_role), db: Session = Depends(get_session)):
    with s.atomic(db):
        s.mutable_term(db, term_id)
        if not user.faculty_id:
            raise HTTPException(404, 'Faculty profile not found.')
        department_faculty(db, user, user.faculty_id)
        selected = period_ids(db, term_id, body.unavailable_period_ids)
        valid = [p.id for p in db.query(m.PeriodDefinition).filter_by(term_id=term_id)]
        db.query(m.FacultyUnavailable).filter(m.FacultyUnavailable.faculty_id == user.faculty_id, m.FacultyUnavailable.period_id.in_(valid)).delete(synchronize_session=False)
        db.add_all(m.FacultyUnavailable(faculty_id=user.faculty_id, period_id=p) for p in sorted(selected))
        s.audit(db, user, 'timetable.faculty_availability', term_id=term_id, dept=user.dept_code)
    return get_availability(term_id, user, db)


@router.put('/timetable/terms/{term_id}/rooms/{room_id}/availability')
def room_availability(term_id: int, room_id: int, body: AvailabilityBody, user: User = Depends(require_role('hod', 'admin')), db: Session = Depends(get_session)):
    with s.atomic(db):
        s.mutable_term(db, term_id)
        room = db.get(m.Room, room_id)
        if not room or (user.role == 'hod' and room.dept_code != user.dept_code):
            raise HTTPException(404, 'Room not found.')
        selected = period_ids(db, term_id, body.unavailable_period_ids)
        valid = [p.id for p in db.query(m.PeriodDefinition).filter_by(term_id=term_id)]
        db.query(m.RoomUnavailable).filter(m.RoomUnavailable.room_id == room_id, m.RoomUnavailable.period_id.in_(valid)).delete(synchronize_session=False)
        db.add_all(m.RoomUnavailable(room_id=room_id, period_id=p) for p in sorted(selected))
        s.audit(db, user, 'timetable.room_availability', term_id=term_id, dept=room.dept_code)
    return {'room_id': room_id, 'unavailable_period_ids': sorted(selected)}


@router.post('/hod/timetable/terms/{term_id}/preflight')
def readiness(term_id: int, user: User = Depends(hod), db: Session = Depends(get_session)):
    data, bundle, issues = s.snapshot(db, term_id, user.dept_code)
    return {'ready': not issues, 'issues': [i.model_dump() for i in issues], 'input_hash': s.digest(bundle),
            'required_periods': sum(r.periods for r in data.requirements), 'sections': len(data.sections)}


@router.post('/hod/timetable/terms/{term_id}/runs')
def generate(term_id: int, body: GenerateBody, user: User = Depends(hod), db: Session = Depends(get_session)):
    # A synchronous FastAPI handler runs in its worker thread, not the async event loop.
    locked, fingerprint = (), None
    if body.parent_run_id:
        parent = s.get_run(db, user, body.parent_run_id, write=True)
        if parent.term_id != term_id or parent.status not in s.EDITABLE:
            raise HTTPException(409, 'Choose an editable source draft from this academic term.')
        entries = tuple(map(s.entry_contract, s.run_entries(db, parent)))
        fingerprint = s.digest([e.model_dump() for e in entries])
        locked = tuple(e for e in entries if e.locked)
    data, bundle, issues = s.snapshot(db, term_id, user.dept_code, locked)
    if issues:
        raise HTTPException(422, {'message': 'Timetable configuration is not ready.', 'issues': [i.model_dump() for i in issues]})
    # Close the read transaction while computing; persistence rechecks the hash under lock.
    dept, creator_id = user.dept_code, user.id
    db.rollback()
    try:
        result, elapsed = run_solver(data, **body.model_dump(exclude={'parent_run_id'}))
    except (SolverBusy, SolverTimeout) as exc:
        raise HTTPException(503, str(exc)) from None
    except Exception:
        logging.getLogger(__name__).exception('Timetable worker failed')
        raise HTTPException(500, 'Timetable computation failed. No draft was saved.') from None
    user = db.get(User, creator_id)
    if not user or user.role != 'hod' or user.dept_code != dept:
        raise HTTPException(403, 'Timetable authorization changed. No draft was saved.')
    return s.persist(db, user, term_id, dept, body.seed, data, bundle, result, elapsed, body.parent_run_id, fingerprint)


@router.get('/timetable/runs')
def history(term_id: int | None = None, user: User = Depends(reviewer), db: Session = Depends(get_session)):
    query = db.query(m.Run)
    if user.role == 'hod':
        query = query.filter_by(dept_code=user.dept_code)
    if term_id is not None:
        query = query.filter_by(term_id=term_id)
    return [s.describe_run(db, r, entries=False) for r in query.order_by(m.Run.id.desc()).limit(200)]


@router.get('/timetable/runs/{run_id}')
def read_run(run_id: int, user: User = Depends(reviewer), db: Session = Depends(get_session)):
    return s.describe_run(db, s.get_run(db, user, run_id))


@router.post('/hod/timetable/runs/{run_id}/validate')
def validate_run(run_id: int, user: User = Depends(hod), db: Session = Depends(get_session)):
    return s.validate_run(db, user, run_id)


@router.patch('/hod/timetable/runs/{run_id}/entries/{entry_id}/lock')
def lock_entry(run_id: int, entry_id: int, body: LockBody, user: User = Depends(hod), db: Session = Depends(get_session)):
    return s.lock_entry(db, user, run_id, entry_id, body.locked)


@router.post('/hod/timetable/runs/{run_id}/publish')
def publish_run(run_id: int, user: User = Depends(hod), db: Session = Depends(get_session)):
    # Kept as a controlled compatibility boundary: publication now requires a
    # short-lived preview capability through /timetable/operations.
    s.get_run(db, user, run_id, write=True)
    raise HTTPException(409, 'Create and explicitly confirm a publication preview in Timetable Operations.')


@router.get('/principal/timetable/overview')
def overview(user: User = Depends(require_role('principal', 'admin')), db: Session = Depends(get_session)):
    results = []
    for term in db.query(m.Term).order_by(m.Term.starts_on.desc()):
        for department in db.query(Department).order_by(Department.code):
            runs = db.query(m.Run).filter_by(term_id=term.id, dept_code=department.code).order_by(m.Run.id.desc()).all()
            published = next((r for r in runs if r.status == 'PUBLISHED'), None)
            results.append({'term_id': term.id, 'term': term.name, 'department': department.code,
                            'published_run_id': published.id if published else None,
                            'published_metrics': json.loads(published.metrics) if published else None,
                            'latest_run_id': runs[0].id if runs else None,
                            'latest_status': runs[0].status if runs else 'UNCONFIGURED'})
    return results
