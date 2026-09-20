"""Placement workflows. Reads never score models or persist derived records.

Drive locks serialize shortlist upserts and lifecycle changes. Student locks
serialize accepted-offer checks across drives on PostgreSQL.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import func

from ..models import Department, PlacementDrive, PlacementOutcome, PlacementShortlist, Student, User, utcnow
from ..notifications import notify_users, notify_usns
from . import scoring

ACTIVE = ('OPEN', 'SHORTLIST_GENERATED')
FINAL = ('OFFER_ACCEPTED', 'OFFER_DECLINED', 'REJECTED')
BUSINESS_TIMEZONE = ZoneInfo('Asia/Kolkata')


def placement_today() -> dt.date:
    """Return the MAWOS business date, independent of a browser's timezone."""
    return dt.datetime.now(BUSINESS_TIMEZONE).date()


class PlacementError(Exception):
    def __init__(self, message, status=409):
        self.message, self.status = message, status
        super().__init__(message)


def normalize_usn(usn):
    return str(usn or '').strip().upper()


def drive_record(drive):
    fields = ('id', 'company', 'role', 'package_lpa', 'drive_date', 'departments',
              'status', 'min_cgpa', 'max_backlogs', 'min_attendance',
              'requires_fee_clearance', 'application_deadline', 'description',
              'application_url', 'cancellation_reason', 'created_at', 'updated_at')
    record = {name: getattr(drive, name) for name in fields}
    record['job_document'] = None if not drive.job_document_storage_key else {
        'original_name': drive.job_document_original_name,
        'content_type': drive.job_document_content_type,
        'size_bytes': drive.job_document_size_bytes,
        'uploaded_at': drive.job_document_uploaded_at,
        'download_url': f'/api/placements/drives/{drive.id}/document',
    }
    return record


def outcome_record(outcome):
    return {name: getattr(outcome, name) for name in
            ('id', 'drive_id', 'usn', 'outcome_status', 'package_offered', 'decided_at', 'updated_at')}


def hard_failures(student, drive, attendance, cleared=True):
    dept = student.dept_code.strip().upper()
    departments = ','.join(code.strip().upper() for code in drive.departments.split(','))
    reasons = []
    if departments != 'ALL' and dept not in departments.split(','):
        reasons.append(f'Branch {dept} not in eligible list ({departments})')
    if student.cgpa < drive.min_cgpa:
        reasons.append(f'CGPA {student.cgpa:g} below cutoff of {drive.min_cgpa:g}')
    if student.backlogs > drive.max_backlogs:
        reasons.append(f'{student.backlogs} active backlog(s) exceeds limit of {drive.max_backlogs}')
    if attendance < drive.min_attendance:
        reasons.append(f'Attendance {attendance:g}% below requirement of {drive.min_attendance:g}%')
    if drive.requires_fee_clearance and not cleared:
        reasons.append('Outstanding fee dues; this drive requires fee clearance')
    return reasons


