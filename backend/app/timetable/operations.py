"""Allowlisted timetable commands with preview/confirm capabilities.

No model provider is imported here.  The assistant can only submit this module's
strict action schema; authorization, lookup, conflict checks, persistence and
notifications remain deterministic backend responsibilities.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..auth import require_role
from ..bus import bus
from ..coverage import service as coverage
from ..coverage.models import (AttendanceSheet, CoverageAssignment,
                               CoverageRequest, FacultyAbsence)
from ..database import get_session
from ..models import Faculty, User
from ..notifications import notify_role
from . import models as m, service as timetable
from .room_types import room_kind_satisfies
from .runner import SolverBusy, SolverTimeout, run_solver
from .sources import native_source

router = APIRouter(prefix='/api/timetable/operations', tags=['timetable operations'])
operator = require_role('faculty', 'hod', 'principal', 'admin')
reviewer = require_role('hod', 'principal', 'admin')

PREVIEW_ACTIONS = {
    'generate_timetable_draft', 'preview_reschedule_class', 'preview_cancel_class',
    'preview_replacement_slot', 'preview_coverage_assignment', 'validate_timetable_draft',
    'publish_timetable_draft',
}
READ_ACTIONS = {'get_timetable_conflicts'}
ALL_ACTIONS = PREVIEW_ACTIONS | READ_ACTIONS
IMPACTFUL = PREVIEW_ACTIONS - {'generate_timetable_draft'}
TTL = dt.timedelta(minutes=15)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    action: Literal[
        'generate_timetable_draft', 'get_timetable_conflicts',
        'preview_reschedule_class', 'preview_cancel_class',
        'preview_replacement_slot', 'preview_coverage_assignment',
        'validate_timetable_draft', 'publish_timetable_draft',
    ]
    department: str | None = Field(default=None, min_length=1, max_length=8)
    term_id: int | None = Field(default=None, ge=1)
    run_id: int | None = Field(default=None, ge=1)
    entry_id: int | None = Field(default=None, ge=1)
    occurrence_date: dt.date | None = None
    replacement_date: dt.date | None = None
    period_index: int | None = Field(default=None, ge=0, le=23)
    room_id: int | None = Field(default=None, ge=1)
    coverage_request_id: int | None = Field(default=None, ge=1)
    substitute_faculty_id: int | None = Field(default=None, ge=1)
    seed: int = Field(default=7, ge=0, le=2147483647)
    max_steps: int = Field(default=500000, ge=100, le=2000000)
    restarts: int = Field(default=2, ge=0, le=5)
    optimize_passes: int = Field(default=100, ge=0, le=2000)

    @model_validator(mode='after')
    def exact_parameters(self):
        required = {
            'generate_timetable_draft': {'term_id'},
            'get_timetable_conflicts': {'run_id'},
            'preview_reschedule_class': {'entry_id', 'occurrence_date', 'replacement_date', 'period_index', 'room_id'},
            'preview_cancel_class': {'entry_id', 'occurrence_date'},
            'preview_replacement_slot': {'entry_id', 'occurrence_date'},
            'preview_coverage_assignment': {'coverage_request_id', 'substitute_faculty_id'},
            'validate_timetable_draft': {'run_id'},
            'publish_timetable_draft': {'run_id'},
        }[self.action]
        supplied = {name for name in (
            'term_id', 'run_id', 'entry_id', 'occurrence_date', 'replacement_date',
            'period_index', 'room_id', 'coverage_request_id', 'substitute_faculty_id')
            if getattr(self, name) is not None}
        allowed = set(required)
        if self.action == 'preview_replacement_slot':
            allowed |= {'replacement_date', 'period_index', 'room_id'}
        if not required <= supplied:
            raise ValueError(f'{self.action} requires: {", ".join(sorted(required))}.')
        if supplied - allowed:
            raise ValueError(f'{self.action} received parameters it does not accept.')
        if self.action != 'generate_timetable_draft' and any((self.seed != 7, self.max_steps != 500000,
                                                               self.restarts != 2, self.optimize_passes != 100)):
            raise ValueError('Solver controls are accepted only for draft generation.')
        return self


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_id: str = Field(pattern=r'^[0-9a-f-]{36}$')
    confirmation_token: str | None = Field(default=None, min_length=43, max_length=128)


class AssistantCommand(BaseModel):
    """Bounded natural-language helper; identifiers remain explicit fields."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=500)
    department: str | None = Field(default=None, min_length=1, max_length=8)
    term_id: int | None = Field(default=None, ge=1)
    run_id: int | None = Field(default=None, ge=1)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def _load(value):
    return json.loads(value)


