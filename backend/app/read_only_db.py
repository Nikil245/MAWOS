"""Canonical, server-side read-only database assistant boundary.

Provider models never call this module.  The deterministic chat classifier
recognises database questions, and FastAPI invokes these fixed handlers with
the authenticated ``User`` object.  The module intentionally returns small
DTO dictionaries rather than ORM objects.
"""
import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from .campus_events import audience_roles, india_today, visible_to
from .models import (AttendanceRecord, AttendanceSummary, CampusEvent, Faculty,
                     Parent, ParentStudent, Student,
                     Subject, TeachingAssignment, User)
from .timetable import reads as timetable_reads


class AllowedIntent(str, Enum):
    get_my_attendance = "get_my_attendance"
    get_my_subject_attendance = "get_my_subject_attendance"
    get_my_marks = "get_my_marks"
    get_my_fee_status = "get_my_fee_status"
    get_my_profile = "get_my_profile"
    get_my_hall_ticket_eligibility = "get_my_hall_ticket_eligibility"
    search_library_catalogue = "search_library_catalogue"
    get_library_book_availability = "get_library_book_availability"
    get_my_placements = "get_my_placements"
    get_visible_campus_events = "get_visible_campus_events"
    get_my_timetable = "get_my_timetable"


_SQLISH = re.compile(
    r"(?:;|--|/\*|\*/|\b(?:select|insert|update|delete|drop|alter|union|pragma|where)\b|"
    r"\b(?:postgres(?:ql)?|mysql|sqlite)(?:\+\w+)?://)", re.I)
_USN = re.compile(r"\b[0-9][A-Za-z0-9]{5,15}\b")
_ID_PARAM_NAMES = {"id", "user_id", "student_id", "database_id", "jwt", "token", "role"}


