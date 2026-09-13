"""Campus event lifecycle, audience filtering, and authenticated APIs."""
import datetime as dt
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .auth import get_current_user, require_role
from .database import get_session
from .models import CampusEvent, Department, Parent, ParentStudent, Student, User, utcnow
from .notifications import notify_users


router = APIRouter(prefix="/api", tags=["campus-events"])
admin_only = require_role("admin")
ALLOWED_AUDIENCES = {"STUDENT", "FACULTY", "HOD", "PRINCIPAL", "ADMIN", "PARENT"}
KOLKATA = ZoneInfo("Asia/Kolkata")


def india_today() -> dt.date:
    return dt.datetime.now(KOLKATA).date()


class EventFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    event_date: dt.date
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    venue: str | None = Field(default=None, max_length=200)
    organizer: str | None = Field(default=None, max_length=200)
    audience: str = Field(default="ALL", min_length=1, max_length=128)
    department_code: str | None = Field(default=None, max_length=8)

    @field_validator("audience")
    @classmethod
    def normalize_audience(cls, value):
        roles = list(dict.fromkeys(part.strip().upper() for part in value.split(",")))
        if not roles or any(role not in ALLOWED_AUDIENCES | {"ALL"} for role in roles):
            raise ValueError("Audience must contain supported MAWOS roles")
        if "ALL" in roles and len(roles) != 1:
            raise ValueError("ALL cannot be combined with specific roles")
        return ",".join(roles)

    @field_validator("department_code")
    @classmethod
    def normalize_department(cls, value):
        return value.strip().upper() if value else None

    @field_validator("description", "venue", "organizer")
    @classmethod
    def empty_to_none(cls, value):
        return value or None

    @model_validator(mode="after")
    def validate_times(self):
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("End time must be after start time")
        return self


class EventCreate(EventFields):
    status: Literal["DRAFT", "PUBLISHED"] = "DRAFT"


class EventUpdate(EventFields):
    pass


class CancelEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str | None = Field(default=None, max_length=2000)


def record(row: CampusEvent) -> dict:
    values = {name: getattr(row, name) for name in (
        "id", "title", "description", "event_date", "venue", "organizer",
        "audience", "department_code", "status", "cancellation_reason",
        "created_by_user_id", "created_at", "updated_at", "published_at")}
    values.update(start_time=row.start_time, end_time=row.end_time)
    return values


def public_record(row: CampusEvent) -> dict:
    """Audience-safe event fields without creator or internal audit identifiers."""
    return {name: getattr(row, name) for name in (
        "id", "title", "description", "event_date", "start_time", "end_time",
        "venue", "organizer", "audience", "department_code", "status")}


def audience_roles(value: str) -> set[str]:
    return {part.strip().upper() for part in value.split(",") if part.strip()}


def parent_departments(db, user: User) -> set[str]:
    if user.role != "parent":
        return set()
    return {code for code, in (db.query(Student.dept_code)
        .join(ParentStudent, ParentStudent.student_usn == Student.usn)
        .join(Parent, Parent.id == ParentStudent.parent_id)
        .filter(Parent.user_id == user.id, Parent.active.is_(True),
                ParentStudent.active.is_(True)).distinct().all())}


def visible_to(row: CampusEvent, user: User, db=None) -> bool:
    roles = audience_roles(row.audience)
    department_visible = (row.department_code is None or row.department_code == user.dept_code)
    if user.role == "parent" and db is not None:
        department_visible = row.department_code is None or row.department_code in parent_departments(db, user)
    return (row.status == "PUBLISHED"
            and ("ALL" in roles or user.role.upper() in roles)
            and department_visible)


def eligible_recipient_ids(db, row: CampusEvent) -> list[int]:
    roles = audience_roles(row.audience)
    query = db.query(User.id).filter(User.role != "parent")
    if "ALL" not in roles:
        query = query.filter(User.role.in_({role.lower() for role in roles if role != "PARENT"}))
    if row.department_code is not None:
        query = query.filter(User.dept_code == row.department_code)
    ids = [user_id for user_id, in query.all()]
    if "ALL" in roles or "PARENT" in roles:
        parents = (db.query(User.id).join(Parent, Parent.user_id == User.id)
                   .filter(User.role == "parent", Parent.active.is_(True)))
        if row.department_code is not None:
            parents = (parents.join(ParentStudent, ParentStudent.parent_id == Parent.id)
                       .join(Student, Student.usn == ParentStudent.student_usn)
                       .filter(ParentStudent.active.is_(True),
                               Student.dept_code == row.department_code).distinct())
        ids.extend(user_id for user_id, in parents.all())
    return sorted(set(ids))


def notify_publication(db, row: CampusEvent) -> int:
    time_part = f" at {row.start_time.strftime('%H:%M')}" if row.start_time else ""
    venue_part = f" in {row.venue}" if row.venue else ""
    ids = eligible_recipient_ids(db, row)
    parent_ids = {value for value, in db.query(User.id).filter(
        User.id.in_(ids), User.role == "parent").all()} if ids else set()
    common = dict(title=f"Campus event: {row.title}",
        message=f"{row.title} is scheduled for {row.event_date.isoformat()}{time_part}{venue_part}.",
        notification_type="CAMPUS_EVENT_PUBLISHED", source_agent="notification_agent",
        event_key=f"campus_event_published:{row.id}",
        related_entity_type="campus_event", related_entity_id=row.id)
    return (notify_users(db, set(ids) - parent_ids, route=f"/events/{row.id}", **common)
            + notify_users(db, parent_ids, route=f"/parent/events/{row.id}", **common))