def _department(user, requested):
    value = requested.upper() if requested else user.dept_code
    if user.role == 'hod':
        if not value or value != user.dept_code:
            raise HTTPException(404, 'Timetable scope not found.')
        return value
    if user.role in {'principal', 'admin'}:
        if not value:
            raise HTTPException(422, 'Choose a department for this institution-wide action.')
        return value
    if user.role == 'faculty':
        if requested and requested.upper() != user.dept_code:
            raise HTTPException(404, 'Timetable scope not found.')
        return user.dept_code
    raise HTTPException(403, 'Timetable operation is not permitted.')


def _published_entry(db, entry_id, date, user, *, faculty_request=False):
    entry = (db.query(m.Entry).join(m.Run, m.Run.id == m.Entry.run_id)
             .filter(m.Entry.id == entry_id, m.Run.status == 'PUBLISHED').one_or_none())
    if entry is None:
        raise HTTPException(404, 'Published timetable occurrence not found.')
    if user.role == 'hod' and entry.dept_code != user.dept_code:
        raise HTTPException(404, 'Published timetable occurrence not found.')
    if user.role == 'faculty' and (not faculty_request or entry.faculty_id != user.faculty_id):
        raise HTTPException(404, 'Published timetable occurrence not found.')
    term = db.get(m.Term, entry.term_id)
    if not term or not term.starts_on <= date <= term.ends_on or date.weekday() != entry.day_of_week:
        raise HTTPException(409, 'Date is not an occurrence of this published class.')
    if db.query(m.Holiday.id).filter_by(term_id=entry.term_id, date=date).first():
        raise HTTPException(409, 'The occurrence falls on an institution holiday.')
    if db.query(m.OccurrenceChange.id).filter_by(timetable_entry_id=entry.id, occurrence_date=date).first():
        raise HTTPException(409, 'This occurrence already has an authorized change.')
    if db.query(AttendanceSheet.id).filter_by(timetable_entry_id=entry.id, occurrence_date=date).first():
        raise HTTPException(409, 'Attendance already exists for this occurrence.')
    return entry, term


def _ensure_occurrence_is_not_covered(db, entry_id, date):
    if db.query(CoverageAssignment.id).join(
            CoverageRequest, CoverageRequest.id == CoverageAssignment.coverage_request_id).filter(
                CoverageAssignment.timetable_entry_id == entry_id,
                CoverageAssignment.occurrence_date == date,
                CoverageAssignment.status == 'ACCEPTED',
                CoverageRequest.status == 'APPROVED').first():
        raise HTTPException(409, 'Accepted coverage already resolves this occurrence.')


def _block(db, entry):
    return (db.query(m.Entry).filter_by(run_id=entry.run_id, requirement_id=entry.requirement_id,
                                        occurrence=entry.occurrence)
            .order_by(m.Entry.period_index, m.Entry.id).all())


def _summary(db, entry, date):
    section = db.get(m.Section, entry.section_id)
    faculty = db.get(Faculty, entry.faculty_id)
    room = db.get(m.Room, entry.room_id)
    return {'entry_id': entry.id, 'run_id': entry.run_id, 'date': str(date),
            'department': entry.dept_code, 'section': f'{section.year}{section.name}',
            'subject_code': entry.subject_code, 'faculty_id': entry.faculty_id,
            'faculty': faculty.name if faculty else None, 'room_id': entry.room_id,
            'room': room.name if room else None, 'period_index': entry.period_index}


