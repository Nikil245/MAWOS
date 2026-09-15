"""REST API v2 — role-scoped gateway in front of the agent layer."""
import datetime as dt
import hashlib
import hmac
import json
from ..timetable import reads as timetable_reads
import logging
import math
import time
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .. import ai_provider, config, llm, metrics
from ..agents import get_agents
from ..agents import tools as assistant_tools
from ..auth import (create_token, get_authenticated_user, get_current_user,
                    hash_password, require_role, verify_password)
from ..database import get_session
from ..models import (Department, HallTicket, Notification, ScholarshipAssessment, Student,
                      TeachingAssignment, User, Faculty, Scholarship,
                      ScholarshipApplication, utcnow)
from .. import scholarships
from ..notifications import mark_read, owned_query
from ..marks_policy import INTERNALS, MAX_MARKS, assessments
from .schemas import (
    AdminAdmissionsResponse,
    AdmissionApplicationResponse,
    AdmissionsFunnelResponse,
    AssistantCapabilitiesResponse,
    ChatResponse,
    ChatTopic,
    DepartmentAnalyticsResponse,
    FeeCollectionResponse,
    NotificationListResponse,
    PlacementStatsResponse,
    PrincipalAnalyticsResponse,
    ScholarshipApplyRequest,
    ScholarshipRequest,
    ScholarshipReviewRequest,
)

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
EXISTING_PORTAL_ROLES = ("student", "faculty", "hod", "principal", "admin")
CHAT_PORTAL_ROLES = EXISTING_PORTAL_ROLES + ("parent", "librarian")
ASSISTANT_CONTEXT_TTL_SECONDS = 12 * 60 * 60


# ---------- auth ------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_session)):
    user = db.query(User).filter(User.username == body.username.strip()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.role == "librarian":
        from ..models import LibrarianAccount
        account = db.get(LibrarianAccount, user.id)
        if account is None or not account.active:
            raise HTTPException(status_code=403, detail="Librarian account is inactive")
    if user.role == "parent":
        from ..models import Parent
        parent = db.query(Parent).filter(Parent.user_id == user.id).one_or_none()
        if parent is None or not parent.active:
            raise HTTPException(status_code=403, detail="Parent account is inactive")
    return {"token": create_token(user),
            "user": {"username": user.username, "role": user.role,
                     "name": user.display_name, "usn": user.usn,
                     "dept": user.dept_code,
                     "must_change_password": bool(user.must_change_password)},
            # Login never probes Ollama: startup/login stay independent of a
            # local optional service. Chat checks it only when escalation is
            # warranted.
            "ai_mode": "llm" if llm.runtime_status()["available"] else "lexicon",
            "runtime_model": llm.runtime_status()["runtime_model"]}


@router.get("/me")
def me(user: User = Depends(get_authenticated_user)):
    return {"username": user.username, "role": user.role,
            "name": user.display_name, "usn": user.usn, "dept": user.dept_code,
            "must_change_password": bool(user.must_change_password),
            "ai_mode": "llm" if llm.runtime_status()["available"] else "lexicon",
            "runtime_model": llm.runtime_status()["runtime_model"]}


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=128)

    @field_validator("new_password")
    @classmethod
    def strong_password(cls, value):
        if not (any(char.isalpha() for char in value)
                and any(char.isdigit() for char in value)):
            raise ValueError("New password must contain letters and numbers")
        return value


@router.post("/auth/change-password")
def change_password(body: PasswordChangeRequest,
                    user: User = Depends(get_authenticated_user),
                    db: Session = Depends(get_session)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=422, detail="New password must be different")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    db.commit()
    return {"changed": True, "must_change_password": False}


# ---------- notifications ----------------------------------------------------
@router.get("/notifications", response_model=NotificationListResponse)
def notifications(user: User = Depends(get_current_user),
                  db: Session = Depends(get_session),
                  limit: int = Query(50, ge=1, le=100),
                  offset: int = Query(0, ge=0)):
    """Return only notifications addressed to the authenticated database user."""
    items = get_agents()["notification_agent"].for_user(
        db, user_id=user.id, limit=limit, offset=offset)
    return NotificationListResponse(
        notifications=items,
        unread_count=owned_query(db, user).filter(Notification.read.is_(False)).count(),
    )


def _visible_notification_query(db, user):
    return owned_query(db, user)


@router.get("/notifications/unread-count")
def notification_unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    return {"unread_count": owned_query(db, user).filter(Notification.read.is_(False)).count()}