class AllowedIntentParameters(BaseModel):
    """Only user-facing search/subject selectors are accepted.

    Identity is normally bound to the authenticated session. ``student_usn``
    is accepted only for already-authorized staff/parent flows and is checked
    against the resource policy; it is never trusted as authorization.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str | None = Field(default=None, max_length=128)
    query: str | None = Field(default=None, max_length=128)
    child_usn: str | None = Field(default=None, max_length=16)
    student_usn: str | None = Field(default=None, max_length=16)
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("subject", "query", "child_usn", "student_usn")
    @classmethod
    def safe_parameter(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value or _SQLISH.search(value):
            raise ValueError("unsupported or unsafe parameter")
        return value

    @field_validator("child_usn", "student_usn")
    @classmethod
    def normalized_identity(cls, value):
        return value.upper() if value else value


class AllowedIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: AllowedIntent
    parameters: AllowedIntentParameters = Field(default_factory=AllowedIntentParameters)

    @model_validator(mode="after")
    def intent_parameters_are_supported(self):
        allowed = {
            AllowedIntent.get_my_attendance: {"student_usn", "child_usn"},
            AllowedIntent.get_my_subject_attendance: {"subject", "student_usn", "child_usn"},
            AllowedIntent.get_my_marks: {"subject", "student_usn", "child_usn"},
            AllowedIntent.get_my_fee_status: {"student_usn", "child_usn"},
            AllowedIntent.get_my_profile: set(),
            AllowedIntent.get_my_hall_ticket_eligibility: {"student_usn", "child_usn"},
            AllowedIntent.search_library_catalogue: {"query", "limit"},
            AllowedIntent.get_library_book_availability: {"query", "limit"},
            AllowedIntent.get_my_placements: {"student_usn", "child_usn"},
            AllowedIntent.get_visible_campus_events: {"limit"},
            AllowedIntent.get_my_timetable: {"student_usn", "child_usn"},
        }[self.intent]
        supplied = self.parameters.model_dump(exclude_none=True, exclude_defaults=True)
        if any(name not in allowed for name in supplied):
            raise ValueError("unsupported parameters for intent")
        return self


class AllowedIntentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: AllowedIntent
    parameters: AllowedIntentParameters = Field(default_factory=AllowedIntentParameters)


def validate_provider_intent(payload: str | bytes | dict) -> AllowedIntentResponse:
    """Validate a provider JSON response without giving it execution power."""
    if isinstance(payload, (str, bytes)) and _SQLISH.search(payload.decode() if isinstance(payload, bytes) else payload):
        raise ValueError("provider response contains SQL or connection syntax")
    return AllowedIntentResponse.model_validate_json(payload) if isinstance(payload, (str, bytes)) else AllowedIntentResponse.model_validate(payload)


_INTENT_PATTERNS: tuple[tuple[AllowedIntent, tuple[str, ...]], ...] = (
    (AllowedIntent.get_my_subject_attendance, (r"subject.?wise attendance", r"attendance (?:for|in) each subject", r"attendance in .+")),
    (AllowedIntent.get_my_attendance, (r"my attendance", r"attendance status", r"attendance percentage", r"how much attendance")),
    (AllowedIntent.get_my_marks, (r"my (?:internal )?marks", r"cie marks", r"marks did i get", r"internals?")),
    (AllowedIntent.get_my_fee_status, (r"my fees?", r"fee status", r"fees? (?:do|i) owe", r"payment pending")),
    (AllowedIntent.get_my_profile, (r"my profile", r"my details", r"my name", r"my role", r"my faculty.?id")),
    (AllowedIntent.get_my_hall_ticket_eligibility, (r"hall.?ticket", r"eligible.*exam", r"write.*exam")),
    (AllowedIntent.get_my_timetable, (r"my timetable", r"my time table", r"my class schedule", r"what classes")),
    # Placement must precede every library availability pattern.  ``available``
    # is intentionally not sufficient by itself: it becomes placement only
    # when paired with a placement/company/job/eligibility action signal.
    (AllowedIntent.get_my_placements, (
        r"my placements?", r"placement drives?", r"placement companies?",
        r"(?:eligible|eligibility|available)\b.{0,40}\b(?:companies?|placements?|drives?|jobs?|opportunities?)",
        r"\b(?:companies?|placements?|drives?|jobs?|opportunities?)\b.{0,40}\b(?:eligible|eligibility|available|apply|shortlisted|offer|recruiter)",
        r"available for me", r"can apply", r"apply to", r"shortlisted companies?",
        r"placement status", r"recruiters?",
    )),
    (AllowedIntent.get_visible_campus_events, (r"campus events?", r"college events?", r"upcoming events?")),
    (AllowedIntent.search_library_catalogue, (r"search.*library", r"find .*books?", r"library catalogue", r"books? (?:about|by)")),
    (AllowedIntent.get_library_book_availability, (r"book.*available", r"availability.*book", r"available.*book")),
)


def classify_deterministic(message: str) -> AllowedIntentRequest | None:
    """Classify only the explicit database surface; never calls an LLM."""
    text = " ".join(message.casefold().split())
    if _SQLISH.search(text):
        return None
    # Recommendations retain the existing bounded catalogue-assistant path,
    # which may send only its sanitized catalogue projection to the provider.
    if re.search(r"\brecommend\w*\b", text):
        return None
    intent = next((name for name, patterns in _INTENT_PATTERNS
                   if any(re.search(pattern, text) for pattern in patterns)), None)
    if intent is None:
        return None
    params: dict = {}
    usn = _USN.search(message)
    if usn:
        params["student_usn"] = usn.group(0).upper()
    subject = re.search(r"\b([0-9]{2}[A-Za-z]{2,6}[0-9]{2})\b", message)
    if subject and intent in {AllowedIntent.get_my_subject_attendance, AllowedIntent.get_my_marks}:
        params["subject"] = subject.group(1).upper()
    if intent in {AllowedIntent.search_library_catalogue, AllowedIntent.get_library_book_availability}:
        params["query"] = _library_term(message)
    return AllowedIntentRequest(intent=intent, parameters=params)


def _library_term(message: str) -> str:
    value = re.sub(r"\b(?:search|find|show|check|is|the|a|an|book|books|library|catalogue|catalogue|available|availability|for|in|college)\b", " ", message, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" ?.!\t\n")
    return value[:128] or message[:128]


def _safe_denial() -> dict:
    return {"error": "That record is not available through the read-only assistant. "
                    "You may ask about your own attendance or other authorized records."}


def _overall_percentage(db, usn: str) -> float:
    rows = db.scalars(select(AttendanceRecord.present).where(
        AttendanceRecord.usn == usn)).all()
    return round(100.0 * sum(bool(value) for value in rows) / len(rows), 2) if rows else 0.0


def _student_for_target(db, user, params: AllowedIntentParameters):
    """Resolve only an authenticated or actively linked target."""
    requested = params.student_usn or params.child_usn
    if user.role == "student":
        if requested and requested != user.usn:
            return None
        return db.scalar(select(Student).where(Student.usn == user.usn)) if user.usn else None
    if user.role == "parent":
        parent = db.scalar(select(Parent).where(Parent.user_id == user.id, Parent.active.is_(True)))
        if parent is None:
            return None
        query = select(Student).join(ParentStudent, ParentStudent.student_usn == Student.usn).where(
            ParentStudent.parent_id == parent.id, ParentStudent.active.is_(True))
        if requested:
            query = query.where(Student.usn == requested)
        rows = list(db.scalars(query.limit(2)))
        return rows[0] if len(rows) == 1 else None
    if user.role in {"faculty", "hod", "admin"} and requested:
        student = db.scalar(select(Student).where(Student.usn == requested))
        if student is None:
            return None
        if user.role == "faculty" and not db.scalar(select(TeachingAssignment.id).where(
                TeachingAssignment.faculty_id == user.faculty_id,
                TeachingAssignment.dept_code == student.dept_code,
                TeachingAssignment.year == student.year,
                TeachingAssignment.section == student.section)):
            return None
        if user.role == "hod" and user.dept_code != student.dept_code:
            return None
        return student
    return None


def _student_or_denied(db, user, params):
    student = _student_for_target(db, user, params)
    return student, None if student is not None else _safe_denial()


def _clean_profile(db, user) -> dict:
    row = db.scalar(select(User).where(User.id == user.id))
    if row is None:
        return _safe_denial()
    result = {"display_name": row.display_name, "role": row.role}
    if row.role == "student" and row.usn:
        student = db.scalar(select(Student).where(Student.usn == row.usn))
        if student:
            result.update(department=student.dept_code, year=student.year,
                          semester=student.semester, section=student.section)
    elif row.role in {"faculty", "hod"} and row.faculty_id:
        faculty = db.scalar(select(Faculty).where(Faculty.id == row.faculty_id))
        if faculty:
            result.update(department=faculty.dept_code, designation=faculty.designation)
    elif row.dept_code:
        result["department"] = row.dept_code
    return result


def _clean_events(db, user, limit: int) -> dict:
    today = india_today()
    rows = db.scalars(select(CampusEvent).where(
        CampusEvent.status == "PUBLISHED", CampusEvent.event_date >= today
    ).order_by(CampusEvent.event_date, CampusEvent.start_time, CampusEvent.id).limit(50)).all()
    visible = [row for row in rows if visible_to(row, user, db)][:limit]
    return {"events": [{key: getattr(row, key) for key in (
        "title", "description", "event_date", "start_time", "end_time",
        "venue", "organizer", "department_code", "status")} for row in visible],
            "india_date": today}


def _clean_timetable(db, user, student) -> dict:
    if user.role == "parent":
        data = timetable_reads.view(db, dept=student.dept_code, year=student.year,
                                    semester=student.semester, section=student.section)
    elif user.role == "student":
        data = timetable_reads.personal(db, user)
    elif user.role in {"faculty", "hod"}:
        data = timetable_reads.personal(db, user)
    elif user.role in {"admin", "principal"}:
        return _safe_denial()
    else:
        return _safe_denial()
    data.pop("period_definitions", None)
    for key in ("current", "next"):
        if isinstance(data.get(key), dict):
            data[key] = {field: value for field, value in data[key].items()
                         if field not in {"id", "run_id", "section_id", "faculty_id", "room_id"}}
    data["weekly"] = [{field: value for field, value in item.items()
                       if field not in {"id", "run_id", "section_id", "faculty_id", "room_id"}}
                      for item in data.get("weekly", [])[:50]]
    data["today"] = [{field: value for field, value in item.items()
                      if field not in {"id", "run_id", "section_id", "faculty_id", "room_id"}}
                     for item in data.get("today", [])[:20]]
    return data


def execute(db, agents, user, request: AllowedIntentRequest) -> dict:
    """Execute one fixed, bounded, read-only operation."""
    intent = request.intent
    params = request.parameters
    if user.role not in {"student", "faculty", "hod", "principal", "admin", "parent", "librarian"}:
        return _safe_denial()
    if intent in {AllowedIntent.search_library_catalogue, AllowedIntent.get_library_book_availability}:
        # Preserve the existing assistant policy: catalogue chat is student
        # scoped. Librarians retain their existing catalogue REST workflow.
        if user.role != "student":
            return _safe_denial()
    if intent == AllowedIntent.get_my_profile:
        return _clean_profile(db, user)
    if intent in {AllowedIntent.get_my_attendance, AllowedIntent.get_my_subject_attendance,
                  AllowedIntent.get_my_marks, AllowedIntent.get_my_fee_status,
                  AllowedIntent.get_my_hall_ticket_eligibility, AllowedIntent.get_my_placements}:
        student, denied = _student_or_denied(db, user, params)
        if denied:
            return denied
        if intent in {AllowedIntent.get_my_attendance, AllowedIntent.get_my_subject_attendance}:
            # Reuse the established, tested read-only attendance service. The
            # returned DTO is narrowed below and never passed to a provider.
            if user.role == "parent":
                rows = db.scalars(select(AttendanceSummary).where(
                    AttendanceSummary.usn == student.usn).order_by(AttendanceSummary.subject_code).limit(50)).all()
                names = {code: name for code, name in db.execute(select(Subject.code, Subject.name).where(
                    Subject.code.in_([row.subject_code for row in rows]))).all()}
                legacy = {"overall_pct": _overall_percentage(db, student.usn), "subjects": [
                    {"subject": names.get(row.subject_code, row.subject_code),
                     "subject_code": row.subject_code, "attended": row.classes_attended,
                     "held": row.classes_held, "pct": row.percentage,
                     "shortage": bool(row.shortage)} for row in rows]}
            else:
                from .agents import tools as legacy_tools
                legacy = legacy_tools.execute_chat(db, agents, user, "get_attendance", {})
                if "error" in legacy:
                    return _safe_denial()
            if intent == AllowedIntent.get_my_subject_attendance and params.subject:
                wanted = params.subject.casefold()
                legacy["subjects"] = [row for row in legacy["subjects"]
                                      if wanted in row["subject"].casefold()]
            return {"overall_pct": legacy["overall_pct"],
                    "subjects": [{"subject": row["subject"],
                                  "subject_code": row.get("subject_code"),
                                  "attended": row["attended"], "held": row["held"],
                                  "pct": row["pct"], "shortage": bool(row["shortage"])}
                                 for row in legacy["subjects"]]}
        if intent == AllowedIntent.get_my_marks:
            marks = agents["academic_agent"].student_marks(db, student.usn, params.subject)
            return {"marks": [{key: value for key, value in mark.items()
                               if key in {"subject", "name", "internals", "cie_average"}}
                              for mark in marks[:50]]}
        if intent == AllowedIntent.get_my_fee_status:
            result = agents["finance_agent"].student_fees(db, student.usn)
            return {"cleared": bool(result["cleared"]),
                    "total_outstanding": result["total_outstanding"],
                    "items": [{key: item[key] for key in ("type", "amount_due", "fine", "status", "due_date")}
                              for item in result["items"][:50]]}
        if intent == AllowedIntent.get_my_hall_ticket_eligibility:
            result = agents["eligibility_agent"].hall_ticket_status(db, student.usn)
            return {"eligible": bool(result["eligible"]), "reasons": list(result["reasons"])[:10]}
        drives = agents["placement_agent"].student_view(db, student.usn)
        return {"drives": [{key: drive[key] for key in
                            ("company", "role", "package_lpa", "date", "departments",
                             "application_deadline", "application_url", "eligible",
                             "reasons", "status") if key in drive}
                           for drive in drives[:15]]}
    if intent in {AllowedIntent.search_library_catalogue, AllowedIntent.get_library_book_availability}:
        if not params.query:
            return {"books": []}
        books = agents["library_agent"].search_catalogue(db, params.query, limit=min(params.limit, 50))
        if intent == AllowedIntent.get_library_book_availability:
            books = [book for book in books if book.get("available_copies", 0) > 0][:params.limit]
        return {"books": [{key: book[key] for key in (
            "title", "author", "isbn", "publisher", "category", "description",
            "total_copies", "available_copies", "availability_status", "departments") if key in book}
                       for book in books[:params.limit]]}
    if intent == AllowedIntent.get_visible_campus_events:
        return _clean_events(db, user, params.limit)
    if intent == AllowedIntent.get_my_timetable:
        student = _student_for_target(db, user, params) if user.role in {"student", "parent"} else None
        if user.role == "student" and student is None:
            return _safe_denial()
        if user.role == "parent" and student is None:
            return _safe_denial()
        return _clean_timetable(db, user, student)
    return _safe_denial()


def format_result(intent: AllowedIntent, result: dict) -> str:
    if "error" in result:
        return result["error"]
    if intent in {AllowedIntent.get_my_attendance, AllowedIntent.get_my_subject_attendance}:
        rows = result.get("subjects", [])
        return "Overall attendance: {:.1f}%\n{}".format(
            result.get("overall_pct", 0), "\n".join(
                f"  {row['subject']}: {row['attended']}/{row['held']} = {row['pct']}%" for row in rows))
    if intent == AllowedIntent.get_my_marks:
        return "Internal (CIE) marks:\n" + "\n".join(
            f"  {row['subject']} ({row['name']}): " + ", ".join(f"{key} {value:g}" for key, value in row["internals"].items())
            for row in result.get("marks", []))
    if intent == AllowedIntent.get_my_fee_status:
        return "All fees are cleared ✓" if result.get("cleared") else f"Outstanding: ₹{result.get('total_outstanding', 0):,.0f}"
    if intent == AllowedIntent.get_my_profile:
        labels = {"display_name": "Display name", "role": "Role",
                  "department": "Department", "year": "Year",
                  "semester": "Semester", "section": "Section"}
        return "\n".join(f"{labels.get(key, key.replace('_', ' ').title())}: {value}"
                         for key, value in result.items())
    if intent == AllowedIntent.get_my_hall_ticket_eligibility:
        return "Hall ticket: " + ("ELIGIBLE ✓" if result.get("eligible") else "BLOCKED ✗") + "\n" + "\n".join(result.get("reasons", []))
    if intent == AllowedIntent.get_my_placements:
        lines = []
        for row in result.get("drives", []):
            line = (f"{row['company']} · {row['role']} · {row['package_lpa']} LPA · "
                    f"{row['date']} — {'eligible' if row['eligible'] else row.get('status', 'not eligible')}")
            if row.get("application_deadline"):
                line += f" · deadline {row['application_deadline']}"
            if row.get("application_url"):
                line += f" · apply: {row['application_url']}"
            lines.append(line)
        return "\n".join(lines) or "No placement drives are currently available."
    if intent in {AllowedIntent.search_library_catalogue, AllowedIntent.get_library_book_availability}:
        return "\n".join(f"{book['title']} by {book['author']} — {book.get('available_copies', 0)} available" for book in result.get("books", [])) or "No matching active catalogue book was found."
    if intent == AllowedIntent.get_visible_campus_events:
        return "\n".join(f"{event['event_date']}: {event['title']}" for event in result.get("events", [])) or "No visible upcoming campus events were found."
    if intent == AllowedIntent.get_my_timetable:
        return "Timetable loaded for the authorized account."
    return "The read-only request could not be completed."