def _target(db, entry, term, source_date, date, period_index, room_id, block_size):
    if date < dt.date.today() or not term.starts_on <= date <= term.ends_on:
        raise HTTPException(422, 'Replacement date must be today or later within the same term.')
    if db.query(m.Holiday.id).filter_by(term_id=term.id, date=date).first():
        raise HTTPException(409, 'Replacement date is an institution holiday.')
    if db.query(m.OccurrenceChange.id).filter_by(
            timetable_entry_id=entry.id, replacement_date=date,
            action='RESCHEDULED').first():
        raise HTTPException(409, 'This class already has an approved replacement on that date.')
    periods = [db.query(m.PeriodDefinition).filter_by(
        term_id=term.id, day_of_week=date.weekday(), period_index=period_index + offset).one_or_none()
        for offset in range(block_size)]
    if any(not p or p.is_break or p.is_closed for p in periods) or any(
            a.ends_at != b.starts_at for a, b in zip(periods, periods[1:])):
        raise HTTPException(409, 'Replacement periods are unavailable or not contiguous.')
    room = db.get(m.Room, room_id)
    section = db.get(m.Section, entry.section_id)
    requirement = db.get(m.Requirement, entry.requirement_id)
    if (not room or room.dept_code != entry.dept_code or room.capacity < section.size
            or not room_kind_satisfies(requirement.room_type, room.kind)):
        raise HTTPException(404, 'Compatible replacement room not found.')
    unavailable = {p.id for p in periods}
    if db.query(m.RoomUnavailable).filter_by(room_id=room.id).filter(
            m.RoomUnavailable.period_id.in_(unavailable)).first():
        raise HTTPException(409, 'Replacement room is unavailable.')
    if db.query(m.FacultyUnavailable).filter_by(faculty_id=entry.faculty_id).filter(
            m.FacultyUnavailable.period_id.in_(unavailable)).first():
        raise HTTPException(409, 'Faculty is unavailable in the replacement period.')
    if db.query(FacultyAbsence.id).filter(
            FacultyAbsence.faculty_id == entry.faculty_id,
            FacultyAbsence.status == 'APPROVED',
            FacultyAbsence.starts_on <= date, FacultyAbsence.ends_on >= date,
            ((FacultyAbsence.period_id.is_(None)) |
             (FacultyAbsence.period_id.in_(unavailable)))).first():
        raise HTTPException(409, 'Faculty has an approved absence in the replacement period.')
    if db.query(CoverageAssignment.id).filter(
            CoverageAssignment.substitute_faculty_id == entry.faculty_id,
            CoverageAssignment.occurrence_date == date,
            CoverageAssignment.period_id.in_(unavailable),
            CoverageAssignment.status.in_(('PROPOSED', 'ACCEPTED'))).first():
        raise HTTPException(409, 'Faculty already has an active coverage assignment in the replacement period.')
    for offset in range(block_size):
        index = period_index + offset
        conflicts = (db.query(m.Entry).join(m.Run, m.Run.id == m.Entry.run_id)
                     .filter(m.Run.status == 'PUBLISHED', m.Entry.day_of_week == date.weekday(),
                             m.Entry.period_index == index,
                             ((m.Entry.section_id == entry.section_id) |
                              (m.Entry.faculty_id == entry.faculty_id) |
                              (m.Entry.room_id == room.id))))
        if date == source_date:
            conflicts = conflicts.filter(m.Entry.id.notin_([e.id for e in _block(db, entry)]))
        if conflicts.first():
            raise HTTPException(409, 'Replacement conflicts with a published section, faculty, or room booking.')
        changed = (db.query(m.OccurrenceChange).join(
            m.Entry, m.Entry.id == m.OccurrenceChange.timetable_entry_id)
            .filter(m.OccurrenceChange.action == 'RESCHEDULED',
                    m.OccurrenceChange.replacement_date == date,
                    m.OccurrenceChange.replacement_period_index == index,
                    ((m.Entry.section_id == entry.section_id) |
                     (m.Entry.faculty_id == entry.faculty_id) |
                     (m.OccurrenceChange.replacement_room_id == room.id))).first())
        if changed:
            raise HTTPException(409, 'Replacement conflicts with an approved timetable change.')
    return room, periods


