"""Authenticated placement API; commands and document access are explicit."""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError

from ..auth import require_role
from ..database import get_session
from .schemas import (AdminPlacementView, CancellationInput, DriveInput,
                      DriveListRecord, DriveRecord, GenerationInput,
                      OutcomeInput, OutcomeRecord, ShortlistRecord,
                      StudentDriveDetail)
from .service import PlacementError, drive_record, normalize_usn
from .storage import placement_document_storage

router = APIRouter(prefix='/api/placements', tags=['placements'])
logger = logging.getLogger(__name__)
admin = require_role('admin')
student_or_admin = require_role('student', 'admin')


def controlled_storage(operation):
    try:
        return operation()
    except PlacementError as exc:
        raise HTTPException(exc.status, exc.message) from None


def agent():
    from ..agents import get_agents
    return get_agents()['placement_agent']


def call(db, operation, write=False):
    try:
        result = operation()
        if write:
            db.commit()
        return result
    except PlacementError as exc:
        db.rollback()
        raise HTTPException(exc.status, exc.message) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(409 if write else 503, 'Placement data unavailable or changed; refresh and retry') from None


async def publish(events):
    for topic, payload in events:
        await agent().publish(topic, payload)


async def publish_safely(events):
    """Database writes are authoritative; event telemetry is best effort."""
    try:
        await publish(events)
    except Exception:  # optional downstream/audit delivery must not undo committed notices
        logger.exception('Placement event delivery failed after the database transaction committed')


@router.get('/drives', response_model=list[DriveListRecord])
def list_drives(user=Depends(student_or_admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.list_drives(
        db, user.role == 'admin', user.usn if user.role == 'student' else None))


@router.get('/drives/{drive_id}', response_model=DriveRecord | StudentDriveDetail)
def detail(drive_id: int, user=Depends(student_or_admin), db=Depends(get_session)):
    if user.role == 'student':
        return call(db, lambda: agent().service.student_detail(db, drive_id, user.usn))
    return call(db, lambda: drive_record(agent().service.drive(db, drive_id)))


@router.post('/drives', status_code=201)
async def create(data: DriveInput, user=Depends(admin), db=Depends(get_session)):
    events = []
    result = call(db, lambda: agent().service.save_drive(db, data, events=events), write=True)
    await publish_safely(events)
    return result


@router.put('/drives/{drive_id}')
async def update(drive_id: int, data: DriveInput, user=Depends(admin), db=Depends(get_session)):
    events = []
    result = call(db, lambda: agent().service.save_drive(db, data, drive_id, events=events), write=True)
    await publish_safely(events)
    return result


@router.post('/drives/{drive_id}/close')
def close(drive_id: int, user=Depends(admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.transition(db, drive_id, 'close'), write=True)


@router.post('/drives/{drive_id}/cancel')
def cancel(drive_id: int, data: CancellationInput, user=Depends(admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.transition(db, drive_id, 'cancel', data.reason), write=True)


@router.post('/drives/{drive_id}/shortlist')
async def generate(drive_id: int, data: GenerationInput, user=Depends(admin), db=Depends(get_session)):
    result, events = call(db, lambda: agent().service.generate(db, drive_id, data.regenerate), write=True)
    await publish_safely(events)
    return result


@router.get('/drives/{drive_id}/shortlist', response_model=list[ShortlistRecord])
def shortlist(drive_id: int, user=Depends(admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.shortlist(db, drive_id))


@router.get('/drives/{drive_id}/admin-view', response_model=AdminPlacementView)
def admin_view(drive_id: int, user=Depends(admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.admin_view(db, drive_id))


@router.get('/drives/{drive_id}/eligibility/{usn}')
def eligibility(drive_id: int, usn: str, user=Depends(student_or_admin), db=Depends(get_session)):
    usn = normalize_usn(usn)
    if user.role == 'student' and usn != normalize_usn(user.usn):
        raise HTTPException(403, 'Students may view only their own placement eligibility')
    return call(db, lambda: agent().service.eligibility(db, drive_id, usn))


@router.put('/drives/{drive_id}/outcomes/{usn}')
async def outcome(drive_id: int, usn: str, data: OutcomeInput, user=Depends(admin), db=Depends(get_session)):
    result, events = call(db, lambda: agent().service.record_outcome(db, drive_id, usn, data), write=True)
    await publish_safely(events)
    return result


@router.get('/drives/{drive_id}/outcomes', response_model=list[OutcomeRecord])
def outcomes(drive_id: int, user=Depends(admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.outcomes(db, drive_id))


@router.post('/drives/{drive_id}/document')
async def upload_document(drive_id: int, document: UploadFile = File(...),
                          user=Depends(admin), db=Depends(get_session)):
    storage = placement_document_storage()
    try:
        stored = await storage.save_pdf(document)
    except PlacementError as exc:
        raise HTTPException(exc.status, exc.message) from None
    try:
        result, old_key = call(
            db, lambda: agent().service.set_document(db, drive_id, stored, user.id), write=True)
    except Exception:
        try:
            storage.remove(stored.storage_key)
        except PlacementError:
            pass  # best-effort orphan cleanup must not mask the database error
        raise
    if old_key and old_key != stored.storage_key:
        controlled_storage(lambda: storage.remove(old_key))
    return result


@router.delete('/drives/{drive_id}/document')
def remove_document(drive_id: int, user=Depends(admin), db=Depends(get_session)):
    storage = placement_document_storage()
    result, old_key = call(db, lambda: agent().service.clear_document(db, drive_id), write=True)
    controlled_storage(lambda: storage.remove(old_key))
    return result


@router.get('/drives/{drive_id}/document')
def download_document(drive_id: int, disposition: Literal['inline', 'attachment'] = 'inline',
                      user=Depends(student_or_admin), db=Depends(get_session)):
    service = agent().service
    drive = call(db, lambda: service.drive(db, drive_id))
    if user.role == 'student':
        # This enforces student visibility and existence without exposing any
        # other student's shortlist/outcome data.
        call(db, lambda: service.student_detail(db, drive_id, user.usn))
    if not drive.job_document_storage_key:
        raise HTTPException(404, 'Job document not found')
    path = call(db, lambda: placement_document_storage().open_path(drive.job_document_storage_key))
    safe_name = drive.job_document_original_name or 'job-description.pdf'
    response = FileResponse(path, media_type='application/pdf', filename=safe_name,
                            content_disposition_type=disposition)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'private, no-store'
    return response
