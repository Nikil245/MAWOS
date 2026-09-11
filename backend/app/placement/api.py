"""Thin authenticated placement API; commands and transactions are explicit."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from ..auth import get_current_user, require_role
from ..database import get_session
from .schemas import DriveInput, GenerationInput, OutcomeInput
from .service import PlacementError, drive_record, normalize_usn

router = APIRouter(prefix='/api/placements', tags=['placements'])
admin = require_role('admin')
student_or_admin = require_role('student', 'admin')


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


@router.get('/drives')
def list_drives(user=Depends(get_current_user), db=Depends(get_session)):
    return call(db, lambda: agent().service.list_drives(db, user.role == 'admin'))


@router.get('/drives/{drive_id}')
def detail(drive_id: int, user=Depends(get_current_user), db=Depends(get_session)):
    return call(db, lambda: drive_record(agent().service.drive(db, drive_id)))


@router.post('/drives', status_code=201)
def create(data: DriveInput, user=Depends(admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.save_drive(db, data), write=True)


@router.put('/drives/{drive_id}')
def update(drive_id: int, data: DriveInput, user=Depends(admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.save_drive(db, data, drive_id), write=True)


@router.post('/drives/{drive_id}/close')
def close(drive_id: int, user=Depends(admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.transition(db, drive_id, 'close'), write=True)


@router.post('/drives/{drive_id}/cancel')
def cancel(drive_id: int, user=Depends(admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.transition(db, drive_id, 'cancel'), write=True)


@router.post('/drives/{drive_id}/shortlist')
async def generate(drive_id: int, data: GenerationInput, user=Depends(admin), db=Depends(get_session)):
    result, events = call(db, lambda: agent().service.generate(db, drive_id, data.regenerate), write=True)
    await publish(events)
    return result


@router.get('/drives/{drive_id}/shortlist')
def shortlist(drive_id: int, user=Depends(admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.shortlist(db, drive_id))


@router.get('/drives/{drive_id}/eligibility/{usn}')
def eligibility(drive_id: int, usn: str, user=Depends(student_or_admin), db=Depends(get_session)):
    usn = normalize_usn(usn)
    if user.role == 'student' and usn != normalize_usn(user.usn):
        raise HTTPException(403, 'Students may view only their own placement eligibility')
    return call(db, lambda: agent().service.eligibility(db, drive_id, usn))


@router.put('/drives/{drive_id}/outcomes/{usn}')
async def outcome(drive_id: int, usn: str, data: OutcomeInput, user=Depends(admin), db=Depends(get_session)):
    result, events = call(db, lambda: agent().service.record_outcome(db, drive_id, usn, data), write=True)
    await publish(events)
    return result


@router.get('/drives/{drive_id}/outcomes')
def outcomes(drive_id: int, user=Depends(admin), db=Depends(get_session)):
    return call(db, lambda: agent().service.outcomes(db, drive_id))
