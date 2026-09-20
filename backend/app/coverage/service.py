"""Deterministic, provider-free faculty absence and runtime coverage policy."""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func

from ..agents.attendance import overall_percentage
from ..models import AttendanceRecord, Faculty, Student, User, utcnow
from ..notifications import notify_role, notify_users
from ..timetable import models as tm
from .models import (AttendanceSheet, CoverageAssignment, CoverageAudit,
                     CoverageRequest, FacultyAbsence)

TZ = ZoneInfo("Asia/Kolkata")
ACTIVE_REQUESTS = ("PENDING", "CANDIDATES_AVAILABLE", "APPROVED")
ACTIVE_ASSIGNMENTS = ("PROPOSED", "ACCEPTED")


def business_today() -> dt.date:
    return dt.datetime.now(TZ).date()


def fail(status: int, message: str):
    raise HTTPException(status, message)


def faculty_user(db, faculty_id: int) -> User | None:
    return (db.query(User).filter(User.faculty_id == faculty_id,
                                  User.role.in_(("faculty", "hod"))).order_by(
                                      User.role.desc(), User.id).first())


def is_hod_faculty(db, faculty_id: int) -> bool:
    return db.query(User.id).filter(User.faculty_id == faculty_id, User.role == "hod").first() is not None


def audit(db, user, action, *, absence=None, request=None, occurrence_item=None,
          from_status=None, to_status=None, detail=None):
    db.add(CoverageAudit(
        actor_user_id=user.id, action=action,
        absence_id=absence.id if absence else None,
        coverage_request_id=request.id if request else None,
        timetable_entry_id=(request.timetable_entry_id if request else
                            occurrence_item["entry"].id if occurrence_item else None),
        occurrence_date=(request.occurrence_date if request else
                         occurrence_item["date"] if occurrence_item else None),
        from_status=from_status, to_status=to_status,
        reason_category=absence.reason_category if absence else None,
        detail=json.dumps(detail or {}, sort_keys=True, separators=(",", ":"))[:2000]))


def absence_record(db, row: FacultyAbsence, *, private=False) -> dict:
    faculty = db.get(Faculty, row.faculty_id)
    result = {
        "id": row.id, "faculty_id": row.faculty_id,
        "faculty_name": faculty.name if faculty else "Faculty",
        "department": faculty.dept_code if faculty else None,
        "starts_on": row.starts_on, "ends_on": row.ends_on,
        "period_id": row.period_id, "reason_category": row.reason_category,
        "status": row.status, "submitted_by": row.submitted_by,
        "reviewed_by": row.reviewed_by, "reviewed_at": row.reviewed_at,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }
    if private:
        result["private_note"] = row.private_note
    affected = []
    for request in db.query(CoverageRequest).filter_by(absence_id=row.id).order_by(
            CoverageRequest.occurrence_date, CoverageRequest.id).all():
        entry = db.get(tm.Entry, request.timetable_entry_id)
        section = db.get(tm.Section, entry.section_id) if entry else None
        period = (db.query(tm.PeriodDefinition).filter_by(
            term_id=entry.term_id, day_of_week=entry.day_of_week,
            period_index=entry.period_index).one_or_none()) if entry else None
        affected.append({"coverage_request_id": request.id,
                         "occurrence_id": request.timetable_entry_id,
                         "date": request.occurrence_date,
                         "subject_code": entry.subject_code if entry else None,
                         "year": section.year if section else None,
                         "section": section.name if section else None,
                         "start_time": period.starts_at if period else None,
                         "end_time": period.ends_at if period else None,
                         "status": request.status})
    result["affected_classes"] = affected
    return result


def occurrence(db, entry_id: int, date: dt.date) -> dict:
    entry = (db.query(tm.Entry).join(tm.Run, tm.Run.id == tm.Entry.run_id)
             .filter(tm.Entry.id == entry_id, tm.Run.status == "PUBLISHED").one_or_none())
    if entry is None:
        fail(404, "Published timetable occurrence not found.")
    run = db.get(tm.Run, entry.run_id)
    term = db.get(tm.Term, entry.term_id)
    section = db.get(tm.Section, entry.section_id)
    period = (db.query(tm.PeriodDefinition).filter_by(
        term_id=entry.term_id, day_of_week=entry.day_of_week,
        period_index=entry.period_index).one_or_none())
    if (term is None or section is None or period is None or date < term.starts_on
            or date > term.ends_on or date.weekday() != entry.day_of_week
            or period.is_break or period.is_closed):
        fail(409, "The requested class is not an active published timetable occurrence.")
    if db.query(tm.Holiday.id).filter_by(term_id=term.id, date=date).first():
        fail(409, "Attendance and coverage are unavailable on an institution holiday.")
    return {
        "entry": entry, "run": run, "term": term, "section": section, "period": period,
        "date": date, "dept": entry.dept_code, "year": section.year,
        "semester": section.semester, "section_name": section.name,
        "subject_code": entry.subject_code, "original_faculty_id": entry.faculty_id,
        "start_time": period.starts_at, "end_time": period.ends_at,
    }