def _find_replacement(db, entry, term, source_date, block_size):
    rooms = db.query(m.Room).filter_by(dept_code=entry.dept_code).order_by(m.Room.capacity, m.Room.id).all()
    for delta in range(1, 15):
        date = source_date + dt.timedelta(days=delta)
        if date > term.ends_on:
            break
        indices = [value for value, in db.query(m.PeriodDefinition.period_index).filter_by(
            term_id=term.id, day_of_week=date.weekday(), is_break=False, is_closed=False).distinct().order_by(
                m.PeriodDefinition.period_index).all()]
        for index in indices:
            for room in rooms:
                try:
                    _target(db, entry, term, source_date, date, index, room.id, block_size)
                    return date, index, room.id
                except HTTPException:
                    continue
    raise HTTPException(409, 'No conflict-free replacement slot was found in the next 14 days.')


def _event(preview, user, phase, affected, before, after=None):
    return m.OperationEvent(correlation_id=preview.correlation_id, preview_id=preview.id,
        actor_id=user.id, action=preview.action, phase=phase, dept_code=preview.dept_code,
        requested_action=preview.request_json, affected_records=_json(affected),
        before_summary=_json(before), after_summary=_json(after or {}))


def _expire_if_accepted_coverage(db, preview, user):
    """Expire only a pending dated-change preview resolved by coverage."""
    if preview.state != 'PREVIEW' or preview.action not in {
            'preview_replacement_slot', 'preview_cancel_class', 'preview_reschedule_class'}:
        return False
    body = _load(preview.request_json)
    entry_id, occurrence_date = body.get('entry_id'), body.get('occurrence_date')
    if not entry_id or not isinstance(occurrence_date, str):
        return False
    try:
        occurrence_date = dt.date.fromisoformat(occurrence_date)
    except ValueError:
        return False
    covered = db.query(CoverageAssignment.id).join(
        CoverageRequest, CoverageRequest.id == CoverageAssignment.coverage_request_id).filter(
            CoverageAssignment.timetable_entry_id == entry_id,
            CoverageAssignment.occurrence_date == occurrence_date,
            CoverageAssignment.status == 'ACCEPTED',
            CoverageRequest.status == 'APPROVED').first()
    if not covered:
        return False
    preview.state = 'EXPIRED'
    db.add(_event(preview, user, 'EXPIRED',
                  [{'type': 'coverage_assignment', 'id': covered[0]}],
                  _load(preview.before_json), {'reason': 'accepted_coverage'}))
    return True


def _create_preview(db, user, body, department, summary, before, affected, *, run_id=None, term_id=None):
    token = secrets.token_urlsafe(32)
    preview_id, correlation = str(uuid.uuid4()), str(uuid.uuid4())
    row = m.OperationPreview(id=preview_id, correlation_id=correlation, action=body.action,
        actor_id=user.id, dept_code=department, term_id=term_id, run_id=run_id,
        request_json=_json(body.model_dump(mode='json')), preview_json=_json(summary),
        before_json=_json(before), token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=m.now() + TTL)
    db.add(row); db.flush(); db.add(_event(row, user, 'PREVIEWED', affected, before))
    db.commit()
    return {'mode': 'preview', 'preview_id': row.id, 'correlation_id': correlation,
            'confirmation_token': token, 'expires_at': row.expires_at,
            'action': body.action, 'summary': summary, 'conflicts': []}


