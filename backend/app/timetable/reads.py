"""Read-only published views and Asia/Kolkata current/next occurrence calculation."""
import datetime as dt
import json
from zoneinfo import ZoneInfo
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from ..auth import require_role
from ..database import get_session
from ..coverage.models import CoverageAssignment, CoverageRequest
from ..models import Faculty, Student, Subject, User
from . import models as m
from .api import router

TZ = ZoneInfo('Asia/Kolkata')
DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def occurrence(entry, date):
    start = dt.datetime.combine(date, dt.time(entry['start_minute']//60, entry['start_minute']%60), TZ)
    end = dt.datetime.combine(date, dt.time(entry['end_minute']//60, entry['end_minute']%60), TZ)
    return {**entry, 'date': str(date), 'starts_at': start.isoformat(), 'ends_at': end.isoformat()}


def temporal(entries, now, *, holidays=(), starts_on=None, ends_on=None, changes=()):
    """Pure boundary logic; continue past weekends and holidays through term end."""
    if now.tzinfo is None:
        raise ValueError('A timezone-aware now is required.')
    now = now.astimezone(TZ)
    start_date = max(now.date(), starts_on or now.date())
    end_date = ends_on or (now.date()+dt.timedelta(days=7))
    current = next_class = None
    today = []
    day = start_date
    holiday_set = set(holidays)
    source_changes = {(item['entry_id'], item['occurrence_date']): item for item in changes}
    while day <= end_date:
        if day not in holiday_set:
            candidates = [e for e in entries if e['day'] == day.weekday()
                          and (e.get('id'), str(day)) not in source_changes]
            candidates.extend(item['replacement'] for item in changes
                              if item.get('replacement') and item['replacement_date'] == str(day))
            for e in sorted(candidates, key=lambda e: e['start_minute']):
                item = occurrence(e, day)
                starts, ends = dt.datetime.fromisoformat(item['starts_at']), dt.datetime.fromisoformat(item['ends_at'])
                if day == now.date():
                    today.append(item)
                if starts <= now < ends:
                    current = item
                elif starts > now and next_class is None:
                    next_class = item
        if next_class is not None:
            break
        day += dt.timedelta(days=1)
    return {'current': current, 'next': next_class, 'today': today}


def published_rows(db, *, dept=None, faculty_id=None, year=None, semester=None, section=None):
    query = db.query(m.Run).filter_by(status='PUBLISHED')
    if dept:
        query = query.filter_by(dept_code=dept)
    results = []
    for run in query.order_by(m.Run.term_id, m.Run.dept_code):
        bundle = json.loads(run.input_snapshot)
        meta = bundle['metadata']
        periods = {(p['day'], p['index']): p for p in bundle['data']['periods']}
        entries = db.query(m.Entry).filter_by(run_id=run.id)
        if faculty_id is not None:
            entries = entries.filter_by(faculty_id=faculty_id)
        values = []
        for e in entries.order_by(m.Entry.day_of_week, m.Entry.period_index):
            sec = meta['sections'][str(e.section_id)]
            if ((year is not None and sec['year'] != year) or (semester is not None and sec['semester'] != semester)
                or (section is not None and sec['name'] != section)):
                continue
            p = periods[e.day_of_week, e.period_index]
            values.append({'id': e.id, 'run_id': run.id, 'subject_code': e.subject_code, 'subject_name': meta['subjects'][e.subject_code],
                           'faculty': meta['faculty'][str(e.faculty_id)], 'room': meta['rooms'][str(e.room_id)],
                           'section': f"{run.dept_code} {sec['year']}{sec['name']} / semester {sec['semester']}",
                           'section_id': e.section_id, 'day': e.day_of_week, 'day_name': DAYS[e.day_of_week],
                           'period_index': e.period_index, 'start_minute': p['start'], 'end_minute': p['end'],
                           'start_time': f"{p['start']//60:02}:{p['start']%60:02}", 'end_time': f"{p['end']//60:02}:{p['end']%60:02}"})
        results.append((run, bundle, values))
    return results


def accepted_coverage_entries(db, faculty_id, now):
    """Dated substitute work for a faculty timetable, never recurring slots."""
    rows = (db.query(CoverageAssignment).join(
        CoverageRequest, CoverageRequest.id == CoverageAssignment.coverage_request_id).filter(
            CoverageAssignment.substitute_faculty_id == faculty_id,
            CoverageAssignment.status == 'ACCEPTED',
            CoverageRequest.status == 'APPROVED',
            CoverageAssignment.occurrence_date >= now.date()).order_by(
                CoverageAssignment.occurrence_date, CoverageAssignment.id).all())
    result = []
    for assignment in rows:
        entry = db.get(m.Entry, assignment.timetable_entry_id)
        term = db.get(m.Term, entry.term_id) if entry else None
        section = db.get(m.Section, entry.section_id) if entry else None
        period = (db.query(m.PeriodDefinition).filter_by(term_id=entry.term_id,
            day_of_week=entry.day_of_week, period_index=entry.period_index).one_or_none()) if entry else None
        subject = db.get(Subject, entry.subject_code) if entry else None
        original = db.get(Faculty, entry.faculty_id) if entry else None
        room = db.get(m.Room, entry.room_id) if entry else None
        if not all((entry, term, section, period, subject)) or not term.starts_on <= assignment.occurrence_date <= term.ends_on:
            continue
        result.append({'id': f'coverage-{assignment.id}', 'coverage_assignment_id': assignment.id,
                       'dated_coverage': True, 'coverage_status': 'ACCEPTED',
                       'date': str(assignment.occurrence_date), 'day': assignment.occurrence_date.weekday(),
                       'day_name': DAYS[assignment.occurrence_date.weekday()], 'period_index': entry.period_index,
                       'subject_code': entry.subject_code, 'subject_name': subject.name,
                       'faculty': original.name if original else 'Original faculty',
                       'room': room.name if room else 'Room',
                       'section': f"{entry.dept_code} {section.year}{section.name} / semester {section.semester}",
                       'start_minute': period.starts_at.hour * 60 + period.starts_at.minute,
                       'end_minute': period.ends_at.hour * 60 + period.ends_at.minute,
                       'start_time': period.starts_at.strftime('%H:%M'),
                       'end_time': period.ends_at.strftime('%H:%M')})
    return result


def view(db, *, now=None, **filters):
    now = now or dt.datetime.now(TZ)
    if now.tzinfo is None:
        raise ValueError('A timezone-aware now is required.')
    now = now.astimezone(TZ)
    weekly, today, current, next_options = [], [], None, []
    active, holiday, published, period_definitions, changes = False, False, False, [], []
    for run, bundle, entries in published_rows(db, **filters):
        meta = bundle['metadata']
        start, end = dt.date.fromisoformat(meta['term']['starts_on']), dt.date.fromisoformat(meta['term']['ends_on'])
        if end < now.date():
            continue
        holidays = [dt.date.fromisoformat(d) for d in meta['holidays']]
        entry_map = {entry['id']: entry for entry in entries}
        rows = (db.query(m.OccurrenceChange).filter(
            m.OccurrenceChange.timetable_run_id == run.id,
            m.OccurrenceChange.timetable_entry_id.in_(entry_map or {-1})).order_by(
                m.OccurrenceChange.occurrence_date, m.OccurrenceChange.id).all())
        run_changes = []
        for change in rows:
            original = entry_map[change.timetable_entry_id]
            replacement = None
            if change.action == 'RESCHEDULED':
                period = next((p for p in bundle['data']['periods']
                               if p['day'] == change.replacement_date.weekday()
                               and p['index'] == change.replacement_period_index), None)
                room = db.get(m.Room, change.replacement_room_id)
                if period:
                    replacement = {**original, 'day': change.replacement_date.weekday(),
                                   'day_name': DAYS[change.replacement_date.weekday()],
                                   'period_index': change.replacement_period_index,
                                   'start_minute': period['start'], 'end_minute': period['end'],
                                   'start_time': f"{period['start']//60:02}:{period['start']%60:02}",
                                   'end_time': f"{period['end']//60:02}:{period['end']%60:02}",
                                   'room': room.name if room else original['room']}
            item = {'id': change.id, 'entry_id': change.timetable_entry_id,
                    'occurrence_date': str(change.occurrence_date), 'status': change.action,
                    'replacement_date': str(change.replacement_date) if change.replacement_date else None,
                    'replacement_period_index': change.replacement_period_index,
                    'subject_code': original['subject_code'], 'section': original['section'],
                    'replacement': replacement}
            run_changes.append(item)
            changes.append({key: value for key, value in item.items() if key != 'replacement'})
        times = temporal(entries, now, holidays=holidays, starts_on=start, ends_on=end,
                         changes=run_changes)
        if times['next']:
            next_options.append(times['next'])
        if start <= now.date() <= end:
            published = True
            weekly.extend(entries)
            period_definitions = bundle['data']['periods']
            today.extend(times['today'])
            current = times['current'] or current
            holiday = now.date() in holidays
            active = True
    coverage_entries = accepted_coverage_entries(db, filters['faculty_id'], now) if filters.get('faculty_id') else []
    for entry in coverage_entries:
        dated = occurrence(entry, dt.date.fromisoformat(entry['date']))
        if dated['date'] == str(now.date()):
            today.append(dated)
            if dt.datetime.fromisoformat(dated['starts_at']) <= now < dt.datetime.fromisoformat(dated['ends_at']):
                current = dated
        if dt.datetime.fromisoformat(dated['starts_at']) > now:
            next_options.append(dated)
        changes.append({'id': entry['coverage_assignment_id'], 'kind': 'COVERAGE',
                        'subject_code': entry['subject_code'], 'occurrence_date': entry['date'],
                        'status': 'ACCEPTED', 'substitute_class': True})
    next_class = min(next_options, key=lambda e: e['starts_at']) if next_options else None
    state = 'current_class' if current else 'holiday' if holiday else 'between_classes' if today else 'no_classes_today' if active else 'unpublished'
    messages = {'current_class': 'Class in progress.', 'holiday': 'Institution holiday today.', 'between_classes': 'No class in progress; this may be a break or free period.',
                'no_classes_today': 'No classes scheduled today.', 'unpublished': 'No published timetable for the current academic term.'}
    return {'timezone': 'Asia/Kolkata', 'date': str(now.date()), 'published': published, 'state': state,
            'message': messages[state], 'weekly': weekly, 'today': today, 'current': current, 'next': next_class,
            'next_message': None if next_class else 'No remaining published classes.',
            'period_definitions': period_definitions, 'changes': changes}


def personal(db, user, now=None):
    if user.role == 'student':
        student = db.get(Student, user.usn) if user.usn else None
        if student is None:
            raise HTTPException(404, 'Student profile not found.')
        return view(db, dept=student.dept_code, year=student.year, semester=student.semester, section=student.section, now=now)
    if user.role in ('faculty', 'hod'):
        faculty = db.get(Faculty, user.faculty_id) if user.faculty_id else None
        if faculty is None or faculty.dept_code != user.dept_code:
            raise HTTPException(404, 'Faculty profile not found.')
        return view(db, faculty_id=faculty.id, now=now)
    raise HTTPException(403, 'A student or faculty identity is required.')


def grid(db, dept=None, year=None, section=None, faculty_id=None, semester=None):
    if faculty_id is None and dept is None:
        return {'days': DAYS, 'periods': [], 'cells': {}, 'published': False}
    data = view(db, dept=dept, year=year, section=section, faculty_id=faculty_id, semester=semester)
    definitions = data['period_definitions']
    count = max((p['index'] for p in definitions), default=-1)+1
    labels = [next((f"{p['start']//60:02}:{p['start']%60:02}" for p in definitions if p['index'] == i), f'Period {i+1}') for i in range(count)]
    return {'dept': dept, 'year': year, 'section': section, 'days': DAYS, 'periods': labels, 'published': data['published'],
            'cells': {f"{e['day']}-{e['period_index']}": {'subject': e['subject_code'], 'subject_name': e['subject_name'],
                       'faculty': e['faculty'], 'room': e['room'], 'class': e['section']} for e in data['weekly']}}


def authorized_grid(db, user, dept, year, section):
    if user.role == 'student':
        student = db.get(Student, user.usn) if user.usn else None
        if not student or (dept, year, section) != (student.dept_code, student.year, student.section):
            raise HTTPException(404, 'Timetable not found.')
        return grid(db, dept, year, section, semester=student.semester)
    if user.role == 'hod' and dept == user.dept_code:
        return grid(db, dept, year, section)
    if user.role in ('principal', 'admin'):
        return grid(db, dept, year, section)
    # Faculty may only see their own entries, never another section's full grid.
    if user.role == 'faculty' and user.faculty_id and dept == user.dept_code:
        return grid(db, dept, year, section, faculty_id=user.faculty_id)
    raise HTTPException(404, 'Timetable not found.')


@router.get('/student/timetable')
@router.get('/student/timetable/weekly')
@router.get('/student/timetable/today')
@router.get('/student/timetable/current-next')
def student_timetable(user: User = Depends(require_role('student')), db: Session = Depends(get_session)):
    with db.no_autoflush:
        return personal(db, user)


@router.get('/faculty/timetable')
@router.get('/faculty/timetable/weekly')
@router.get('/faculty/timetable/today')
@router.get('/faculty/timetable/current-next')
def faculty_timetable(user: User = Depends(require_role('faculty', 'hod')), db: Session = Depends(get_session)):
    with db.no_autoflush:
        return personal(db, user)