def occurrence_record(db, item: dict) -> dict:
    original = db.get(Faculty, item["original_faculty_id"])
    return {
        "occurrence_id": item["entry"].id, "date": item["date"],
        "term_id": item["term"].id, "term": item["term"].name,
        "department": item["dept"], "year": item["year"],
        "semester": item["semester"], "section": item["section_name"],
        "subject_code": item["subject_code"],
        "original_faculty_id": item["original_faculty_id"],
        "original_faculty": original.name if original else "Faculty",
        "period_index": item["entry"].period_index,
        "start_time": item["start_time"], "end_time": item["end_time"],
    }


def stored_occurrence_record(db, entry_id: int, date: dt.date) -> dict:
    """Serialize immutable history without treating an archived run as current."""
    entry = db.get(tm.Entry, entry_id)
    if entry is None:
        fail(404, "Timetable occurrence history not found.")
    term = db.get(tm.Term, entry.term_id)
    section = db.get(tm.Section, entry.section_id)
    period = db.query(tm.PeriodDefinition).filter_by(
        term_id=entry.term_id, day_of_week=entry.day_of_week,
        period_index=entry.period_index).one_or_none()
    original = db.get(Faculty, entry.faculty_id)
    return {"occurrence_id": entry.id, "date": date, "term_id": entry.term_id,
            "term": term.name if term else None, "department": entry.dept_code,
            "year": section.year if section else None,
            "semester": section.semester if section else None,
            "section": section.name if section else None,
            "subject_code": entry.subject_code,
            "original_faculty_id": entry.faculty_id,
            "original_faculty": original.name if original else "Faculty",
            "period_index": entry.period_index,
            "start_time": period.starts_at if period else None,
            "end_time": period.ends_at if period else None}


def absence_covers(db, faculty_id: int, item: dict) -> bool:
    query = db.query(FacultyAbsence.id).filter(
        FacultyAbsence.faculty_id == faculty_id,
        FacultyAbsence.status == "APPROVED",
        FacultyAbsence.starts_on <= item["date"], FacultyAbsence.ends_on >= item["date"])
    return query.filter((FacultyAbsence.period_id.is_(None)) |
                        (FacultyAbsence.period_id == item["period"].id)).first() is not None


def affected_occurrences(db, absence: FacultyAbsence) -> list[dict]:
    results = []
    day = absence.starts_on
    while day <= absence.ends_on:
        entries = (db.query(tm.Entry).join(tm.Run, tm.Run.id == tm.Entry.run_id)
                   .join(tm.Term, tm.Term.id == tm.Entry.term_id)
                   .filter(tm.Run.status == "PUBLISHED", tm.Entry.faculty_id == absence.faculty_id,
                           tm.Entry.day_of_week == day.weekday(), tm.Term.starts_on <= day,
                           tm.Term.ends_on >= day).order_by(tm.Entry.id).all())
        for entry in entries:
            try:
                item = occurrence(db, entry.id, day)
            except HTTPException:
                continue
            if absence.period_id is None or absence.period_id == item["period"].id:
                results.append(item)
        day += dt.timedelta(days=1)
    return results


def create_absence(db, user, body) -> tuple[dict, list[tuple[str, dict]]]:
    if not user.faculty_id or user.role not in {"faculty", "hod"}:
        fail(403, "A faculty identity is required.")
    if body.starts_on < business_today():
        fail(422, "Faculty absence cannot be submitted for a past date.")
    if body.period_id is not None and db.get(tm.PeriodDefinition, body.period_id) is None:
        fail(404, "Timetable period not found.")
    row = FacultyAbsence(faculty_id=user.faculty_id, submitted_by=user.id,
                         **body.model_dump())
    db.add(row); db.flush()
    audit(db, user, "faculty.absence_created", absence=row, to_status="DRAFT")
    return absence_record(db, row, private=True), []


def own_absences(db, user) -> list[dict]:
    if not user.faculty_id or user.role not in {"faculty", "hod"}:
        fail(403, "A faculty identity is required.")
    return [absence_record(db, row, private=True) for row in db.query(FacultyAbsence).filter_by(
        faculty_id=user.faculty_id).order_by(FacultyAbsence.created_at.desc()).all()]


def own_absence(db, user, absence_id, lock=False) -> FacultyAbsence:
    query = db.query(FacultyAbsence).filter_by(id=absence_id, faculty_id=user.faculty_id)
    if lock:
        query = query.with_for_update()
    row = query.one_or_none()
    if row is None:
        fail(404, "Faculty absence not found.")
    return row