def preview(db, user, body: ActionRequest):
    if body.action == 'get_timetable_conflicts':
        run = timetable.get_run(db, user, body.run_id)
        return {'mode': 'read', 'action': body.action, 'run_id': run.id,
                'status': run.status, 'conflicts': _load(run.conflicts), 'unplaced': _load(run.unplaced)}
    department = _department(user, body.department)
    if body.action == 'generate_timetable_draft':
        if user.role == 'faculty':
            raise HTTPException(403, 'Faculty may request class changes but cannot generate timetable drafts.')
        data, bundle, issues = native_source.snapshot(db, body.term_id, department)
        summary = {'ready': not issues, 'input_hash': timetable.digest(bundle),
                   'required_periods': sum(item.periods for item in data.requirements),
                   'sections': len(data.sections), 'issues': [item.model_dump() for item in issues]}
        if issues:
            return {'mode': 'conflict_report', 'action': body.action, 'summary': summary,
                    'conflicts': summary['issues']}
        return _create_preview(db, user, body, department, summary, {},
                               [{'type': 'term', 'id': body.term_id}], term_id=body.term_id)
    if body.action == 'publish_timetable_draft':
        if user.role == 'faculty':
            raise HTTPException(403, 'Faculty cannot publish timetables.')
        run = timetable.get_run(db, user, body.run_id, write=user.role == 'hod')
        if run.dept_code != department:
            raise HTTPException(404, 'Timetable run not found.')
        if run.status != 'COMPLETE' or timetable.check_run(db, run):
            raise HTTPException(409, 'Only a currently valid COMPLETE draft can be previewed for publication.')
        old = db.query(m.Run).filter_by(term_id=run.term_id, dept_code=department, status='PUBLISHED').one_or_none()
        before = {'published_run_id': old.id if old else None, 'replacement_status': 'ARCHIVED' if old else None}
        summary = {'message': f'Publish version {run.id} for {department}.',
                   'new_run_id': run.id, **before, 'entry_count': db.query(m.Entry).filter_by(run_id=run.id).count()}
        return _create_preview(db, user, body, department, summary, before,
                               [{'type': 'timetable_run', 'id': run.id}], run_id=run.id, term_id=run.term_id)
    if body.action == 'validate_timetable_draft':
        if user.role == 'faculty':
            raise HTTPException(403, 'Faculty cannot validate timetable drafts.')
        run = timetable.get_run(db, user, body.run_id, write=user.role == 'hod')
        if run.dept_code != department:
            raise HTTPException(404, 'Timetable run not found.')
        if run.status not in {'DRAFT', 'PARTIAL'}:
            raise HTTPException(409, 'Only an unvalidated draft can be validated.')
        issues = timetable.check_run(db, run)
        before = {'status': run.status, 'conflict_count': len(_load(run.conflicts))}
        summary = {'message': f'Validate version {run.id} for publication readiness.',
                   'run_id': run.id, 'status_after_confirmation': 'COMPLETE' if not issues else 'PARTIAL',
                   'conflicts': [issue.model_dump() for issue in issues], **before}
        return _create_preview(db, user, body, department, summary, before,
                               [{'type': 'timetable_run', 'id': run.id}], run_id=run.id, term_id=run.term_id)
    if body.action == 'preview_coverage_assignment':
        if user.role == 'faculty':
            raise HTTPException(403, 'Faculty may respond to proposals but cannot assign coverage.')
        request, _, original, _ = coverage.request_scope(db, user, body.coverage_request_id)
        if original.dept_code != department:
            raise HTTPException(404, 'Coverage request not found in your authorized scope.')
        eligible = {item['faculty_id']: item for item in coverage.candidates(
            db, user, request.id, mutate_status=False)}
        candidate = eligible.get(body.substitute_faculty_id)
        if not candidate:
            raise HTTPException(409, 'The selected faculty member is not an eligible substitute.')
        before = {'coverage_request_id': request.id, 'status': request.status}
        summary = {'message': 'Propose qualified substitute coverage; the substitute must still accept.',
                   'occurrence_date': str(request.occurrence_date), 'candidate': candidate, **before}
        return _create_preview(db, user, body, department, summary, before,
                               [{'type': 'coverage_request', 'id': request.id}])
    faculty_request = user.role == 'faculty' and body.action in {
        'preview_reschedule_class', 'preview_replacement_slot'}
    entry, term = _published_entry(db, body.entry_id, body.occurrence_date, user,
                                    faculty_request=faculty_request)
    _ensure_occurrence_is_not_covered(db, entry.id, body.occurrence_date)
    if entry.dept_code != department:
        raise HTTPException(404, 'Published timetable occurrence not found.')
    block = _block(db, entry)
    before = {'occurrences': [_summary(db, item, body.occurrence_date) for item in block]}
    if body.action == 'preview_cancel_class':
        if user.role == 'faculty':
            raise HTTPException(403, 'Faculty may request rescheduling but cannot cancel classes.')
        summary = {'message': 'Cancel this dated class; a make-up class remains required.', **before}
    else:
        date, index, room_id = (body.replacement_date, body.period_index, body.room_id)
        if body.action == 'preview_replacement_slot':
            date, index, room_id = _find_replacement(db, entry, term, body.occurrence_date, len(block))
        room, _ = _target(db, entry, term, body.occurrence_date, date, index, room_id, len(block))
        summary = {'message': 'Move this dated class after explicit department confirmation.',
                   'replacement_date': str(date), 'period_index': index,
                   'room_id': room.id, 'room': room.name, **before}
        # Store the deterministic suggestion/validated target in the signed request.
        body = body.model_copy(update={'replacement_date': date, 'period_index': index, 'room_id': room.id})
    return _create_preview(db, user, body, department, summary, before,
                           [{'type': 'timetable_entry', 'id': item.id, 'date': str(body.occurrence_date)} for item in block],
                           run_id=entry.run_id, term_id=entry.term_id)