def notify_cancellation(db, row: CampusEvent) -> int:
    reason = f" Reason: {row.cancellation_reason}" if row.cancellation_reason else ""
    return notify_users(
        db, eligible_recipient_ids(db, row), title=f"Campus event cancelled: {row.title}",
        message=f"The event scheduled for {row.event_date.isoformat()} was cancelled.{reason}",
        notification_type="CAMPUS_EVENT_CANCELLED", source_agent="notification_agent",
        event_key=f"campus_event_cancelled:{row.id}", route=None,
        related_entity_type="campus_event", related_entity_id=row.id)


def validate_department(db, code):
    if code and db.get(Department, code) is None:
        raise HTTPException(422, "Unknown department code")


def save(db, operation):
    try:
        result = operation()
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Campus event changed concurrently; refresh and retry") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Campus events are temporarily unavailable") from None


def event_or_404(db, event_id, lock=False):
    query = db.query(CampusEvent).filter_by(id=event_id)
    if lock:
        query = query.populate_existing().with_for_update()
    row = query.one_or_none()
    if row is None:
        raise HTTPException(404, "Campus event not found")
    return row


@router.get("/admin/campus-events")
def admin_events(user=Depends(admin_only), db=Depends(get_session)):
    rows = db.query(CampusEvent).order_by(
        CampusEvent.event_date.desc(), CampusEvent.start_time, CampusEvent.id.desc()).limit(300)
    return {"events": [record(row) for row in rows]}


@router.get("/admin/campus-events/{event_id}")
def admin_event_detail(event_id: int, user=Depends(admin_only), db=Depends(get_session)):
    return record(event_or_404(db, event_id))


@router.post("/admin/campus-events", status_code=201)
def create_event(body: EventCreate, user=Depends(admin_only), db=Depends(get_session)):
    def operation():
        validate_department(db, body.department_code)
        values = body.model_dump()
        row = CampusEvent(**values, created_by_user_id=user.id)
        db.add(row)
        db.flush()
        if row.status == "PUBLISHED":
            row.published_at = utcnow()
            notify_publication(db, row)
        return record(row)
    return save(db, operation)


@router.put("/admin/campus-events/{event_id}")
def update_event(event_id: int, body: EventUpdate, user=Depends(admin_only), db=Depends(get_session)):
    def operation():
        row = event_or_404(db, event_id, lock=True)
        if row.status == "CANCELLED":
            raise HTTPException(409, "Cancelled events cannot be edited")
        validate_department(db, body.department_code)
        values = body.model_dump()
        for name, value in values.items():
            setattr(row, name, value)
        row.updated_at = utcnow()
        db.flush()
        return record(row)
    return save(db, operation)


@router.post("/admin/campus-events/{event_id}/publish")
def publish_event(event_id: int, user=Depends(admin_only), db=Depends(get_session)):
    def operation():
        row = event_or_404(db, event_id, lock=True)
        if row.status == "CANCELLED":
            raise HTTPException(409, "Cancelled events cannot be published")
        if row.status == "DRAFT":
            row.status = "PUBLISHED"
            row.published_at = utcnow()
            row.updated_at = utcnow()
            notify_publication(db, row)
        return record(row)
    return save(db, operation)


@router.post("/admin/campus-events/{event_id}/cancel")
def cancel_event(event_id: int, body: CancelEvent, user=Depends(admin_only), db=Depends(get_session)):
    def operation():
        row = event_or_404(db, event_id, lock=True)
        if row.status != "CANCELLED":
            was_published = row.status == "PUBLISHED"
            row.status = "CANCELLED"
            row.cancellation_reason = body.reason
            row.updated_at = utcnow()
            if was_published:
                notify_cancellation(db, row)
        return record(row)
    return save(db, operation)


def visible_query(db, user, from_date=None):
    departments = parent_departments(db, user) if user.role == "parent" else {user.dept_code}
    query = db.query(CampusEvent).filter(CampusEvent.status == "PUBLISHED")
    if departments:
        query = query.filter(or_(CampusEvent.department_code.is_(None),
                                 CampusEvent.department_code.in_(departments)))
    else:
        query = query.filter(CampusEvent.department_code.is_(None))
    if from_date is not None:
        query = query.filter(CampusEvent.event_date >= from_date)
    return query


@router.get("/campus-events")
def my_events(limit: int = Query(100, ge=1, le=200),
              user=Depends(get_current_user), db=Depends(get_session)):
    today = india_today()
    rows = visible_query(db, user, today).order_by(
        CampusEvent.event_date, CampusEvent.start_time, CampusEvent.id).limit(500).all()
    rows = [row for row in rows if visible_to(row, user, db)]
    today_rows = [row for row in rows if row.event_date == today]
    upcoming = [row for row in rows if row.event_date > today][:5]
    return {"today": [public_record(row) for row in today_rows],
            "upcoming": [public_record(row) for row in upcoming],
            "events": [public_record(row) for row in rows[:limit]], "india_date": today}


@router.get("/campus-events/{event_id}")
def event_detail(event_id: int, user=Depends(get_current_user), db=Depends(get_session)):
    row = event_or_404(db, event_id)
    if not visible_to(row, user, db):
        raise HTTPException(404, "Campus event not found")
    return public_record(row)