def submit_absence(db, user, absence_id) -> tuple[dict, list[tuple[str, dict]]]:
    row = own_absence(db, user, absence_id, lock=True)
    if row.status != "DRAFT":
        fail(409, "Only a draft absence may be submitted.")
    if row.starts_on < business_today():
        fail(422, "Faculty absence cannot be submitted for a past date.")
    overlapping = db.query(FacultyAbsence.id).filter(
        FacultyAbsence.id != row.id, FacultyAbsence.faculty_id == row.faculty_id,
        FacultyAbsence.status.in_(("SUBMITTED", "APPROVED")),
        FacultyAbsence.starts_on <= row.ends_on,
        FacultyAbsence.ends_on >= row.starts_on,
        (FacultyAbsence.period_id.is_(None)) | (row.period_id is None) |
        (FacultyAbsence.period_id == row.period_id)).first()
    if overlapping:
        fail(409, "An active absence request already covers this date and period.")
    row.status = "SUBMITTED"; row.updated_at = utcnow()
    audit(db, user, "faculty.absence_submitted", absence=row,
          from_status="DRAFT", to_status="SUBMITTED")
    faculty = db.get(Faculty, row.faculty_id)
    hod_absence = user.role == "hod"
    if hod_absence:
        for role in ("principal", "admin"):
            notify_role(db, role, title="HOD absence awaiting review",
                        message=f"A {faculty.dept_code} HOD absence requires review.",
                        notification_type="FACULTY_ABSENCE_SUBMITTED",
                        source_agent="coverage_service", event_key=f"absence_submitted:{row.id}:{role}",
                        route="/coverage/escalations", related_entity_type="faculty_absence",
                        related_entity_id=row.id)
    else:
        notify_role(db, "hod", dept=faculty.dept_code, title="Faculty absence awaiting review",
                    message="A department faculty absence requires review.",
                    notification_type="FACULTY_ABSENCE_SUBMITTED",
                    source_agent="coverage_service", event_key=f"absence_submitted:{row.id}:hod",
                    route="/hod/coverage", related_entity_type="faculty_absence",
                    related_entity_id=row.id)
    return absence_record(db, row, private=True), [("faculty.absence_submitted", {
        "absence_id": row.id, "department": faculty.dept_code,
        "reason_category": row.reason_category, "hod_escalation": hod_absence})]


def cancel_absence(db, user, absence_id) -> tuple[dict, list[tuple[str, dict]]]:
    row = own_absence(db, user, absence_id, lock=True)
    if row.status not in {"DRAFT", "SUBMITTED", "APPROVED"}:
        fail(409, "This absence can no longer be cancelled.")
    if row.ends_on < business_today():
        fail(409, "Historical absence and coverage records cannot be changed.")
    request_ids = [request_id for request_id, in db.query(CoverageRequest.id).filter_by(
        absence_id=row.id).all()]
    if request_ids and db.query(AttendanceSheet.id).join(
            CoverageAssignment,
            CoverageAssignment.id == AttendanceSheet.coverage_assignment_id).filter(
                CoverageAssignment.coverage_request_id.in_(request_ids)).first():
        fail(409, "Attendance was already submitted; a correction workflow is not available.")
    old = row.status; row.status = "CANCELLED"; row.updated_at = utcnow()
    requests = db.query(CoverageRequest).filter_by(absence_id=row.id).with_for_update().all()
    for request in requests:
        request.status = "CANCELLED"
        for assignment in db.query(CoverageAssignment).filter_by(
                coverage_request_id=request.id).all():
            if assignment.status in ACTIVE_ASSIGNMENTS:
                assignment.status = "CANCELLED"; assignment.cancelled_at = utcnow()
    audit(db, user, "faculty.absence_cancelled", absence=row,
          from_status=old, to_status="CANCELLED")
    return absence_record(db, row, private=True), [("faculty.absence_cancelled", {
        "absence_id": row.id, "cancelled_occurrences": len(requests)})]


def review_queue(db, user, *, escalated=False) -> list[dict]:
    if escalated:
        if user.role not in {"principal", "admin"}:
            fail(403, "Principal or Admin approval is required.")
        query = db.query(FacultyAbsence).filter(FacultyAbsence.status == "SUBMITTED")
        rows = [row for row in query.order_by(FacultyAbsence.starts_on).all()
                if is_hod_faculty(db, row.faculty_id)]
    else:
        if user.role != "hod":
            fail(403, "HOD approval is required.")
        rows = (db.query(FacultyAbsence).join(Faculty, Faculty.id == FacultyAbsence.faculty_id)
                .filter(Faculty.dept_code == user.dept_code,
                        FacultyAbsence.status == "SUBMITTED").order_by(
                            FacultyAbsence.starts_on).all())
        rows = [row for row in rows if not is_hod_faculty(db, row.faculty_id)]
    return [absence_record(db, row, private=True) for row in rows]


def authorize_reviewer(db, user, row: FacultyAbsence):
    faculty = db.get(Faculty, row.faculty_id)
    target_is_hod = is_hod_faculty(db, row.faculty_id)
    if user.id == row.submitted_by or user.faculty_id == row.faculty_id:
        fail(403, "You cannot review your own absence.")
    if target_is_hod:
        if user.role not in {"principal", "admin"}:
            fail(404, "Faculty absence not found in your authorized scope.")
    elif user.role != "hod" or user.dept_code != faculty.dept_code:
        fail(404, "Faculty absence not found in your authorized scope.")
    return faculty, target_is_hod