def _authorized_preview(db, user, body):
    row = db.query(m.OperationPreview).filter_by(id=body.preview_id).with_for_update().one_or_none()
    if row is None:
        raise HTTPException(404, 'Timetable operation preview not found.')
    owns = row.actor_id == user.id
    delegated_review = (not owns and user.role in {'hod', 'principal', 'admin'}
                        and (user.role != 'hod' or row.dept_code == user.dept_code))
    if not owns and not delegated_review:
        raise HTTPException(404, 'Timetable operation preview not found.')
    if _expire_if_accepted_coverage(db, row, user):
        db.commit()
        raise HTTPException(409, 'This preview expired because accepted coverage resolves the occurrence.')
    if row.state != 'PREVIEW':
        raise HTTPException(409, 'This preview was already used or expired.')
    now = m.now()
    if row.expires_at.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    if row.expires_at <= now:
        row.state = 'EXPIRED'; db.add(_event(row, user, 'EXPIRED', [], _load(row.before_json)))
        db.commit()
        raise HTTPException(409, 'This preview expired; create and review a new preview.')
    # The creator proves possession of the one-time capability.  A different
    # authorized reviewer confirms from the scoped pending queue instead.
    if owns and (not body.confirmation_token or not hmac.compare_digest(
            row.token_hash, hashlib.sha256(body.confirmation_token.encode()).hexdigest())):
        raise HTTPException(404, 'Timetable operation preview not found.')
    if user.role == 'hod' and row.dept_code != user.dept_code:
        raise HTTPException(404, 'Timetable operation preview not found.')
    return row