class PlacementService:
    def __init__(self, model=None, model_version=None):
        self.model, self.model_version = model, model_version

    def drive(self, db, drive_id, lock=False):
        query = db.query(PlacementDrive).filter_by(id=drive_id)
        if lock:
            query = query.populate_existing().with_for_update()
        drive = query.one_or_none()
        if drive is None:
            raise PlacementError('Placement drive not found', 404)
        return drive

    def list_drives(self, db, admin=False, usn=None):
        result = []
        query = db.query(PlacementDrive)
        if not admin:
            query = query.filter(PlacementDrive.status != 'DRAFT')
        for drive in query.order_by(PlacementDrive.drive_date.desc(), PlacementDrive.id.desc()):
            item = drive_record(drive)
            if admin:
                query = db.query(PlacementShortlist).filter_by(drive_id=drive.id)
                item.update(candidate_count=query.count(), shortlisted_count=query.filter_by(eligible=True).count())
            elif usn:
                eligibility = self.eligibility_fields(db, drive, usn)
                eligibility['eligibility_status'] = eligibility.pop('status')
                item.update(eligibility)
            result.append(item)
        return result

    def save_drive(self, db, data, drive_id=None, events=None):
        if data.departments != 'ALL':
            codes = data.departments.split(',')
            known = {code for code, in db.query(Department.code).filter(Department.code.in_(codes))}
            if set(codes) != known:
                raise PlacementError('Unknown department code in eligible list', 422)
        drive = self.drive(db, drive_id, lock=True) if drive_id else PlacementDrive()
        self._validate_drive_dates(data, existing=drive if drive_id else None)
        was_open = drive_id is not None and drive.status == 'OPEN'
        if drive_id and drive.status not in ('DRAFT', 'OPEN'):
            raise PlacementError('Only DRAFT or OPEN drives may be edited')
        if data.status not in ('DRAFT', 'OPEN') or (drive_id and drive.status == 'OPEN' and data.status == 'DRAFT'):
            raise PlacementError('Use explicit lifecycle actions; only DRAFT to OPEN is allowed when editing')
        for name, value in data.model_dump().items():
            setattr(drive, name, value)
        if drive.status != 'CANCELLED':
            drive.cancellation_reason = None
        db.add(drive)
        db.flush()
        if data.status == 'OPEN' and not was_open:
            notified = self._announce_open_drive(db, drive)
            if events is not None:
                events.append(('placement.drive_opened', dict(
                    drive_id=drive.id, company=drive.company, notified_count=notified)))
        return drive_record(drive)

    @staticmethod
    def _validate_drive_dates(data, existing=None):
        """Enforce future placement dates while preserving untouched legacy rows."""
        today = placement_today()
        deadline_changed = existing is None or data.application_deadline != existing.application_deadline
        drive_date_changed = existing is None or data.drive_date != existing.drive_date
        if (data.application_deadline is not None and data.application_deadline < today
                and deadline_changed):
            raise PlacementError('Application deadline cannot be in the past.', 422)
        if data.drive_date < today and drive_date_changed:
            raise PlacementError('Drive date cannot be in the past.', 422)
        dates_changed = existing is None or deadline_changed or drive_date_changed
        if (dates_changed and data.application_deadline is not None
                and data.drive_date < data.application_deadline):
            raise PlacementError('Drive date must be on or after the application deadline.', 422)

    def _announce_open_drive(self, db, drive):
        departments = {code.strip().upper() for code in drive.departments.split(',') if code.strip()}
        recipients = (db.query(User.id).join(Student, Student.usn == User.usn)
                      .filter(User.role == 'student', Student.year == 4))
        if departments != {'ALL'}:
            recipients = recipients.filter(func.upper(Student.dept_code).in_(departments))
        deadline = (f', application deadline {drive.application_deadline.isoformat()}'
                    if drive.application_deadline else '')
        message = (f'{drive.role} · {drive.package_lpa:g} LPA · drive date '
                   f'{drive.drive_date.isoformat()}{deadline}.')
        return notify_users(
            db, (user_id for user_id, in recipients.all()),
            title=f'New placement drive: {drive.company}', message=message,
            notification_type='PLACEMENT_DRIVE_OPENED', source_agent='placement_service',
            event_key=f'placement_drive_opened:{drive.id}',
            route=f'/student/placements/{drive.id}',
            related_entity_type='placement_drive', related_entity_id=drive.id)

    def transition(self, db, drive_id, action, reason=None):
        drive = self.drive(db, drive_id, lock=True)
        if action == 'close' and drive.status in ACTIVE:
            drive.status = 'CLOSED'
            drive.cancellation_reason = None
        elif action == 'cancel' and drive.status in ('DRAFT', *ACTIVE):
            if not reason or not reason.strip():
                raise PlacementError('Cancellation reason is required', 400)
            drive.status = 'CANCELLED'
            drive.cancellation_reason = reason.strip()
        else:
            raise PlacementError('Invalid drive lifecycle transition')
        drive.updated_at = utcnow()
        db.flush()
        return drive_record(drive)

    def evaluate(self, db, student, drive, attendance=None, use_model=True):
        # Lazy imports keep domain/agent registry imports acyclic.
        from ..agents.attendance import overall_percentage
        from ..agents.finance import fees_cleared
        if attendance is None:
            attendance = overall_percentage(db, student.usn)
        cleared = fees_cleared(db, student.usn) if drive.requires_fee_clearance else True
        failures = hard_failures(student, drive, attendance, cleared)
        if failures:
            return dict(eligible=False, ml_probability=None, model_version=None, reasons='; '.join(failures))
        if not use_model:
            return dict(eligible=None, ml_probability=None, model_version=None,
                        reasons='Meets all hard-filter criteria; shortlist not yet evaluated')
        return scoring.score(self.model, self.model_version, student, attendance)

    def upsert(self, db, student, drive, attendance=None):
        result = self.evaluate(db, student, drive, attendance)
        entry = db.query(PlacementShortlist).filter_by(drive_id=drive.id, usn=student.usn).one_or_none()
        if entry is None:
            entry = PlacementShortlist(drive_id=drive.id, usn=student.usn)
            db.add(entry)
        for key, value in result.items():
            setattr(entry, key, value)
        entry.updated_at = utcnow()
        db.flush()
        return entry

    def generate(self, db, drive_id, regenerate=False):
        drive = self.drive(db, drive_id, lock=True)
        if drive.status not in ACTIVE:
            raise PlacementError('Shortlist generation requires OPEN or SHORTLIST_GENERATED')
        exists = db.query(PlacementShortlist.id).filter_by(drive_id=drive.id).first()
        if (drive.status == 'SHORTLIST_GENERATED' or exists) and not regenerate:
            raise PlacementError('Shortlist already exists; confirm regenerate=true')
        events, shortlisted = [], 0
        for student in db.query(Student).filter_by(year=4).order_by(Student.usn):
            entry = self.upsert(db, student, drive)
            if entry.eligible:
                shortlisted += 1
                notify_usns(
                    db, [student.usn],
                    title=f'Placement shortlist: {drive.company}',
                    message=f'You have been shortlisted for {drive.company} — {drive.role}.',
                    notification_type='PLACEMENT_SHORTLISTED', source_agent='placement_service',
                    event_key=f'placement_shortlisted:{drive.id}:{student.usn}',
                    route=f'/student/placements/{drive.id}',
                    related_entity_type='placement_drive', related_entity_id=drive.id)
                events.append(('placement.notification_required', dict(usn=student.usn,
                              notification_type='PLACEMENT_SHORTLISTED', drive_id=drive.id)))
        drive.status, drive.updated_at = 'SHORTLIST_GENERATED', utcnow()
        db.flush()
        events.insert(0, ('placement.shortlist_generated', dict(drive_id=drive.id,
                      company=drive.company, shortlisted_count=shortlisted,
                      model_version=self.model_version if self.model is not None else None)))
        return dict(drive_id=drive.id, shortlisted_count=shortlisted, status=drive.status), events

    def active_drives(self, db):
        return (db.query(PlacementDrive).filter(PlacementDrive.status.in_(ACTIVE),
                PlacementDrive.drive_date >= dt.date.today() - dt.timedelta(days=7))
                .order_by(PlacementDrive.id).all())

    def reevaluate_student(self, db, usn, drives=None, attendance=None):
        student = db.get(Student, normalize_usn(usn))
        if student is None or student.year != 4:
            return 0
        changed = 0
        for item in drives if drives is not None else self.active_drives(db):
            drive = self.drive(db, item.id, lock=True)
            if drive.status not in ACTIVE or drive.drive_date < dt.date.today() - dt.timedelta(days=7):
                continue
            final = db.query(PlacementOutcome.id).filter_by(drive_id=drive.id, usn=student.usn).filter(
                PlacementOutcome.outcome_status.in_(FINAL)).first()
            if final:
                continue
            self.upsert(db, student, drive, attendance)
            changed += 1
        return changed

    def eligibility(self, db, drive_id, usn):
        drive = self.drive(db, drive_id)
        if drive.status == 'DRAFT':
            raise PlacementError('Placement drive not found', 404)
        return dict(usn=normalize_usn(usn), drive=drive_record(drive),
                    **self.eligibility_fields(db, drive, usn))

    def eligibility_fields(self, db, drive, usn):
        student = db.get(Student, normalize_usn(usn))
        if student is None:
            raise PlacementError('Student not found', 404)
        entry = db.query(PlacementShortlist).filter_by(drive_id=drive.id, usn=student.usn).one_or_none()
        if entry:
            result = {key: getattr(entry, key) for key in
                      ('eligible', 'ml_probability', 'model_version', 'reasons', 'updated_at')}
            result['status'] = 'EVALUATED'
        else:
            result = self.evaluate(db, student, drive, use_model=False) if student.year == 4 else dict(
                eligible=False, ml_probability=None, model_version=None, reasons='Placements open in final year')
            result['status'] = 'NOT_EVALUATED'
        can_apply = (drive.status in ACTIVE and drive.application_url is not None
                     and (drive.application_deadline is None or drive.application_deadline >= dt.date.today())
                     and result['eligible'] is True)
        if can_apply:
            apply_message = 'Eligible to apply on the official company portal.'
        elif drive.status == 'CLOSED':
            apply_message = 'This drive is closed; external applications are no longer available.'
        elif drive.status == 'CANCELLED':
            apply_message = 'This drive was cancelled; external applications are unavailable.'
        elif drive.application_deadline and drive.application_deadline < dt.date.today():
            apply_message = 'The application deadline has passed.'
        elif result['eligible'] is not True:
            apply_message = 'You are not currently eligible for this drive.'
        elif not drive.application_url:
            apply_message = 'The company has not provided an external application link.'
        else:
            apply_message = 'External applications are unavailable.'
        return dict(**result, can_apply=can_apply, apply_message=apply_message)

    def student_detail(self, db, drive_id, usn):
        return self.eligibility(db, drive_id, usn)

    def shortlist(self, db, drive_id):
        self.drive(db, drive_id)
        rows = (db.query(PlacementShortlist, Student.name).join(Student, Student.usn == PlacementShortlist.usn)
                .filter(PlacementShortlist.drive_id == drive_id)
                .order_by(PlacementShortlist.eligible.desc(), PlacementShortlist.ml_probability.desc().nullslast(), PlacementShortlist.usn))
        return [dict(usn=row.usn, name=name, eligible=row.eligible, ml_probability=row.ml_probability,
                     model_version=row.model_version, reasons=row.reasons, updated_at=row.updated_at) for row, name in rows]

    def record_outcome(self, db, drive_id, usn, data):
        drive = self.drive(db, drive_id, lock=True)
        usn = normalize_usn(usn)
        student = db.query(Student).filter_by(usn=usn).with_for_update().one_or_none()
        if student is None:
            raise PlacementError('Student not found', 404)
        if data.outcome_status == 'OFFER_ACCEPTED' and not data.allow_multiple_offers:
            other = db.query(PlacementOutcome.id).filter(PlacementOutcome.usn == usn,
                    PlacementOutcome.drive_id != drive_id, PlacementOutcome.outcome_status == 'OFFER_ACCEPTED').first()
            if other:
                raise PlacementError('Student already has an accepted offer; confirm allow_multiple_offers=true')
        outcome = db.query(PlacementOutcome).filter_by(drive_id=drive_id, usn=usn).one_or_none()
        if outcome is None:
            outcome = PlacementOutcome(drive_id=drive_id, usn=usn)
            db.add(outcome)
        outcome.outcome_status, outcome.package_offered = data.outcome_status, data.package_offered
        outcome.decided_at = outcome.updated_at = utcnow()
        db.flush()
        package = f' Package: {data.package_offered:g} LPA.' if data.package_offered else ''
        notify_usns(
            db, [usn],
            title=f'Placement outcome: {drive.company}',
            message=f'Your outcome for {drive.role} is {data.outcome_status.replace("_", " ").title()}.{package}',
            notification_type='PLACEMENT_OUTCOME', source_agent='placement_service',
            event_key=f'placement_outcome:{drive_id}:{usn}:{data.outcome_status}',
            route=f'/student/placements/{drive_id}',
            related_entity_type='placement_drive', related_entity_id=drive_id)
        event = ('placement.' + data.outcome_status.lower(), dict(drive_id=drive_id, usn=usn,
                 outcome_status=data.outcome_status, package_offered=data.package_offered))
        return outcome_record(outcome), [event]

    def outcomes(self, db, drive_id):
        self.drive(db, drive_id)
        return [outcome_record(row) for row in db.query(PlacementOutcome).filter_by(drive_id=drive_id).order_by(PlacementOutcome.usn)]

    def admin_view(self, db, drive_id):
        drive = drive_record(self.drive(db, drive_id))
        outcomes = {row['usn']: row for row in self.outcomes(db, drive_id)}
        rows = self.shortlist(db, drive_id)
        for row in rows:
            row['outcome'] = outcomes.pop(row['usn'], None)
        return {'drive': drive, 'shortlist': rows, 'other_outcomes': list(outcomes.values())}

    def set_document(self, db, drive_id, document, user_id):
        drive = self.drive(db, drive_id, lock=True)
        if drive.status not in ('DRAFT', 'OPEN'):
            raise PlacementError('Documents may be changed only on DRAFT or OPEN drives')
        old_key = drive.job_document_storage_key
        drive.job_document_storage_key = document.storage_key
        drive.job_document_original_name = document.original_name
        drive.job_document_content_type = document.content_type
        drive.job_document_size_bytes = document.size_bytes
        drive.job_document_sha256 = document.sha256
        drive.job_document_uploaded_at = utcnow()
        drive.job_document_uploaded_by = user_id
        drive.updated_at = utcnow()
        db.flush()
        return drive_record(drive), old_key

    def clear_document(self, db, drive_id):
        drive = self.drive(db, drive_id, lock=True)
        if drive.status not in ('DRAFT', 'OPEN'):
            raise PlacementError('Documents may be changed only on DRAFT or OPEN drives')
        old_key = drive.job_document_storage_key
        if not old_key:
            raise PlacementError('Job document not found', 404)
        for field in ('job_document_storage_key', 'job_document_original_name',
                      'job_document_content_type', 'job_document_size_bytes',
                      'job_document_sha256', 'job_document_uploaded_at',
                      'job_document_uploaded_by'):
            setattr(drive, field, None)
        drive.updated_at = utcnow()
        db.flush()
        return drive_record(drive), old_key

    def stats(self, db):
        eligible = (db.query(Student.dept_code, func.count(func.distinct(PlacementShortlist.usn)))
                    .join(PlacementShortlist, PlacementShortlist.usn == Student.usn)
                    .filter(PlacementShortlist.eligible.is_(True)).group_by(Student.dept_code).all())
        return dict(upcoming_drives=len(self.active_drives(db)), eligible_finalists_by_dept=dict(eligible))