def review_absence(db, user, absence_id, decision: str) -> tuple[dict, list[tuple[str, dict]]]:
    row = db.query(FacultyAbsence).filter_by(id=absence_id).with_for_update().one_or_none()
    if row is None:
        fail(404, "Faculty absence not found.")
    faculty, target_is_hod = authorize_reviewer(db, user, row)
    if row.status != "SUBMITTED":
        fail(409, "Only a submitted absence may be reviewed.")
    now = utcnow(); row.reviewed_by = user.id; row.reviewed_at = now; row.updated_at = now
    events = []
    if decision == "REJECT":
        row.status = "REJECTED"
        audit(db, user, "faculty.absence_rejected", absence=row,
              from_status="SUBMITTED", to_status="REJECTED")
        events.append(("faculty.absence_rejected", {"absence_id": row.id,
                       "department": faculty.dept_code, "reason_category": row.reason_category}))
    else:
        row.status = "APPROVED"
        created = []
        for item in affected_occurrences(db, row):
            request = db.query(CoverageRequest).filter_by(
                timetable_entry_id=item["entry"].id, occurrence_date=item["date"]).filter(
                    CoverageRequest.status.in_(ACTIVE_REQUESTS)).one_or_none()
            if request is None:
                request = CoverageRequest(
                    absence_id=row.id, timetable_entry_id=item["entry"].id,
                    timetable_run_id=item["run"].id, term_id=item["term"].id,
                    occurrence_date=item["date"], original_faculty_id=row.faculty_id,
                    status="PENDING", safe_reason="Approved faculty absence",
                    requested_by=row.submitted_by)
                db.add(request); db.flush(); created.append(request.id)
                audit(db, user, "coverage.requested", absence=row, request=request,
                      to_status="PENDING")
                eligible = candidates(db, user, request.id, mutate_status=True)
                events.append(("coverage.requested", {"coverage_request_id": request.id,
                               "absence_id": row.id, "occurrence_id": item["entry"].id,
                               "date": str(item["date"]), "department": faculty.dept_code}))
                if eligible:
                    events.append(("coverage.candidates_ready", {
                        "coverage_request_id": request.id,
                        "occurrence_id": item["entry"].id,
                        "date": str(item["date"]),
                        "candidate_count": len(eligible),
                        "department": faculty.dept_code,
                    }))
        audit(db, user, "faculty.absence_approved", absence=row,
              from_status="SUBMITTED", to_status="APPROVED",
              detail={"affected_occurrence_count": len(created)})
        events.insert(0, ("faculty.absence_approved", {"absence_id": row.id,
                      "department": faculty.dept_code, "affected_occurrence_count": len(created),
                      "hod_escalation": target_is_hod}))
    notify_users(db, [row.submitted_by], title=f"Absence {row.status.lower()}",
                 message=f"Your {row.reason_category.replace('_', ' ').lower()} absence is {row.status.lower()}.",
                 notification_type=f"FACULTY_ABSENCE_{row.status}", source_agent="coverage_service",
                 event_key=f"absence_review:{row.id}:{row.status}", route="/faculty/coverage",
                 related_entity_type="faculty_absence", related_entity_id=row.id)
    return absence_record(db, row, private=True), events


def request_scope(db, user, request_id: int, lock=False) -> tuple[CoverageRequest, FacultyAbsence, Faculty, bool]:
    query = db.query(CoverageRequest).filter_by(id=request_id)
    if lock:
        query = query.with_for_update()
    request = query.one_or_none()
    if request is None:
        fail(404, "Coverage request not found.")
    absence = db.get(FacultyAbsence, request.absence_id)
    faculty = db.get(Faculty, request.original_faculty_id)
    target_is_hod = is_hod_faculty(db, request.original_faculty_id)
    if target_is_hod:
        if user.role not in {"principal", "admin"}:
            fail(404, "Coverage request not found in your authorized scope.")
    elif user.role != "hod" or user.dept_code != faculty.dept_code:
        fail(404, "Coverage request not found in your authorized scope.")
    if user.faculty_id in {request.original_faculty_id}:
        fail(403, "You cannot approve coverage for your own absence.")
    return request, absence, faculty, target_is_hod


def coverage_queue(db, user, *, escalated=False) -> list[dict]:
    if escalated and user.role not in {"principal", "admin"}:
        fail(403, "Principal or Admin coverage approval is required.")
    if not escalated and user.role != "hod":
        fail(403, "HOD coverage approval is required.")
    rows = db.query(CoverageRequest).filter(
        CoverageRequest.status.in_(ACTIVE_REQUESTS),
        CoverageRequest.occurrence_date >= business_today()).order_by(
        CoverageRequest.occurrence_date, CoverageRequest.id).all()
    result = []
    for row in rows:
        faculty = db.get(Faculty, row.original_faculty_id)
        target_is_hod = is_hod_faculty(db, row.original_faculty_id)
        if target_is_hod != escalated:
            continue
        if not escalated and faculty.dept_code != user.dept_code:
            continue
        try:
            item = occurrence(db, row.timetable_entry_id, row.occurrence_date)
        except HTTPException:
            # A superseded timetable version is not actionable. GET remains
            # read-only; an authorized cancellation action retains history.
            continue
        result.append({"id": row.id, "status": row.status,
                       **occurrence_record(db, item)})
    return result


