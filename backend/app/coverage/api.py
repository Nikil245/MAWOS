"""Role-authorized APIs for absence, coverage approval, and attendance occurrences."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..auth import require_role
from ..bus import bus
from ..database import get_session
from . import service
from .schemas import AbsenceCreate, AttendanceSubmission, CandidateApproval, ReviewBody

router = APIRouter(prefix="/api/coverage", tags=["faculty coverage"])
faculty = require_role("faculty", "hod")
hod = require_role("hod")
reviewer = require_role("hod", "principal", "admin")
principal_admin = require_role("principal", "admin")


async def write(db, operation):
    try:
        result, events = operation()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "The occurrence changed or was already assigned; refresh and retry.") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(409, "Coverage data changed; refresh and retry.") from None
    for topic, payload in events:
        await bus.publish(topic, payload, source_agent="coverage_service")
    return result


@router.get("/faculty/absences")
def list_my_absences(user=Depends(faculty), db=Depends(get_session)):
    return service.own_absences(db, user)


@router.post("/faculty/absences", status_code=201)
async def create_absence(body: AbsenceCreate, user=Depends(faculty), db=Depends(get_session)):
    return await write(db, lambda: service.create_absence(db, user, body))


@router.get("/faculty/absences/{absence_id}")
def absence_detail(absence_id: int, user=Depends(faculty), db=Depends(get_session)):
    return service.absence_record(db, service.own_absence(db, user, absence_id), private=True)


@router.post("/faculty/absences/{absence_id}/submit")
async def submit_absence(absence_id: int, user=Depends(faculty), db=Depends(get_session)):
    return await write(db, lambda: service.submit_absence(db, user, absence_id))


@router.post("/faculty/absences/{absence_id}/cancel")
async def cancel_absence(absence_id: int, user=Depends(faculty), db=Depends(get_session)):
    return await write(db, lambda: service.cancel_absence(db, user, absence_id))


@router.get("/hod/absence-queue")
def hod_absence_queue(user=Depends(hod), db=Depends(get_session)):
    return service.review_queue(db, user)


@router.get("/escalations/absence-queue")
def hod_absence_escalations(user=Depends(principal_admin), db=Depends(get_session)):
    return service.review_queue(db, user, escalated=True)


@router.post("/absences/{absence_id}/review")
async def review_absence(absence_id: int, body: ReviewBody,
                         user=Depends(reviewer), db=Depends(get_session)):
    return await write(db, lambda: service.review_absence(
        db, user, absence_id, body.decision))


@router.get("/hod/requests")
def hod_coverage_queue(user=Depends(hod), db=Depends(get_session)):
    return service.coverage_queue(db, user)


@router.get("/escalations/requests")
def hod_coverage_escalations(user=Depends(principal_admin), db=Depends(get_session)):
    return service.coverage_queue(db, user, escalated=True)


@router.get("/requests/{request_id}/candidates")
def candidate_list(request_id: int, user=Depends(reviewer), db=Depends(get_session)):
    # GET is strictly read-only; candidate state is recomputed deterministically.
    with db.no_autoflush:
        return service.candidates(db, user, request_id, mutate_status=False)


@router.post("/requests/{request_id}/approve")
async def approve_candidate(request_id: int, body: CandidateApproval,
                            user=Depends(reviewer), db=Depends(get_session)):
    return await write(db, lambda: service.approve_candidate(
        db, user, request_id, body.substitute_faculty_id))


@router.post("/requests/{request_id}/unfilled")
async def mark_unfilled(request_id: int, user=Depends(reviewer), db=Depends(get_session)):
    return await write(db, lambda: service.mark_unfilled(db, user, request_id))


@router.post("/requests/{request_id}/decline")
async def decline_coverage_request(request_id: int, user=Depends(reviewer), db=Depends(get_session)):
    return await write(db, lambda: service.decline_request(db, user, request_id))


@router.get("/faculty/assignments")
def list_my_assignments(user=Depends(faculty), db=Depends(get_session)):
    return service.my_assignments(db, user)


@router.post("/faculty/assignments/{assignment_id}/accept")
async def accept_assignment(assignment_id: int, user=Depends(faculty), db=Depends(get_session)):
    return await write(db, lambda: service.respond_assignment(
        db, user, assignment_id, True))


@router.post("/faculty/assignments/{assignment_id}/decline")
async def decline_assignment(assignment_id: int, user=Depends(faculty), db=Depends(get_session)):
    return await write(db, lambda: service.respond_assignment(
        db, user, assignment_id, False))


@router.get("/faculty/attendance-occurrences")
def available_attendance_occurrences(user=Depends(faculty), db=Depends(get_session)):
    with db.no_autoflush:
        return service.attendance_occurrences(db, user)


@router.get("/faculty/attendance-occurrences/{entry_id}/roster")
def occurrence_attendance_roster(entry_id: int, user=Depends(faculty), db=Depends(get_session)):
    with db.no_autoflush:
        return service.attendance_roster(db, user, entry_id)


@router.post("/faculty/attendance")
async def submit_occurrence_attendance(body: AttendanceSubmission,
                                       user=Depends(faculty), db=Depends(get_session)):
    return await write(db, lambda: service.submit_attendance(db, user, body))
