"""Placement workflows. Reads never score models or persist derived records.

Drive locks serialize shortlist upserts and lifecycle changes. Student locks
serialize accepted-offer checks across drives on PostgreSQL.
"""
import datetime as dt

from sqlalchemy import func

from ..models import Department, PlacementDrive, PlacementOutcome, PlacementShortlist, Student, utcnow
from . import scoring

ACTIVE = ('OPEN', 'SHORTLIST_GENERATED')
FINAL = ('OFFER_ACCEPTED', 'OFFER_DECLINED', 'REJECTED')


class PlacementError(Exception):
    def __init__(self, message, status=409):
        self.message, self.status = message, status
        super().__init__(message)


def normalize_usn(usn):
    return str(usn or '').strip().upper()


def drive_record(drive):
    fields = ('id', 'company', 'role', 'package_lpa', 'drive_date', 'departments',
              'status', 'min_cgpa', 'max_backlogs', 'min_attendance',
              'requires_fee_clearance', 'application_deadline', 'created_at', 'updated_at')
    return {name: getattr(drive, name) for name in fields}


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

    def list_drives(self, db, admin=False):
        result = []
        for drive in db.query(PlacementDrive).order_by(PlacementDrive.drive_date.desc(), PlacementDrive.id.desc()):
            item = drive_record(drive)
            if admin:
                query = db.query(PlacementShortlist).filter_by(drive_id=drive.id)
                item.update(candidate_count=query.count(), shortlisted_count=query.filter_by(eligible=True).count())
            result.append(item)
        return result

    def save_drive(self, db, data, drive_id=None):
        if data.departments != 'ALL':
            codes = data.departments.split(',')
            known = {code for code, in db.query(Department.code).filter(Department.code.in_(codes))}
            if set(codes) != known:
                raise PlacementError('Unknown department code in eligible list', 422)
        drive = self.drive(db, drive_id, lock=True) if drive_id else PlacementDrive()
        if drive_id and drive.status not in ('DRAFT', 'OPEN'):
            raise PlacementError('Only DRAFT or OPEN drives may be edited')
        if data.status not in ('DRAFT', 'OPEN') or (drive_id and drive.status == 'OPEN' and data.status == 'DRAFT'):
            raise PlacementError('Use explicit lifecycle actions; only DRAFT to OPEN is allowed when editing')
        for name, value in data.model_dump().items():
            setattr(drive, name, value)
        db.add(drive)
        db.flush()
        return drive_record(drive)

    def transition(self, db, drive_id, action):
        drive = self.drive(db, drive_id, lock=True)
        if action == 'close' and drive.status in ACTIVE:
            drive.status = 'CLOSED'
        elif action == 'cancel' and drive.status != 'CANCELLED':
            drive.status = 'CANCELLED'
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
        return dict(usn=student.usn, drive=drive_record(drive), **result)

    def shortlist(self, db, drive_id):
        self.drive(db, drive_id)
        rows = (db.query(PlacementShortlist, Student.name).join(Student, Student.usn == PlacementShortlist.usn)
                .filter(PlacementShortlist.drive_id == drive_id)
                .order_by(PlacementShortlist.eligible.desc(), PlacementShortlist.ml_probability.desc().nullslast(), PlacementShortlist.usn))
        return [dict(usn=row.usn, name=name, eligible=row.eligible, ml_probability=row.ml_probability,
                     model_version=row.model_version, reasons=row.reasons, updated_at=row.updated_at) for row, name in rows]

    def record_outcome(self, db, drive_id, usn, data):
        self.drive(db, drive_id, lock=True)
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
        event = ('placement.' + data.outcome_status.lower(), dict(drive_id=drive_id, usn=usn,
                 outcome_status=data.outcome_status, package_offered=data.package_offered))
        return outcome_record(outcome), [event]

    def outcomes(self, db, drive_id):
        self.drive(db, drive_id)
        return [outcome_record(row) for row in db.query(PlacementOutcome).filter_by(drive_id=drive_id).order_by(PlacementOutcome.usn)]

    def stats(self, db):
        eligible = (db.query(Student.dept_code, func.count(func.distinct(PlacementShortlist.usn)))
                    .join(PlacementShortlist, PlacementShortlist.usn == Student.usn)
                    .filter(PlacementShortlist.eligible.is_(True)).group_by(Student.dept_code).all())
        return dict(upcoming_drives=len(self.active_drives(db)), eligible_finalists_by_dept=dict(eligible))