def _week_bounds(date: dt.date) -> tuple[dt.date, dt.date]:
    start = date - dt.timedelta(days=date.weekday())
    return start, start + dt.timedelta(days=6)


def candidates(db, user, request_id: int, *, mutate_status=True) -> list[dict]:
    request, _, original, _ = request_scope(db, user, request_id, lock=mutate_status)
    if request.status in {"DECLINED", "UNFILLED", "CANCELLED"}:
        fail(409, "This coverage request is closed.")
    item = occurrence(db, request.timetable_entry_id, request.occurrence_date)
    faculty_rows = (db.query(Faculty).join(User, User.faculty_id == Faculty.id)
                    .filter(Faculty.dept_code == item["dept"], Faculty.id != original.id,
                            User.role.in_(("faculty", "hod"))).distinct().order_by(
                                Faculty.name, Faculty.id).all())
    start_week, end_week = _week_bounds(item["date"])
    output = []
    for faculty in faculty_rows:
        if db.get(tm.Qualification, (faculty.id, item["subject_code"])) is None:
            continue
        if absence_covers(db, faculty.id, item):
            continue
        if db.get(tm.FacultyUnavailable, (faculty.id, item["period"].id)) is not None:
            continue
        scheduled = (db.query(tm.Entry.id).join(tm.Run, tm.Run.id == tm.Entry.run_id)
                     .filter(tm.Run.status == "PUBLISHED", tm.Entry.term_id == item["term"].id,
                             tm.Entry.faculty_id == faculty.id,
                             tm.Entry.day_of_week == item["date"].weekday(),
                             tm.Entry.period_index == item["entry"].period_index).first())
        if scheduled:
            continue
        coverage_conflict = (db.query(CoverageAssignment.id).join(
            CoverageRequest, CoverageRequest.id == CoverageAssignment.coverage_request_id).join(
            tm.Entry, tm.Entry.id == CoverageAssignment.timetable_entry_id).filter(
                CoverageAssignment.substitute_faculty_id == faculty.id,
                CoverageAssignment.status.in_(ACTIVE_ASSIGNMENTS),
                CoverageAssignment.coverage_request_id != request.id,
                CoverageAssignment.occurrence_date == item["date"],
                CoverageAssignment.period_id == item["period"].id).first())
        if coverage_conflict:
            continue
        planned_daily = (db.query(func.count(tm.Entry.id)).join(tm.Run, tm.Run.id == tm.Entry.run_id)
                         .filter(tm.Run.status == "PUBLISHED", tm.Entry.term_id == item["term"].id,
                                 tm.Entry.faculty_id == faculty.id,
                                 tm.Entry.day_of_week == item["date"].weekday()).scalar() or 0)
        planned_weekly = (db.query(func.count(tm.Entry.id)).join(tm.Run, tm.Run.id == tm.Entry.run_id)
                          .filter(tm.Run.status == "PUBLISHED", tm.Entry.term_id == item["term"].id,
                                  tm.Entry.faculty_id == faculty.id).scalar() or 0)
        accepted = (db.query(CoverageAssignment).filter(
            CoverageAssignment.substitute_faculty_id == faculty.id,
            CoverageAssignment.status == "ACCEPTED",
            CoverageAssignment.occurrence_date.between(start_week, end_week)).all())
        coverage_daily = sum(row.occurrence_date == item["date"] for row in accepted)
        coverage_weekly = len(accepted)
        limit = db.get(tm.FacultyLimit, (faculty.id, item["term"].id))
        daily = int(planned_daily) + coverage_daily
        weekly = int(planned_weekly) + coverage_weekly
        if limit and (daily + 1 > limit.daily_limit or weekly + 1 > limit.weekly_limit):
            continue
        output.append({"faculty_id": faculty.id, "name": faculty.name,
                       "department": faculty.dept_code, "daily_load": daily,
                       "weekly_load": weekly,
                       "reason": "Same department, qualified, available, and within configured load limits."})
    output.sort(key=lambda row: (row["daily_load"], row["weekly_load"],
                                 row["name"].casefold(), row["faculty_id"]))
    if mutate_status and request.status == "PENDING":
        old = request.status
        request.status = "CANDIDATES_AVAILABLE" if output else "PENDING"
        if request.status != old:
            audit(db, user, "coverage.candidates_ready", request=request,
                  from_status=old, to_status=request.status,
                  detail={"candidate_count": len(output)})
    return output