@router.patch("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    row = _visible_notification_query(db, user).filter(Notification.id == notification_id).first()
    if row is None:
        raise HTTPException(404, "Notification not found")
    try:
        mark_read(row)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Unable to mark notification read")
        raise HTTPException(500, "Notification could not be updated.") from exc
    return {"id": row.id, "read": True}


@router.post("/notifications/read-all")
def mark_all_notifications_read(user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    try:
        now = utcnow()
        changed = _visible_notification_query(db, user).filter(Notification.read.is_(False)).update(
            {Notification.read: True, Notification.read_at: now}, synchronize_session=False)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Unable to mark notifications read")
        raise HTTPException(500, "Notifications could not be updated.") from exc
    return {"updated": changed}


# ---------- assistant ---------------------------------------------------------
@router.get("/assistant/capabilities", response_model=AssistantCapabilitiesResponse)
def assistant_capabilities(user: User = Depends(require_role(*CHAT_PORTAL_ROLES))):
    result = assistant_tools.assistant_capabilities(user.role, user.display_name)
    provider = ai_provider.runtime_status()
    ollama_available = llm.runtime_status()["available"] is True
    selected = None
    if config.AI_PROVIDER in {"auto", "groq"} and provider["groq_available"] is True:
        selected = "groq"
    elif config.AI_PROVIDER in {"auto", "ollama"} and ollama_available:
        selected = "ollama"
    result.update(
        ai_provider_mode=config.AI_PROVIDER,
        ai_provider_status=(
            "available" if selected else provider["groq_status"]
            if config.AI_PROVIDER in {"auto", "groq"} else
            "not_checked" if config.AI_PROVIDER == "ollama" else "disabled"
        ),
        ai_provider=selected,
    )
    return result


class LibraryContextBook(BaseModel):
    """A public catalogue reference, never a stock or authorization claim."""
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    isbn: str = Field(min_length=1, max_length=32, pattern=r"^[0-9Xx -]+$")
    author: str = Field(min_length=1, max_length=240)
    category: str = Field(min_length=1, max_length=120)


class ConversationContextMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    category: Literal["general_ai", "library_catalogue"]
    content: str
    books: list[LibraryContextBook] = Field(default_factory=list, max_length=5)
    proof: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    issued_at: int | None = Field(default=None, ge=0)

    @field_validator("content")
    @classmethod
    def content_is_bounded(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 700:
            raise ValueError("context content must contain 1 to 700 characters")
        return value

    @model_validator(mode="after")
    def metadata_matches_message(self):
        if self.books and (self.role != "assistant" or self.category != "library_catalogue"):
            raise ValueError("catalogue references are allowed only on library assistant messages")
        if self.role == "user" and self.proof is not None:
            raise ValueError("context proof is allowed only on assistant messages")
        if self.role == "user" and self.issued_at is not None:
            raise ValueError("context timestamp is allowed only on assistant messages")
        if self.books and self.issued_at is None:
            raise ValueError("catalogue candidate sets require a timestamp")
        if self.category != "library_catalogue" and self.issued_at is not None:
            raise ValueError("context timestamp is allowed only for library messages")
        return self


def _context_proof(user: User, prior_user: dict, prior_assistant: dict) -> str:
    """Bind one safe pair to the authenticated account without exposing identity."""
    canonical = json.dumps({
        "owner": {"username": user.username, "role": user.role},
        "user": {key: prior_user[key] for key in ("role", "category", "content")},
        "assistant": {
            "role": prior_assistant["role"], "category": prior_assistant["category"],
            "content": prior_assistant["content"], "books": prior_assistant.get("books", []),
            "issued_at": prior_assistant.get("issued_at"),
        },
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signing_key = hmac.new(
        config.jwt_secret().encode("utf-8"), b"mawos-assistant-context-v1", hashlib.sha256,
    ).digest()
    return hmac.new(signing_key, canonical, hashlib.sha256).hexdigest()


def _owned_context(user: User, context: Any) -> tuple[list[dict], str]:
    """Validate optional server-signed context; it is never authority or evidence."""
    if context in (None, []):
        return [], "absent"
    if not isinstance(context, list) or len(context) > 8 or len(context) % 2:
        return [], "invalid_shape"
    accepted = []
    try:
        parsed = [ConversationContextMessage.model_validate(item) for item in context]
    except (TypeError, ValueError):
        return [], "invalid_shape"
    if sum(len(item.content) for item in parsed) > 4000:
        return [], "over_limit"
    expired_candidate = False
    for index in range(0, len(parsed), 2):
        user_item, assistant_item = parsed[index:index + 2]
        if ((user_item.role, assistant_item.role) != ("user", "assistant")
                or user_item.category != assistant_item.category):
            return [], "invalid_shape"
        prior_user = user_item.model_dump(exclude={"proof"})
        prior_assistant = assistant_item.model_dump(exclude={"proof"})
        supplied = assistant_item.proof
        valid_proof = supplied and hmac.compare_digest(
            supplied, _context_proof(user, prior_user, prior_assistant))
        if not valid_proof:
            return [], "unsigned" if not supplied else "signature_invalid"
        issued_at = prior_assistant.get("issued_at")
        if issued_at is not None:
            age = int(dt.datetime.now(dt.timezone.utc).timestamp()) - issued_at
            prior_assistant["candidate_set_expired"] = not (-300 <= age <= ASSISTANT_CONTEXT_TTL_SECONDS)
            expired_candidate = expired_candidate or prior_assistant["candidate_set_expired"]
        prior_assistant["proof"] = supplied
        accepted.extend((prior_user, prior_assistant))
    return accepted, "accepted_expired" if expired_candidate else "accepted"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    # Optional enhancement fields are intentionally loose at the HTTP boundary.
    # The route discards them by category if they cannot be safely validated.
    context_topic: str | None = None
    conversation_context: Any = None

    @field_validator("message")
    @classmethod
    def chat_message_is_present_and_bounded(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be empty")
        if len(value) > 1000:
            raise ValueError("message must be at most 1000 characters")
        return value


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, user: User = Depends(require_role(*CHAT_PORTAL_ROLES)),
               db: Session = Depends(get_session)):
    started = time.perf_counter()
    result = None
    context, context_status = _owned_context(user, body.conversation_context)
    topic = body.context_topic if body.context_topic in ChatTopic.__args__ else None
    if body.context_topic is not None and topic is None and context_status == "absent":
        context_status = "topic_invalid"
    try:
        result = await get_agents()["orchestrator_agent"].handle_chat(
            db, user, body.message, context_topic=topic, conversation_context=context)
        if ((result.get("category") == "general_ai" and result.get("mode") == "general_ai")
                or result.get("category") == "library_catalogue"):
            prior_user = {"role": "user", "category": result["category"],
                          "content": body.message[:700]}
            prior_assistant = {
                "role": "assistant", "category": result["category"],
                "content": result["text"][:700], "books": result.get("context_books", []),
            }
            if prior_assistant["books"]:
                prior_assistant["issued_at"] = int(dt.datetime.now(dt.timezone.utc).timestamp())
                result["context_issued_at"] = prior_assistant["issued_at"]
            # The HMAC key remains server-only. The browser only returns this server-issued proof.
            result["context_proof"] = _context_proof(user, prior_user, prior_assistant)
        return result
    finally:
        logger.info(
            "assistant_request intent=%s role=%s category=%s outcome=%s elapsed_ms=%.1f",
            (result or {}).get("intent", "unclassified"), user.role,
            (result or {}).get("category", "error"),
            "failure" if not result or result.get("fallback") else "success",
            (time.perf_counter() - started) * 1000,
        )


# ---------- student portal ------------------------------------------------------
@router.get("/student/dashboard")
def student_dashboard(user: User = Depends(require_role("student")),
                      db: Session = Depends(get_session)):
    agents = get_agents()
    profile = agents["academic_agent"].student_profile(db, user.usn)
    from ..models import AttendanceSummary
    from ..agents.attendance import overall_percentage
    subs = db.query(AttendanceSummary).filter_by(usn=user.usn).all()
    ht = db.query(HallTicket).filter_by(usn=user.usn).first()
    s = db.get(Student, user.usn)
    workflow_scholarship = scholarships.student_summary(db, s)
    from ..library.service import summary as library_summary
    return {
        "library": library_summary(db, user.usn),
        "profile": profile,
        "attendance": {
            "overall": overall_percentage(db, user.usn),
            "subjects": [{"subject": x.subject_code, "held": x.classes_held,
                          "attended": x.classes_attended, "pct": x.percentage,
                          "shortage": x.shortage} for x in subs]},
        "marks": agents["academic_agent"].student_marks(db, user.usn),
        "fees": agents["finance_agent"].student_fees(db, user.usn),
        "hall_ticket": ({"eligible": ht.eligible, "reasons": ht.reasons}
                        if ht else None),
        "scholarship": {**workflow_scholarship, "status": workflow_scholarship["state"]},
        "placements": agents["placement_agent"].student_view(db, user.usn),
        "timetable": timetable_reads.grid(db, s.dept_code, s.year, s.section, semester=s.semester),
        "exams": agents["eligibility_agent"].schedule_for(db, s.dept_code, s.semester),
        "notifications": agents["notification_agent"].for_user(
            db, user_id=user.id),
    }


class PayFeeRequest(BaseModel):
    fee_id: int


@router.post("/student/pay-fee")
async def pay_fee(body: PayFeeRequest,
                  user: User = Depends(require_role("student")),
                  db: Session = Depends(get_session)):
    return await get_agents()["finance_agent"].pay_fee(db, user.usn, body.fee_id)


# ---------- timetable (any authenticated role) -----------------------------------
@router.get("/timetable/{dept}/{year}/{section}")
def timetable(dept: str, year: int, section: str,
              user: User = Depends(get_current_user),
              db: Session = Depends(get_session)):
    return timetable_reads.authorized_grid(db, user, dept.upper(), year, section.upper())


@router.get("/timetable/{dept}/{year}/{section}/csv")
def timetable_csv(dept: str, year: int, section: str,
                  user: User = Depends(get_current_user),
                  db: Session = Depends(get_session)):
    import csv as csv_module
    import io
    grid = timetable_reads.authorized_grid(db, user, dept.upper(), year, section.upper())
    stream = io.StringIO()
    writer = csv_module.writer(stream)
    writer.writerow(['Day', *grid['periods']])
    for day, label in enumerate(grid['days']):
        writer.writerow([label, *[grid['cells'].get(f'{day}-{period}', {}).get('subject', '') for period in range(len(grid['periods']))]])
    csv = stream.getvalue()
    return PlainTextResponse(csv, media_type="text/csv", headers={
        "Content-Disposition":
            f"attachment; filename=timetable_{dept}_{year}{section}.csv"})


# ---------- faculty console --------------------------------------------------------
@router.get("/faculty/overview")
def faculty_overview(user: User = Depends(require_role("faculty", "hod")),
                     db: Session = Depends(get_session)):
    agents = get_agents()
    assignments = agents["academic_agent"].faculty_assignments(db, user.faculty_id)
    return {"assignments": assignments,
            "timetable": timetable_reads.grid(db, faculty_id=user.faculty_id),
            "notifications": agents["notification_agent"].for_user(
                db, user_id=user.id)}


@router.get("/faculty/roster/{dept}/{year}/{section}")
def class_roster(dept: str, year: int, section: str,
                 user: User = Depends(require_role("faculty", "hod", "admin")),
                 db: Session = Depends(get_session)):
    dept = dept.upper()
    section = section.upper()
    if not _can_access_class(db, user, dept, year, section):
        raise HTTPException(status_code=403,
                            detail="You are not assigned to this class")
    return {"roster": get_agents()["academic_agent"].class_roster(
        db, dept, year, section)}


class AttendanceSheet(BaseModel):
    dept: str
    year: int
    section: str
    subject_code: str
    date: str
    absent_usns: list[str] = []


def _can_access_class(db, user, dept: str, year: int, section: str) -> bool:
    if user.role == "admin":
        return True
    if user.role == "hod":
        return user.dept_code == dept
    if user.role == "faculty":
        return db.query(TeachingAssignment).filter_by(
            faculty_id=user.faculty_id, dept_code=dept, year=year,
            section=section).first() is not None
    return False


def _owns_assignment(db, user, sheet: AttendanceSheet) -> bool:
    if user.role in ("admin",):
        return True
    return db.query(TeachingAssignment).filter_by(
        faculty_id=user.faculty_id, subject_code=sheet.subject_code.upper(),
        dept_code=sheet.dept.upper(), year=sheet.year,
        section=sheet.section.upper()).first() is not None


@router.post("/faculty/attendance")
async def mark_attendance(sheet: AttendanceSheet,
                          user: User = Depends(require_role("faculty", "hod", "admin")),
                          db: Session = Depends(get_session)):
    """Mark a whole class in one call: everyone present except absent_usns."""
    if not _owns_assignment(db, user, sheet):
        raise HTTPException(status_code=403,
                            detail="You are not assigned to this subject-section")
    roster = db.query(Student).filter_by(dept_code=sheet.dept.upper(),
                                         year=sheet.year,
                                         section=sheet.section.upper()).all()
    absent = {u.upper().strip() for u in sheet.absent_usns}
    records = [{"usn": s.usn, "subject_code": sheet.subject_code.upper(),
                "date": sheet.date, "present": s.usn not in absent}
               for s in roster]
    return await get_agents()["attendance_agent"].upload_attendance(
        db, user.username, records)


class MarkEntry(BaseModel):
    usn: str
    marks: float

    @field_validator("usn")
    @classmethod
    def usn_is_required(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("student USN is required")
        return value

    @field_validator("marks", mode="before")
    @classmethod
    def mark_is_numeric_and_in_range(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("mark must be a numeric value")
        if not math.isfinite(value):
            raise ValueError("mark must be finite")
        if value < 0 or value > MAX_MARKS:
            raise ValueError(f"mark must be between 0 and {MAX_MARKS:g}")
        return float(value)


class MarksSheet(BaseModel):
    subject_code: str
    internal: int
    entries: list[MarkEntry]

    @field_validator("subject_code")
    @classmethod
    def subject_is_required(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("subject code is required")
        return value

    @field_validator("internal")
    @classmethod
    def internal_is_supported(cls, value: int) -> int:
        if value not in INTERNALS:
            raise ValueError(f"assessment must be one of {INTERNALS}")
        return value

    @field_validator("entries")
    @classmethod
    def sheet_has_unique_entries(cls, entries: list[MarkEntry]) -> list[MarkEntry]:
        if not entries:
            raise ValueError("at least one student mark is required")
        usns = [entry.usn for entry in entries]
        if len(usns) != len(set(usns)):
            raise ValueError("marks sheet contains a duplicate student")
        return entries


class MarksAssessmentPolicy(BaseModel):
    internal: int
    label: str
    max_marks: float


class MarksPolicyResponse(BaseModel):
    assessments: list[MarksAssessmentPolicy]


@router.get("/faculty/marks-policy", response_model=MarksPolicyResponse)
def marks_policy(user: User = Depends(require_role("faculty", "hod", "admin"))):
    return MarksPolicyResponse(assessments=assessments())


def _authorize_marks_sheet(db, user, sheet: MarksSheet) -> None:
    if user.role == "admin":
        return
    subject_code = sheet.subject_code.upper()
    usns = {entry.usn for entry in sheet.entries}
    students = {s.usn: s for s in db.query(Student).filter(Student.usn.in_(usns)).all()}
    if len(students) != len(usns) or "" in usns:
        raise HTTPException(status_code=403,
                            detail="Marks request contains unauthorized students")
    if user.role == "hod":
        if any(s.dept_code != user.dept_code for s in students.values()):
            raise HTTPException(status_code=403,
                                detail="You are not authorized for this department")
        assigned_depts = {s.dept_code for s in students.values()}
        if db.query(TeachingAssignment).filter(
                TeachingAssignment.subject_code == subject_code,
                TeachingAssignment.dept_code.in_(assigned_depts)).first() is None:
            raise HTTPException(status_code=403,
                                detail="Subject is not assigned in this department")
        return
    for student in students.values():
        ok = db.query(TeachingAssignment).filter_by(
            faculty_id=user.faculty_id, subject_code=subject_code,
            dept_code=student.dept_code, year=student.year,
            section=student.section).first() is not None
        if not ok:
            raise HTTPException(status_code=403,
                                detail="You are not assigned to this subject-section")


@router.post("/faculty/marks")
def enter_marks(sheet: MarksSheet,
                user: User = Depends(require_role("faculty", "hod", "admin")),
                db: Session = Depends(get_session)):
    _authorize_marks_sheet(db, user, sheet)
    records = [{"usn": entry.usn, "subject_code": sheet.subject_code,
                "internal": sheet.internal, "marks": entry.marks}
               for entry in sheet.entries]
    try:
        return get_agents()["academic_agent"].enter_marks(
            db, user.username, records)
    except ValueError as exc:
        # Defensive domain validation for callers that bypass the Pydantic
        # boundary; no records have been staged before this is raised.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------- HOD ------------------------------------------------------------------------
@router.get("/hod/analytics")
def hod_analytics(user: User = Depends(require_role("hod", "principal", "admin")),
                  db: Session = Depends(get_session)):
    agents = get_agents()
    dept = user.dept_code or "AIML"
    data = agents["academic_agent"].dept_analytics(db, dept)
    data["fee_defaulters"] = agents["finance_agent"].defaulter_list(db, dept, 20)
    data["sections"] = [
        {"year": y, "section": s}
        for y in (1, 2, 3, 4) for s in ("A", "B")]
    return data


@router.post("/hod/generate-timetable")
@router.post("/hod/generate-timetable-live")
def legacy_timetable_generation(user: User = Depends(require_role("hod", "admin"))):
    raise HTTPException(410, "Use the academic-term timetable workspace to generate a draft and explicitly publish it.")


# ---------- principal --------------------------------------------------------------------
@router.get("/principal/analytics", response_model=PrincipalAnalyticsResponse)
def principal_analytics(user: User = Depends(require_role("principal", "admin")),
                        db: Session = Depends(get_session)):
    agents = get_agents()
    department_map = agents["academic_agent"].institution_analytics(db)
    departments = [DepartmentAnalyticsResponse(
        **data,
        faculty=db.query(Faculty).filter_by(dept_code=code).count(),
    ) for code, data in sorted(department_map.items())]
    fee_by_department = agents["finance_agent"].collection_stats(db)
    total_due = round(sum(row["due"] for row in fee_by_department.values()), 2)
    total_collected = round(sum(row["collected"]
                                for row in fee_by_department.values()), 2)
    return PrincipalAnalyticsResponse(
        departments=departments,
        fee_collection=FeeCollectionResponse(
            total_due=total_due,
            total_collected=total_collected,
            total_outstanding=round(total_due - total_collected, 2),
            by_department=fee_by_department,
        ),
        placements=PlacementStatsResponse(**agents["placement_agent"].stats(db)),
        admissions=AdmissionsFunnelResponse(**agents["admission_agent"].funnel(db)),
    )


# ---------- admissions (admin) ---------------------------------------------------------------
@router.get("/admin/admissions", response_model=AdminAdmissionsResponse)
def admissions_list(status: str | None = None, dept: str | None = None,
                    user: User = Depends(require_role("admin", "principal")),
                    db: Session = Depends(get_session)):
    agents = get_agents()
    applications = agents["admission_agent"].list_applications(
        db, status=status, dept=dept)
    return AdminAdmissionsResponse(
        funnel=AdmissionsFunnelResponse(**agents["admission_agent"].funnel(db)),
        applications=[AdmissionApplicationResponse(
            id=row["id"], applicant_name=row["name"], dept_code=row["dept"],
            category=row["category"], tenth_pct=row["tenth"],
            twelfth_pct=row["twelfth"], entrance_score=row["entrance"],
            status=row["status"], merit_score=row["merit_score"],
            merit_rank=row["merit_rank"], allotted_usn=row["usn"],
            notes=row["notes"],
        ) for row in applications],
    )


@router.post("/admin/admissions/verify-all")
def admissions_verify(user: User = Depends(require_role("admin")),
                      db: Session = Depends(get_session)):
    return get_agents()["admission_agent"].verify_all(db)


@router.post("/admin/admissions/run-merit")
def admissions_merit(user: User = Depends(require_role("admin")),
                     db: Session = Depends(get_session)):
    return get_agents()["admission_agent"].run_merit(db)


@router.post("/admin/admissions/allot")
async def admissions_allot(user: User = Depends(require_role("admin")),
                           db: Session = Depends(get_session)):
    return await get_agents()["admission_agent"].allot_seats(db)


class EnrolRequest(BaseModel):
    application_id: int


@router.post("/admin/admissions/enrol")
async def admissions_enrol(body: EnrolRequest,
                           user: User = Depends(require_role("admin")),
                           db: Session = Depends(get_session)):
    return await get_agents()["admission_agent"].enrol(db, body.application_id)


@router.post("/admin/simulate-day")
async def simulate_day(user: User = Depends(require_role("admin")),
                       db: Session = Depends(get_session)):
    """Demo: today's attendance for AIML year-3 section A across 5 subjects."""
    rng = np.random.default_rng()
    students = db.query(Student).filter_by(dept_code="AIML", year=3,
                                           section="A").all()
    from ..models import Subject
    subjects = [s.code for s in db.query(Subject)
                .filter_by(dept_code="AIML", semester=5).all()]
    today = dt.date.today().isoformat()
    records = [{"usn": s.usn, "subject_code": c, "date": today,
                "present": bool(rng.random() < 0.82)}
               for s in students for c in subjects]
    return await get_agents()["attendance_agent"].upload_attendance(
        db, user.username, records)


# ---------- system / research views ---------------------------------------------------------
@router.get("/departments")
def departments(user: User = Depends(require_role(*EXISTING_PORTAL_ROLES)),
                db: Session = Depends(get_session)):
    return {"departments": [{"code": d.code, "name": d.name, "intake": d.intake}
                            for d in db.query(Department).all()]}


@router.get("/agents")
def list_agents(user: User = Depends(require_role(*EXISTING_PORTAL_ROLES))):
    return {"agents": [{"name": a.name, "description": a.description}
                       for a in get_agents().values()],
            "ai_mode": "llm" if llm.runtime_status()["available"] else "lexicon",
            "runtime": llm.runtime_status()}


@router.get("/metrics/summary")
def metrics_summary(user: User = Depends(require_role(*EXISTING_PORTAL_ROLES)),
                    db: Session = Depends(get_session)):
    return metrics.summary(db)


@router.get("/workflows/recent")
def recent_workflows(limit: int = 8, user: User = Depends(require_role(*EXISTING_PORTAL_ROLES)),
                     db: Session = Depends(get_session)):
    from sqlalchemy import func
    from ..models import WorkflowEvent
    rows = (db.query(WorkflowEvent.workflow_id,
                     func.min(WorkflowEvent.created_at),
                     func.max(WorkflowEvent.elapsed_ms),
                     func.count(WorkflowEvent.id),
                     func.max(WorkflowEvent.hop))
              .group_by(WorkflowEvent.workflow_id)
              .order_by(func.min(WorkflowEvent.created_at).desc())
              .limit(limit).all())
    return {"workflows": [
        {"workflow_id": wid, "started_at": str(start),
         "duration_ms": round(dur, 1), "events": n, "depth_hops": hops}
        for wid, start, dur, n, hops in rows]}


@router.get("/workflows/{workflow_id}")
def workflow_trace(workflow_id: str, user: User = Depends(require_role(*EXISTING_PORTAL_ROLES)),
                   db: Session = Depends(get_session)):
    from ..models import WorkflowEvent
    events = (db.query(WorkflowEvent).filter_by(workflow_id=workflow_id)
                .order_by(WorkflowEvent.elapsed_ms).all())
    return {"workflow_id": workflow_id, "events": [
        {"topic": e.topic, "agent": e.agent, "hop": e.hop,
         "elapsed_ms": e.elapsed_ms, "at": str(e.created_at)}
        for e in events]}


# ---------- scholarship workflow -----------------------------------------------------------
def _scholarship_write(db, action):
    try:
        value = action()
        db.commit()
        return value
    except HTTPException:
        db.rollback(); raise
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Scholarship workflow transaction failed")
        raise HTTPException(500, "Scholarship workflow could not be completed; no changes were saved.") from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Scholarship workflow operation failed")
        raise HTTPException(500, "Scholarship workflow could not be completed; no changes were saved.") from exc


def _faculty_scholarship(db, user, scholarship_id):
    row = scholarships._not_found(db.get(Scholarship, scholarship_id))
    scholarships._require(row.created_by_faculty_id == user.faculty_id, "You do not own this scholarship")
    return row


@router.get("/faculty/scholarships")
def faculty_scholarships(user: User = Depends(require_role("faculty")), db: Session = Depends(get_session)):
    rows = db.query(Scholarship).filter_by(created_by_faculty_id=user.faculty_id).order_by(Scholarship.updated_at.desc()).all()
    return {"scholarships": [scholarships.serialize(row) for row in rows]}


@router.post("/faculty/scholarships", status_code=201)
def create_scholarship(body: ScholarshipRequest, user: User = Depends(require_role("faculty")), db: Session = Depends(get_session)):
    def action():
        data = body.model_dump(); data["department_code"] = data["department_code"].upper(); scholarships.validate_payload(data)
        if data["department_code"] != user.dept_code or not db.get(Department, data["department_code"]): raise HTTPException(403, "Faculty may create scholarships only for their department")
        row = Scholarship(**{**data, "criteria": scholarships._dump(data.pop("criteria")), "created_by_faculty_id": user.faculty_id})
        db.add(row); db.flush(); scholarships._event(db, "CREATE", row, user.username); return scholarships.serialize(row)
    return _scholarship_write(db, action)


@router.get("/faculty/scholarships/{scholarship_id}")
def faculty_scholarship(scholarship_id: int, user: User = Depends(require_role("faculty")), db: Session = Depends(get_session)):
    return scholarships.serialize(_faculty_scholarship(db, user, scholarship_id))


@router.put("/faculty/scholarships/{scholarship_id}")
def update_scholarship(scholarship_id: int, body: ScholarshipRequest, user: User = Depends(require_role("faculty")), db: Session = Depends(get_session)):
    def action():
        row = _faculty_scholarship(db, user, scholarship_id)
        if row.status not in {"DRAFT", "CHANGES_REQUESTED"}: raise HTTPException(409, "Published, pending, rejected, and closed scholarships are immutable")
        data = body.model_dump(); data["department_code"] = data["department_code"].upper(); scholarships.validate_payload(data)
        if data["department_code"] != user.dept_code: raise HTTPException(403, "Department cannot be changed outside your scope")
        for key, value in data.items(): setattr(row, key, scholarships._dump(value) if key == "criteria" else value)
        if row.status == "CHANGES_REQUESTED": scholarships._transition(row, "DRAFT")
        row.criteria_version += 1; scholarships._event(db, "EDIT", row, user.username); return scholarships.serialize(row)
    return _scholarship_write(db, action)


@router.delete("/faculty/scholarships/{scholarship_id}", status_code=204)
def delete_scholarship(scholarship_id: int, user: User = Depends(require_role("faculty")), db: Session = Depends(get_session)):
    def action():
        row = _faculty_scholarship(db, user, scholarship_id)
        if row.status != "DRAFT": raise HTTPException(409, "Only drafts may be deleted")
        scholarships._event(db, "DELETE", row, user.username); db.delete(row)
    return _scholarship_write(db, action)


@router.post("/faculty/scholarships/{scholarship_id}/submit")
def submit_scholarship(scholarship_id: int, user: User = Depends(require_role("faculty")), db: Session = Depends(get_session)):
    def action():
        row = _faculty_scholarship(db, user, scholarship_id); scholarships._transition(row, "PENDING_APPROVAL"); scholarships._event(db, "SUBMIT", row, user.username); return scholarships.serialize(row)
    return _scholarship_write(db, action)


@router.post("/faculty/scholarships/{scholarship_id}/withdraw")
def withdraw_scholarship(scholarship_id: int, user: User = Depends(require_role("faculty")), db: Session = Depends(get_session)):
    def action():
        row = _faculty_scholarship(db, user, scholarship_id); scholarships._transition(row, "DRAFT"); scholarships._event(db, "WITHDRAW", row, user.username); return scholarships.serialize(row)
    return _scholarship_write(db, action)


def _hod_scholarship(db, user, scholarship_id):
    row = scholarships._not_found(db.get(Scholarship, scholarship_id)); scholarships._require(row.department_code == user.dept_code, "Scholarship is outside your department"); return row


@router.get("/hod/scholarships")
def hod_scholarships(user: User = Depends(require_role("hod")), db: Session = Depends(get_session)):
    rows = db.query(Scholarship).filter_by(department_code=user.dept_code).order_by(Scholarship.updated_at.desc()).all()
    return {"scholarships": [{**scholarships.serialize(row), "impact": scholarships.aggregate(db, row)} for row in rows]}


@router.get("/hod/scholarships/{scholarship_id}")
def hod_scholarship(scholarship_id: int, user: User = Depends(require_role("hod")), db: Session = Depends(get_session)):
    row = _hod_scholarship(db, user, scholarship_id); return {**scholarships.serialize(row), "impact": scholarships.aggregate(db, row)}


@router.post("/hod/scholarships/{scholarship_id}/request-changes")
def request_changes(scholarship_id: int, body: ScholarshipReviewRequest, user: User = Depends(require_role("hod")), db: Session = Depends(get_session)):
    if not body.comment.strip(): raise HTTPException(422, "A comment is required when requesting changes")
    def action():
        row = _hod_scholarship(db, user, scholarship_id); scholarships._transition(row, "CHANGES_REQUESTED"); row.approval_comment = body.comment.strip(); scholarships._notify(db, "Scholarship changes requested", row.approval_comment, role="faculty", dept=row.department_code, event_key=f"scholarship_changes:{row.id}:v{row.criteria_version}", route="/faculty/scholarships", related_entity_id=row.id); scholarships._event(db, "REQUEST_CHANGES", row, user.username); return scholarships.serialize(row)
    return _scholarship_write(db, action)


@router.post("/hod/scholarships/{scholarship_id}/reject")
def reject_scholarship(scholarship_id: int, body: ScholarshipReviewRequest, user: User = Depends(require_role("hod")), db: Session = Depends(get_session)):
    if not body.comment.strip(): raise HTTPException(422, "A rejection reason is required")
    def action():
        row = _hod_scholarship(db, user, scholarship_id); scholarships._transition(row, "REJECTED"); row.rejection_reason = body.comment.strip(); scholarships._notify(db, "Scholarship rejected", row.rejection_reason, role="faculty", dept=row.department_code, event_key=f"scholarship_rejected:{row.id}", route="/faculty/scholarships", related_entity_id=row.id); scholarships._event(db, "REJECT", row, user.username); return scholarships.serialize(row)
    return _scholarship_write(db, action)


@router.post("/hod/scholarships/{scholarship_id}/approve")
def approve_scholarship(scholarship_id: int, body: ScholarshipReviewRequest, user: User = Depends(require_role("hod")), db: Session = Depends(get_session)):
    def action():
        row = _hod_scholarship(db, user, scholarship_id)
        if "PUBLISHED" not in scholarships.TRANSITIONS.get(row.status, set()):
            raise HTTPException(409, f"Invalid scholarship transition: {row.status} to PUBLISHED")
        assessments = scholarships.evaluate_applicable(db, row, publishing=True)
        for assessment in assessments:
            if assessment.eligibility_status == "ELIGIBLE": scholarships._notify(db, "Scholarship available", f"You are eligible for {row.name}.", usn=assessment.usn, event_key=f"scholarship_available:{row.id}:v{row.criteria_version}", route="/student/scholarships", related_entity_id=row.id)
        scholarships._notify(db, "Scholarship published", f"{row.name} was approved and published.", role="faculty", dept=row.department_code, event_key=f"scholarship_published:{row.id}:v{row.criteria_version}", route="/faculty/scholarships", related_entity_id=row.id)
        scholarships._event(db, "APPROVE", row, user.username); scholarships._event(db, "PUBLISH", row, user.username)
        row.status = "PUBLISHED"; row.approved_by_hod_id = user.faculty_id; row.approval_comment = body.comment.strip(); row.published_at = scholarships.utcnow()
        db.flush()
        return {**scholarships.serialize(row), "impact": scholarships.aggregate(db, row)}
    return _scholarship_write(db, action)


@router.post("/hod/scholarships/{scholarship_id}/close")
def close_scholarship(scholarship_id: int, user: User = Depends(require_role("hod")), db: Session = Depends(get_session)):
    def action():
        row = _hod_scholarship(db, user, scholarship_id); scholarships._transition(row, "CLOSED"); scholarships._event(db, "CLOSE", row, user.username); return scholarships.serialize(row)
    return _scholarship_write(db, action)


@router.get("/student/scholarships")
def student_scholarships(user: User = Depends(require_role("student")), db: Session = Depends(get_session)):
    student = db.get(Student, user.usn); rows = db.query(Scholarship).filter_by(department_code=student.dept_code).filter(Scholarship.status.in_(["PUBLISHED", "CLOSED"])).order_by(Scholarship.closes_at).all()
    output = []
    for row in rows:
        assessment = db.query(ScholarshipAssessment).filter_by(scholarship_id=row.id, usn=user.usn, criteria_version=row.criteria_version).first()
        application = db.query(ScholarshipApplication).filter_by(scholarship_id=row.id, student_usn=user.usn).first()
        output.append(scholarships.serialize(row, assessment, application))
    return {"scholarships": output}


@router.get("/student/scholarships/{scholarship_id}")
def student_scholarship(scholarship_id: int, user: User = Depends(require_role("student")), db: Session = Depends(get_session)):
    student = db.get(Student, user.usn); row = scholarships._not_found(db.get(Scholarship, scholarship_id)); scholarships._require(row.department_code == student.dept_code and row.status in {"PUBLISHED", "CLOSED"})
    assessment = db.query(ScholarshipAssessment).filter_by(scholarship_id=row.id, usn=user.usn, criteria_version=row.criteria_version).first(); application = db.query(ScholarshipApplication).filter_by(scholarship_id=row.id, student_usn=user.usn).first(); return scholarships.serialize(row, assessment, application)


@router.post("/student/scholarships/{scholarship_id}/apply", status_code=201)
def apply_scholarship(scholarship_id: int, body: ScholarshipApplyRequest, user: User = Depends(require_role("student")), db: Session = Depends(get_session)):
    def action():
        student = db.get(Student, user.usn); row = scholarships._not_found(db.get(Scholarship, scholarship_id)); scholarships._require(row.department_code == student.dept_code and row.status == "PUBLISHED")
        if not (row.opens_at <= scholarships.utcnow() < row.closes_at): raise HTTPException(409, "Scholarship is not open")
        assessment = db.query(ScholarshipAssessment).filter_by(scholarship_id=row.id, usn=user.usn, criteria_version=row.criteria_version).first()
        if not assessment or assessment.eligibility_status != "ELIGIBLE": raise HTTPException(409, "Only eligible students may apply")
        if db.query(ScholarshipApplication).filter_by(scholarship_id=row.id, student_usn=user.usn).first(): raise HTTPException(409, "Application already exists")
        application = ScholarshipApplication(scholarship_id=row.id, student_usn=user.usn, external_reference=body.external_reference.strip())
        db.add(application); db.flush()
        scholarships._notify(
            db, "Scholarship application submitted",
            f"Your application for {row.name} was submitted successfully.", usn=user.usn,
            notification_type="SCHOLARSHIP_APPLICATION", event_key=f"scholarship_application:{application.id}",
            route="/student/scholarships", related_entity_id=row.id)
        scholarships._event(db, "APPLY", row, user.username)
        return scholarships.serialize(row, assessment, application)
    return _scholarship_write(db, action)