def confirm(db, user, body: ConfirmRequest):
    preview_row = _authorized_preview(db, user, body)
    if user.role == 'faculty':
        raise HTTPException(403, 'Faculty requests require HOD, Principal, or Admin confirmation.')
    request = ActionRequest.model_validate(_load(preview_row.request_json))
    before = _load(preview_row.before_json)
    affected, events = [], []
    try:
        if request.action == 'generate_timetable_draft':
            data, bundle, issues = native_source.snapshot(db, request.term_id, preview_row.dept_code)
            if issues or timetable.digest(bundle) != _load(preview_row.preview_json)['input_hash']:
                raise HTTPException(409, 'Timetable inputs changed; create a new generation preview.')
            db.rollback()
            try:
                result, elapsed = run_solver(data, seed=request.seed, max_steps=request.max_steps,
                                             restarts=request.restarts, optimize_passes=request.optimize_passes)
            except (SolverBusy, SolverTimeout) as exc:
                raise HTTPException(503, str(exc)) from None
            user = db.get(User, user.id)
            preview_row = _authorized_preview(db, user, body)
            timetable.acquire_mutation_lock(db)
            run = timetable.stage_persist(db, user, request.term_id, preview_row.dept_code,
                                          request.seed, data, bundle, result, elapsed, initial_draft=True)
            described = timetable.describe_run(db, run)
            after = {'draft_run_id': run.id, 'status': run.status, 'conflicts': described['conflicts']}
            affected = [{'type': 'timetable_run', 'id': run.id}]
            events.append(('timetable.draft_generated', {'run_id': run.id,
                'department': preview_row.dept_code, 'correlation_id': preview_row.correlation_id}))
        elif request.action == 'validate_timetable_draft':
            run = timetable.get_run(db, user, request.run_id, write=user.role == 'hod')
            if run.dept_code != preview_row.dept_code or run.status != before.get('status'):
                raise HTTPException(409, 'Draft changed; create a new validation preview.')
            after_run = timetable.stage_validate_run(db, user, run.id)
            described = timetable.describe_run(db, after_run)
            after = {'draft_run_id': described['id'], 'status': described['status'],
                     'conflicts': described['conflicts']}
            affected = [{'type': 'timetable_run', 'id': run.id}]
            events.append(('timetable.validated', {'run_id': run.id,
                'department': run.dept_code, 'correlation_id': preview_row.correlation_id}))
        elif request.action == 'publish_timetable_draft':
            run = timetable.get_run(db, user, request.run_id, write=user.role == 'hod')
            current = db.query(m.Run).filter_by(term_id=run.term_id, dept_code=run.dept_code, status='PUBLISHED').one_or_none()
            if (current.id if current else None) != before.get('published_run_id'):
                raise HTTPException(409, 'Published timetable changed; create a new publication preview.')
            after_run = timetable.stage_publish(db, user, run.id)
            after = {'published_run_id': after_run.id, 'status': after_run.status}
            affected = [{'type': 'timetable_run', 'id': run.id}]
            events.append(('timetable.published', {'run_id': run.id, 'department': run.dept_code,
                'correlation_id': preview_row.correlation_id}))
        elif request.action == 'preview_coverage_assignment':
            result, events = coverage.approve_candidate(db, user, request.coverage_request_id,
                                                        request.substitute_faculty_id)
            after = result; affected = [{'type': 'coverage_assignment', 'id': result['assignment_id']}]
        else:
            entry, term = _published_entry(db, request.entry_id, request.occurrence_date, user)
            _ensure_occurrence_is_not_covered(db, entry.id, request.occurrence_date)
            block = _block(db, entry)
            if {'occurrences': [_summary(db, item, request.occurrence_date) for item in block]} != before:
                raise HTTPException(409, 'Published occurrence changed; create a new preview.')
            cancelled = request.action == 'preview_cancel_class'
            if not cancelled:
                _target(db, entry, term, request.occurrence_date, request.replacement_date,
                        request.period_index, request.room_id, len(block))
            changes = []
            for offset, item in enumerate(block):
                change = m.OccurrenceChange(timetable_entry_id=item.id, timetable_run_id=item.run_id,
                    term_id=item.term_id, dept_code=item.dept_code, occurrence_date=request.occurrence_date,
                    action='CANCELLED' if cancelled else 'RESCHEDULED',
                    replacement_date=None if cancelled else request.replacement_date,
                    replacement_period_index=None if cancelled else request.period_index + offset,
                    replacement_room_id=None if cancelled else request.room_id,
                    correlation_id=preview_row.correlation_id, applied_by=user.id)
                db.add(change); db.flush(); changes.append(change.id)
            after = {'change_ids': changes, 'status': 'CANCELLED' if cancelled else 'RESCHEDULED',
                     'replacement_date': None if cancelled else str(request.replacement_date),
                     'period_index': None if cancelled else request.period_index}
            affected = [{'type': 'occurrence_change', 'id': value} for value in changes]
            common = dict(title='Timetable occurrence changed',
                message=('A class was cancelled and requires a make-up class.' if cancelled else
                         f'A class was rescheduled to {request.replacement_date}.'),
                notification_type='TIMETABLE_OCCURRENCE_CHANGED', source_agent='timetable_operations',
                event_key=f'timetable_change:{preview_row.correlation_id}',
                route='/student/timetable', related_entity_type='timetable_change',
                related_entity_id=preview_row.correlation_id)
            notify_role(db, 'student', dept=entry.dept_code, **common)
            notify_role(db, 'faculty', dept=entry.dept_code, **{**common, 'route': '/faculty/timetable'})
            events.append(('timetable.occurrence_changed', {'entry_id': entry.id,
                'date': str(request.occurrence_date), 'department': entry.dept_code,
                'status': after['status'], 'correlation_id': preview_row.correlation_id}))
        preview_row.state = 'CONFIRMED'; preview_row.confirmed_at = m.now()
        db.add(_event(preview_row, user, 'CONFIRMED', affected, before, after))
        db.commit()
        return {'mode': 'confirmed', 'action': request.action.replace('preview_', 'confirm_'),
                'preview_id': preview_row.id, 'correlation_id': preview_row.correlation_id,
                'result': after, '_events': events}
    except HTTPException:
        db.rollback(); raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Timetable data changed; refresh and create a new preview.') from None
    except SQLAlchemyError:
        db.rollback()
        logging.getLogger(__name__).exception('Timetable operation transaction failed')
        raise HTTPException(409, 'Timetable data changed; refresh and create a new preview.') from None
    except Exception:
        db.rollback()
        logging.getLogger(__name__).exception('Timetable operation failed')
        raise HTTPException(500, 'Timetable operation failed; no confirmed change was saved.') from None