def approve_candidate(db, user, request_id: int, faculty_id: int) -> tuple[dict, list[tuple[str, dict]]]:
    request, absence, original, _ = request_scope(db, user, request_id, lock=True)
    if user.faculty_id == faculty_id:
        fail(403, "You cannot approve your own coverage assignment.")
    if request.status in {"DECLINED", "UNFILLED", "CANCELLED"}:
        fail(409, "This coverage request is closed.")
    eligible = {row["faculty_id"]: row for row in candidates(db, user, request_id, mutate_status=False)}
    if faculty_id not in eligible:
        fail(409, "The selected faculty member is not an eligible substitute.")
    existing_active = db.query(CoverageAssignment).filter_by(
        coverage_request_id=request.id).filter(
            CoverageAssignment.status.in_(ACTIVE_ASSIGNMENTS)).first()
    if existing_active:
        fail(409, "This occurrence already has an active coverage proposal.")
    assignment = CoverageAssignment(
        coverage_request_id=request.id, substitute_faculty_id=faculty_id,
        timetable_entry_id=request.timetable_entry_id,
        period_id=occurrence(db, request.timetable_entry_id, request.occurrence_date)["period"].id,
        occurrence_date=request.occurrence_date, status="PROPOSED")
    db.add(assignment); db.flush()
    old = request.status; request.status = "APPROVED"; request.approved_by = user.id
    request.approved_at = utcnow()
    audit(db, user, "coverage.assigned", absence=absence, request=request,
          from_status=old, to_status="APPROVED",
          detail={"assignment_id": assignment.id, "substitute_faculty_id": faculty_id})
    candidate_user = faculty_user(db, faculty_id)
    if candidate_user:
        notify_users(db, [candidate_user.id], title="Class coverage proposed",
                     message=f"Coverage for {request.occurrence_date.isoformat()} requires your response.",
                     notification_type="COVERAGE_ASSIGNED", source_agent="coverage_service",
                     event_key=f"coverage_assigned:{assignment.id}", route="/faculty/coverage",
                     related_entity_type="coverage_assignment", related_entity_id=assignment.id)
    notify_users(db, [absence.submitted_by], title="Class coverage proposed",
                 message=f"Coverage for your class on {request.occurrence_date.isoformat()} has been proposed.",
                 notification_type="COVERAGE_ASSIGNED", source_agent="coverage_service",
                 event_key=f"coverage_original:{assignment.id}", route="/faculty/coverage",
                 related_entity_type="coverage_request", related_entity_id=request.id)
    return {"assignment_id": assignment.id, "status": assignment.status,
            "candidate": eligible[faculty_id]}, [("coverage.assigned", {
                "coverage_request_id": request.id, "assignment_id": assignment.id,
                "occurrence_id": request.timetable_entry_id,
                "date": str(request.occurrence_date), "department": original.dept_code})]


def mark_unfilled(db, user, request_id: int) -> tuple[dict, list[tuple[str, dict]]]:
    request, absence, original, _ = request_scope(db, user, request_id, lock=True)
    if request.status in {"DECLINED", "UNFILLED", "CANCELLED"}:
        fail(409, "This coverage request is already closed.")
    if db.query(CoverageAssignment.id).filter_by(coverage_request_id=request.id,
                                                  status="ACCEPTED").first():
        fail(409, "Accepted coverage cannot be marked unfilled.")
    old = request.status; request.status = "UNFILLED"; request.approved_by = user.id
    request.approved_at = utcnow(); request.safe_reason = "No approved substitute is available"
    for row in db.query(CoverageAssignment).filter_by(coverage_request_id=request.id).all():
        if row.status == "PROPOSED":
            row.status = "CANCELLED"; row.cancelled_at = utcnow()
    audit(db, user, "coverage.unfilled", absence=absence, request=request,
          from_status=old, to_status="UNFILLED")
    notify_users(db, [absence.submitted_by], title="Class coverage is unfilled",
                 message=f"No approved substitute is available for {request.occurrence_date.isoformat()}.",
                 notification_type="COVERAGE_UNFILLED", source_agent="coverage_service",
                 event_key=f"coverage_unfilled:{request.id}", route="/faculty/coverage",
                 related_entity_type="coverage_request", related_entity_id=request.id)
    return {"id": request.id, "status": request.status}, [("coverage.unfilled", {
        "coverage_request_id": request.id, "occurrence_id": request.timetable_entry_id,
        "date": str(request.occurrence_date), "department": original.dept_code})]


def decline_request(db, user, request_id: int) -> tuple[dict, list[tuple[str, dict]]]:
    request, absence, original, _ = request_scope(db, user, request_id, lock=True)
    if request.status in {"DECLINED", "UNFILLED", "CANCELLED"}:
        fail(409, "This coverage request is already closed.")
    if db.query(CoverageAssignment.id).filter_by(
            coverage_request_id=request.id, status="ACCEPTED").first():
        fail(409, "Accepted coverage cannot be declined.")
    old = request.status
    request.status = "DECLINED"; request.approved_by = user.id
    request.approved_at = utcnow(); request.safe_reason = "Coverage declined by authorized approver"
    for row in db.query(CoverageAssignment).filter_by(coverage_request_id=request.id).all():
        if row.status == "PROPOSED":
            row.status = "CANCELLED"; row.cancelled_at = utcnow()
    audit(db, user, "coverage.declined", absence=absence, request=request,
          from_status=old, to_status="DECLINED")
    notify_users(db, [absence.submitted_by], title="Class coverage declined",
                 message=f"Coverage for {request.occurrence_date.isoformat()} was declined by the authorized approver.",
                 notification_type="COVERAGE_DECLINED", source_agent="coverage_service",
                 event_key=f"coverage_request_declined:{request.id}", route="/faculty/coverage",
                 related_entity_type="coverage_request", related_entity_id=request.id)
    return {"id": request.id, "status": request.status}, [("coverage.declined", {
        "coverage_request_id": request.id, "occurrence_id": request.timetable_entry_id,
        "date": str(request.occurrence_date), "department": original.dept_code})]


def my_assignments(db, user) -> list[dict]:
    if not user.faculty_id or user.role not in {"faculty", "hod"}:
        fail(403, "A faculty identity is required.")
    rows = db.query(CoverageAssignment).filter_by(
        substitute_faculty_id=user.faculty_id).order_by(
            CoverageAssignment.occurrence_date.desc()).all()
    result = []
    for row in rows:
        result.append({"id": row.id, "status": row.status,
                       **stored_occurrence_record(db, row.timetable_entry_id,
                                                  row.occurrence_date)})
    return result


def respond_assignment(db, user, assignment_id: int, accept: bool) -> tuple[dict, list[tuple[str, dict]]]:
    row = db.query(CoverageAssignment).filter_by(
        id=assignment_id, substitute_faculty_id=user.faculty_id).with_for_update().one_or_none()
    if row is None:
        fail(404, "Coverage assignment not found.")
    if row.status != "PROPOSED":
        fail(409, "This coverage proposal has already been resolved.")
    request = db.query(CoverageRequest).filter_by(id=row.coverage_request_id).with_for_update().one()
    if accept:
        # Re-evaluate every deterministic rule at acceptance time to close the
        # gap between proposal and response.
        approver = db.get(User, request.approved_by)
        eligible = {item["faculty_id"] for item in candidates(
            db, approver, request.id, mutate_status=False)} if approver else set()
        if row.substitute_faculty_id not in eligible:
            fail(409, "This coverage proposal is no longer eligible.")
        row.status = "ACCEPTED"
        event = "coverage.accepted"
    else:
        row.status = "DECLINED"; request.status = "CANDIDATES_AVAILABLE"
        event = "coverage.declined"
    row.responded_at = utcnow()
    absence = db.get(FacultyAbsence, request.absence_id)
    audit(db, user, event, absence=absence, request=request,
          from_status="PROPOSED", to_status=row.status,
          detail={"assignment_id": row.id})
    notify_users(db, [absence.submitted_by],
                 title=f"Coverage {row.status.lower()}",
                 message=f"The proposed substitute responded for {request.occurrence_date.isoformat()}.",
                 notification_type=event.upper().replace(".", "_"),
                 source_agent="coverage_service", event_key=f"coverage_response:{row.id}:{row.status}",
                 route="/faculty/coverage",
                 related_entity_type="coverage_assignment", related_entity_id=row.id)
    if request.approved_by and request.approved_by != absence.submitted_by:
        approver = db.get(User, request.approved_by)
        notify_users(db, [request.approved_by], title=f"Coverage {row.status.lower()}",
                     message=f"The proposed substitute responded for {request.occurrence_date.isoformat()}.",
                     notification_type=event.upper().replace(".", "_"),
                     source_agent="coverage_service",
                     event_key=f"coverage_approver_response:{row.id}:{row.status}",
                     route=("/coverage/escalations" if approver and approver.role in {"principal", "admin"}
                            else "/hod/coverage"),
                     related_entity_type="coverage_assignment", related_entity_id=row.id)
    return {"id": row.id, "status": row.status}, [(event, {
        "coverage_request_id": request.id, "assignment_id": row.id,
        "occurrence_id": request.timetable_entry_id,
        "date": str(request.occurrence_date)})]


def attendance_occurrences(db, user) -> list[dict]:
    if not user.faculty_id or user.role not in {"faculty", "hod"}:
        fail(403, "A faculty identity is required.")
    today = business_today()
    entries = (db.query(tm.Entry).join(tm.Run, tm.Run.id == tm.Entry.run_id)
               .join(tm.Term, tm.Term.id == tm.Entry.term_id)
               .filter(tm.Run.status == "PUBLISHED", tm.Term.starts_on <= today,
                       tm.Term.ends_on >= today, tm.Entry.day_of_week == today.weekday()).all())
    permitted = []
    for entry in entries:
        try:
            item = occurrence(db, entry.id, today)
        except HTTPException:
            continue
        mode = None
        if entry.faculty_id == user.faculty_id and not absence_covers(db, user.faculty_id, item):
            mode = "NORMAL"
        assignment = (db.query(CoverageAssignment).join(
            CoverageRequest, CoverageRequest.id == CoverageAssignment.coverage_request_id)
            .filter(CoverageAssignment.substitute_faculty_id == user.faculty_id,
                    CoverageAssignment.status == "ACCEPTED",
                    CoverageAssignment.timetable_entry_id == entry.id,
                    CoverageAssignment.occurrence_date == today,
                    CoverageRequest.status == "APPROVED").one_or_none())
        if assignment:
            mode = "SUBSTITUTE"
        if mode:
            submitted = db.query(AttendanceSheet.id).filter_by(
                timetable_entry_id=entry.id, occurrence_date=today).first() is not None
            permitted.append({**occurrence_record(db, item), "marking_mode": mode,
                              "submitted": submitted})
    return sorted(permitted, key=lambda row: (row["start_time"], row["occurrence_id"]))