@router.post('/preview')
def preview_action(body: ActionRequest, user=Depends(operator), db=Depends(get_session)):
    return preview(db, user, body)


@router.post('/confirm')
async def confirm_action(body: ConfirmRequest, user=Depends(reviewer), db=Depends(get_session)):
    result = confirm(db, user, body)
    events = result.pop('_events', [])
    for topic, payload in events:
        await bus.publish(topic, payload, source_agent='timetable_operations')
    return result


@router.get('/audit')
def operation_audit(user=Depends(reviewer), db=Depends(get_session)):
    query = db.query(m.OperationEvent)
    if user.role == 'hod':
        query = query.filter_by(dept_code=user.dept_code)
    return [{'id': row.id, 'correlation_id': row.correlation_id, 'action': row.action,
             'phase': row.phase, 'department': row.dept_code, 'actor_id': row.actor_id,
             'requested_action': _load(row.requested_action),
             'affected_records': _load(row.affected_records),
             'before': _load(row.before_summary), 'after': _load(row.after_summary),
             'created_at': row.created_at} for row in query.order_by(m.OperationEvent.id.desc()).limit(100)]


@router.get('/pending')
def pending_operations(user=Depends(reviewer), db=Depends(get_session)):
    query = db.query(m.OperationPreview).filter_by(state='PREVIEW')
    if user.role == 'hod':
        query = query.filter_by(dept_code=user.dept_code)
    rows = query.order_by(m.OperationPreview.created_at).limit(100).all()
    if any(_expire_if_accepted_coverage(db, row, user) for row in rows):
        db.commit()
    return [{'preview_id': row.id, 'correlation_id': row.correlation_id,
             'action': row.action, 'department': row.dept_code,
             'requested_by': row.actor_id, 'summary': _load(row.preview_json),
             'expires_at': row.expires_at} for row in rows if row.state == 'PREVIEW']


@router.post('/previews/{preview_id}/discard')
def discard_preview(preview_id: str, user=Depends(reviewer), db=Depends(get_session)):
    row = db.query(m.OperationPreview).filter_by(id=preview_id).with_for_update().one_or_none()
    if row is None or (user.role == 'hod' and row.dept_code != user.dept_code):
        raise HTTPException(404, 'Timetable operation preview not found.')
    if row.state != 'PREVIEW':
        raise HTTPException(409, 'This preview was already used or expired.')
    row.state = 'EXPIRED'
    db.add(_event(row, user, 'EXPIRED', [], _load(row.before_json),
                  {'reason': 'discarded_by_reviewer'}))
    db.commit()
    return {'preview_id': row.id, 'state': row.state}


@router.post('/interpret')
def interpret_command(body: AssistantCommand, user=Depends(operator)):
    """Interpret only an allowlisted intent; never executes or fabricates IDs."""
    text = ' '.join(body.message.casefold().split())
    if 'conflict' in text:
        action = 'get_timetable_conflicts'
    elif 'publish' in text:
        action = 'publish_timetable_draft'
    elif 'generate' in text and ('draft' in text or 'timetable' in text):
        action = 'generate_timetable_draft'
    else:
        raise HTTPException(422, 'Use a supported timetable command suggestion and select the referenced record.')
    required = {'generate_timetable_draft': 'term_id', 'get_timetable_conflicts': 'run_id',
                'publish_timetable_draft': 'run_id'}[action]
    value = getattr(body, required)
    if value is None:
        raise HTTPException(422, f'{required} is required for this command.')
    return {'action': action, required: value, 'department': body.department,
            'explanation': 'The assistant produced a validated action only. Preview it before confirmation.'}