def attendance_roster(db, user, entry_id: int) -> dict:
    authorized = {item["occurrence_id"]: item for item in attendance_occurrences(db, user)}
    if entry_id not in authorized:
        fail(404, "Authorized attendance occurrence not found.")
    selected = authorized[entry_id]
    item = occurrence(db, entry_id, selected["date"])
    students = (db.query(Student).filter_by(
        dept_code=item["dept"], year=item["year"], semester=item["semester"],
        section=item["section_name"]).order_by(Student.usn).all())
    return {"occurrence": selected, "roster": [{
        "usn": student.usn, "name": student.name,
        "attendance": overall_percentage(db, student.usn),
    } for student in students]}


def submit_attendance(db, user, body) -> tuple[dict, list[tuple[str, dict]]]:
    if not user.faculty_id or user.role not in {"faculty", "hod"}:
        fail(403, "Only the authorized teaching faculty may mark this occurrence.")
    item = occurrence(db, body.occurrence_id, body.date)
    supplied = (body.dept.upper(), body.year, body.section.upper(), body.subject_code.upper())
    actual = (item["dept"], item["year"], item["section_name"], item["subject_code"])
    if supplied != actual:
        fail(422, "Attendance class fields do not match the published timetable occurrence.")
    if body.date != business_today():
        fail(409, "Attendance may be submitted only for today's published occurrence.")
    if db.query(AttendanceSheet.id).filter_by(
            timetable_entry_id=item["entry"].id, occurrence_date=body.date).first():
        fail(409, "Attendance was already submitted; a correction workflow is not available.")
    assignment = None
    mode = None
    if (user.faculty_id == item["original_faculty_id"]
            and not absence_covers(db, user.faculty_id, item)):
        mode = "NORMAL"
    else:
        assignment = (db.query(CoverageAssignment).join(
            CoverageRequest, CoverageRequest.id == CoverageAssignment.coverage_request_id)
            .filter(CoverageAssignment.substitute_faculty_id == user.faculty_id,
                    CoverageAssignment.status == "ACCEPTED",
                    CoverageAssignment.timetable_entry_id == item["entry"].id,
                    CoverageAssignment.occurrence_date == body.date,
                    CoverageRequest.status == "APPROVED").one_or_none())
        if assignment:
            mode = "SUBSTITUTE"
    if mode is None:
        if user.faculty_id == item["original_faculty_id"] and absence_covers(
                db, user.faculty_id, item):
            fail(403, "The absent original faculty member cannot mark this occurrence.")
        fail(403, "Only the original faculty or an accepted approved substitute may mark this occurrence.")
    roster = (db.query(Student).filter_by(dept_code=item["dept"], year=item["year"],
                                          semester=item["semester"],
                                          section=item["section_name"])
              .order_by(Student.usn).all())
    roster_usns = {student.usn for student in roster}
    absent = {str(usn).strip().upper() for usn in body.absent_usns}
    if not absent <= roster_usns:
        fail(422, "Attendance contains a student outside the server-authorized roster.")
    sheet = AttendanceSheet(
        timetable_entry_id=item["entry"].id, timetable_run_id=item["run"].id,
        term_id=item["term"].id, occurrence_date=body.date,
        original_faculty_id=item["original_faculty_id"],
        marking_faculty_id=user.faculty_id,
        coverage_assignment_id=assignment.id if assignment else None,
        marking_mode=mode, submitted_by=user.id)
    db.add(sheet); db.flush()
    records = [AttendanceRecord(
        attendance_sheet_id=sheet.id, usn=student.usn,
        subject_code=item["subject_code"], date=body.date,
        present=student.usn not in absent, uploaded_by=user.username)
        for student in roster]
    db.add_all(records); db.flush()
    audit(db, user, "attendance.marked_by_substitute" if mode == "SUBSTITUTE"
          else "attendance.marked", request=(db.get(CoverageRequest, assignment.coverage_request_id)
                                              if assignment else None),
          occurrence_item=item,
          detail={"attendance_sheet_id": sheet.id, "marking_mode": mode,
                  "record_count": len(records)})
    payload = {"attendance_sheet_id": sheet.id, "uploaded_by": user.username,
               "count": len(records), "_privacy_safe_log": True}
    events = [("attendance.uploaded", payload)]
    if mode == "SUBSTITUTE":
        events.append(("attendance.marked_by_substitute", {
            "attendance_sheet_id": sheet.id, "coverage_assignment_id": assignment.id,
            "occurrence_id": item["entry"].id, "date": str(body.date),
            "department": item["dept"]}))
    return {"accepted": len(records), "rejected": [], "attendance_sheet_id": sheet.id,
            "occurrence_id": item["entry"].id, "marking_mode": mode}, events
