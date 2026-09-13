"""Tool registry — the capabilities the Orchestrator's LLM can invoke.

Each tool: JSON-schema parameters (sent to the LLM), allowed roles,
an executor, and a text formatter used by the offline fallback path.
Role enforcement happens HERE, not in the prompt: a student physically
cannot read another student's record regardless of what the LLM asks for.

12 tools (P2, docs/RESEARCH_PLAN_V3.md §7.1): `get_admissions_funnel` was
retired here because Admission no longer meets the agent criterion and
this was its only chat-facing capability — the admissions funnel itself
is unaffected and still served directly by the admin/principal REST
routes (`AdmissionAgent.funnel`, `backend/app/api/routes.py`).
"""
import re

from ..models import Department, Faculty, FeeRecord, Student, TeachingAssignment, User

STAFF = ("faculty", "hod", "principal", "admin")
ALL_ROLES = ("student",) + STAFF

# Phase 1 assistant surface.  This is deliberately independent of `TOOLS`: the
# registry still supports the existing workflows, while chat can only execute
# the small read-only subset below.
CHAT_READ_ONLY_TOOLS = frozenset({
    "get_attendance", "get_fees", "get_marks", "get_hall_ticket",
    "get_my_profile", "get_department_summary",
})
CHAT_TOOL_ROLES = {
    "get_attendance": ("student", "faculty", "hod", "admin"),
    "get_fees": ("student",),
    "get_marks": ("student", "faculty", "hod", "admin"),
    "get_hall_ticket": ("student",),
    "get_my_profile": ALL_ROLES,
    "get_department_summary": ("hod",),
}

# User-facing capability copy is kept beside the enforced chat policy above.
# ``assistant_capabilities`` filters these descriptions through
# ``CHAT_TOOL_ROLES`` so a role can never be advertised a record category that
# its chat executor does not authorize.
_CAPABILITY_DETAILS = {
    "student": {
        "get_attendance": ("Attendance", "your own attendance", "What is my attendance?"),
        "get_fees": ("Fees", "your own recorded fees", "Show my fee status."),
        "get_marks": ("Internal marks", "your own internal marks", "Show my internal marks."),
        "get_hall_ticket": ("Hall-ticket eligibility", "your own hall-ticket eligibility", "Show my hall-ticket eligibility."),
        "get_my_profile": ("Profile", "your own safe profile", "Show my profile."),
    },
    "faculty": {
        "get_attendance": ("Attendance", "attendance for a student in one of your assigned classes", "Show attendance for [authorized student USN]."),
        "get_marks": ("Internal marks", "marks for an authorized student in one of your assigned subjects", "Show internal marks for [authorized student USN] in subject [assigned subject code]."),
        "get_my_profile": ("Profile", "the authenticated faculty profile", "What is my faculty ID?"),
    },
    "hod": {
        "get_attendance": ("Attendance", "attendance for a student in your department", "Show attendance for [student USN in your department]."),
        "get_marks": ("Internal marks", "marks for a student in your department in a subject assigned there", "Show internal marks for [student USN in your department] in subject [subject code]."),
        "get_my_profile": ("Profile", "your own safe HOD profile", "Show my profile."),
        "get_department_summary": ("Department summary", "student and faculty counts for your own department", "Give me the student and faculty count for my department."),
    },
    "admin": {
        "get_attendance": ("Attendance", "attendance for a specified student", "Show attendance for [student USN]."),
        "get_marks": ("Internal marks", "internal marks for a specified student", "Show internal marks for [student USN]."),
        "get_my_profile": ("Profile", "your own safe admin profile", "Show my profile."),
    },
    "principal": {
        "get_my_profile": ("Profile", "your own safe principal profile", "Show my profile."),
    },
}

_ROLE_CAPABILITY_COPY = {
    "student": {
        "title": "Student academic assistant",
        "subtitle": "Your read-only records and grounded academic help",
        "description": ("You may ask about your own attendance, fees, internal marks, and "
                        "hall-ticket eligibility and safe profile, search the read-only library catalogue, "
                        "approved MAWOS explanations, and general learning questions."),
        "placeholder": "Ask about your academic information…",
        "record_group": "My records",
    },
    "faculty": {
        "title": "Faculty academic assistant",
        "subtitle": "Assignment-scoped records and grounded academic help",
        "description": ("You may view attendance for students in your assigned classes and marks only "
                        "for an authorized student and assigned subject. You may also ask approved academic "
                        "and MAWOS questions, your authenticated safe profile, plus general learning questions. To find assigned subjects and student rosters, use the Faculty Dashboard."),
        "placeholder": "Ask about an authorized student or academic topic…",
        "record_group": "Assigned students",
    },
    "hod": {
        "title": "HOD academic assistant",
        "subtitle": "Department-scoped records and grounded academic help",
        "description": ("You may view attendance for students in your department and marks for a department "
                        "student when you provide a subject code assigned in that department. You may also ask "
                        "for your own safe profile, department student and faculty counts, approved MAWOS questions and general learning questions."),
        "placeholder": "Ask about department records, counts, or an academic topic…",
        "record_group": "Department records",
    },
    "principal": {
        "title": "Principal academic assistant",
        "subtitle": "Grounded academic and MAWOS help",
        "description": ("You may view your own safe profile. Other personal-record chat access is not "
                        "currently defined for the principal role. You may ask approved MAWOS questions "
                        "and general learning questions."),
        "placeholder": "Ask an academic or MAWOS question…",
        "record_group": "Record access",
    },
    "admin": {
        "title": "Admin academic assistant",
        "subtitle": "Authorized read-only records and grounded academic help",
        "description": ("You may view attendance and internal marks for a specified student under the current "
                        "chat policy and your own safe profile. You may also ask approved MAWOS questions and general learning questions. Fees and "
                        "hall-ticket eligibility are not available to admin through chat."),
        "placeholder": "Ask about a supported student record or academic topic…",
        "record_group": "Supported records",
    },
}


def assistant_capabilities(role: str, display_name: str | None = None) -> dict:
    """Return display-safe capabilities derived from the enforced chat policy.

    This function is intentionally database-free. It exposes friendly scopes
    and examples, not tool names, implementation details, or record data.
    """
    copy = _ROLE_CAPABILITY_COPY.get(role, _ROLE_CAPABILITY_COPY["principal"])
    configured = _CAPABILITY_DETAILS.get(role, {})
    records = [
        {"category": configured[name][0], "scope": configured[name][1]}
        for name, allowed_roles in CHAT_TOOL_ROLES.items()
        if role in allowed_roles and name in configured
    ]
    record_prompts = [
        configured[name][2]
        for name, allowed_roles in CHAT_TOOL_ROLES.items()
        if role in allowed_roles and name in configured
    ]
    if role == "faculty":
        record_prompts.append("How do I find my assigned subjects?")
    mawos_prompts = ["What is a CIE?", "What is MAWOS?"]
    general_prompts = [
        "Explain machine learning simply.", "What is SQL normalization?",
        "Help me understand deadlocks.", "Create a short study plan.",
    ]
    help_prompts = ["What can you help me with?", "Who are you?"]
    greeting_name = (display_name or "").strip()
    greeting = f"Hello {greeting_name}. {copy['description']}" if greeting_name else f"Hello! {copy['description']}"
    examples = record_prompts[:2] + general_prompts[:1]
    help_text = copy["description"]
    if examples:
        help_text += " Examples: " + " ".join(examples)
    suggestion_groups = []
    if record_prompts:
        suggestion_groups.append({"label": copy["record_group"], "prompts": record_prompts})
    if role == "student":
        suggestion_groups.append({"label": "Library catalogue", "prompts": [
            "Find available AIML books",
            "Recommend a Python book from the library",
            "Check a book's availability",
        ]})
    suggestion_groups.extend([
        {"label": "MAWOS help", "prompts": mawos_prompts},
        {"label": "General learning", "prompts": general_prompts},
        {"label": "Assistant help", "prompts": help_prompts},
    ])
    return {
        "role": role,
        "title": copy["title"],
        "subtitle": copy["subtitle"],
        "description": copy["description"],
        "greeting": greeting,
        "help": help_text,
        "input_placeholder": copy["placeholder"],
        "record_capabilities": records,
        "suggestion_groups": suggestion_groups,
    }

_UNDEFINED_POLICY = (
    "This individual record is not available through chat for your role "
    "because no read permission has been defined."
)
_USN_IN_TEXT = re.compile(r"\b([0-9][A-Za-z0-9]{5,15})\b")
_SUBJECT_IN_TEXT = re.compile(r"\b([0-9]{2}[A-Za-z]{2,6}[0-9]{2})\b")
_DEPARTMENT_COUNT = re.compile(
    r"\b(?:how many|count|number of|department strength|students?|faculty|teachers?)\b", re.I)


def department_summary_request(db, user: User, message: str) -> str | None:
    """Classify an aggregate request and detect an explicitly foreign department.

    This is intentionally database-backed only for the public department-code/name
    vocabulary; it returns no count or other institutional data.
    """
    text = " ".join(message.casefold().replace("&", " and ").split())
    has_people = bool(re.search(r"\b(?:students?|faculty|teachers?)\b", text))
    has_count = bool(_DEPARTMENT_COUNT.search(text))
    if not (has_people and has_count) and "department strength" not in text:
        return None
    # Do not reinterpret ordinary student-record prose as an aggregate. A
    # department reference (or an explicit uppercase department code) is required.
    if (not re.search(r"\b(?:department|dept)\b", text)
            and not re.search(r"\b[A-Z]{3,8}\b", message)):
        return None
    requested = None
    for department in db.query(Department).all():
        code = department.code.casefold()
        name = " ".join(department.name.casefold().replace("&", " and ").split())
        aliases = {code, name, code.replace("ml", " ml"), name.replace(" and ", " ")}
        if any(alias and re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text) for alias in aliases):
            requested = department.code
            break
    explicit = re.search(r"\b([a-z]{2,8})\s+(?:department|dept)\b", text)
    if explicit and explicit.group(1) not in {"my", "own", "the", user.dept_code.casefold()} and not requested:
        return "conflict"
    if requested and requested != user.dept_code:
        return "conflict"
    # A named own department and 'my department' are both scoped server-side.
    if requested or re.search(r"\b(?:my|own|the)\s+(?:department|dept)\b|\bdepartment\b", text):
        return "summary"
    return None


def _resolve_usn(db, user: User, args: dict):
    """Students are locked to themselves; staff may pass any USN."""
    if user.role == "student":
        return user.usn, None
    usn = str(args.get("usn") or "").upper().strip()
    if not usn:
        return None, "Please specify the student USN."
    if db.get(Student, usn) is None:
        return None, f"Unknown USN {usn}."
    return usn, None


def _student_ctx(db, user: User):
    return db.get(Student, user.usn) if user.usn else None


def chat_args_from_message(message: str) -> dict:
    """Extract only identifiers needed for deterministic staff queries.

    The values remain untrusted inputs and are always checked again by the
    resource policy in `execute_chat`.
    """
    args = {}
    if match := _USN_IN_TEXT.search(message):
        args["usn"] = match.group(1).upper()
    if match := _SUBJECT_IN_TEXT.search(message):
        args["subject_code"] = match.group(1).upper()
    return args


def _chat_target(db, user: User, args: dict) -> tuple[Student | None, str | None]:
    """Resolve a chat target without allowing a student-supplied override.

    Looking up the target is only used to verify class/department scope; no
    academic, financial, or eligibility data is fetched until authorization
    succeeds.
    """
    if user.role == "student":
        student = db.get(Student, user.usn) if user.usn else None
        return student, None if student else _UNDEFINED_POLICY
    usn = str(args.get("usn") or "").upper().strip()
    if not usn:
        if user.role == "faculty":
            return None, "Please specify an authorized student USN from one of your assigned classes."
        if user.role == "hod":
            return None, "Please specify a student USN from your department."
        return None, "Please specify a student USN for this request."
    student = db.get(Student, usn)
    # Avoid using this API as a student-record enumeration endpoint.
    if student is None:
        return None, "You are not authorized to view that student record."
    return student, None


def _has_class_assignment(db, user: User, student: Student) -> bool:
    return user.role == "faculty" and user.faculty_id is not None and (
        db.query(TeachingAssignment).filter_by(
            faculty_id=user.faculty_id,
            dept_code=student.dept_code,
            year=student.year,
            section=student.section,
        ).first() is not None
    )


def _authorize_chat_attendance(db, user: User, args: dict) -> tuple[Student | None, str | None]:
    if user.role not in CHAT_TOOL_ROLES["get_attendance"]:
        return None, _UNDEFINED_POLICY
    student, error = _chat_target(db, user, args)
    if error:
        return None, error
    assert student is not None
    if user.role == "faculty" and not _has_class_assignment(db, user, student):
        return None, "You are not authorized to view attendance for that student."
    if user.role == "hod" and user.dept_code != student.dept_code:
        return None, "You are not authorized to view attendance for that student."
    return student, None


def _authorize_chat_marks(db, user: User, args: dict) -> tuple[Student | None, str | None, str | None]:
    if user.role not in CHAT_TOOL_ROLES["get_marks"]:
        return None, None, _UNDEFINED_POLICY
    student, error = _chat_target(db, user, args)
    if error:
        return None, None, error
    assert student is not None
    if user.role in ("student", "admin"):
        return student, None, None

    subject_code = str(args.get("subject_code") or "").upper().strip()
    if not subject_code:
        if user.role == "faculty":
            return None, None, "Please specify one of your assigned subject codes for this marks request."
        return None, None, "Please specify a subject code assigned in your department for this marks request."
    if user.role == "faculty":
        assigned = db.query(TeachingAssignment).filter_by(
            faculty_id=user.faculty_id,
            subject_code=subject_code,
            dept_code=student.dept_code,
            year=student.year,
            section=student.section,
        ).first()
        if assigned is None:
            return None, None, "You are not authorized to view that student and subject combination."
    elif user.role == "hod":
        if user.dept_code != student.dept_code:
            return None, None, "You are not authorized to view that student and subject combination."
        assigned = db.query(TeachingAssignment).filter_by(
            subject_code=subject_code, dept_code=student.dept_code).first()
        if assigned is None:
            return None, None, "You are not authorized to view that student and subject combination."
    return student, subject_code, None


TOOLS: dict[str, dict] = {}


def tool(name, description, params=None, roles=ALL_ROLES):
    def wrap(fn):
        TOOLS[name] = {
            "name": name, "description": description,
            "parameters": {"type": "object",
                           "properties": params or {},
                           "required": []},
            "roles": roles, "fn": fn,
        }
        return fn
    return wrap


USN_PARAM = {"usn": {"type": "string",
                     "description": "Student USN, e.g. 4MT23AI049 "
                                    "(staff only; students get their own)"}}
MARKS_PARAM = {**USN_PARAM, "subject_code": {
    "type": "string",
    "description": "Required for faculty/HOD marks requests; e.g. 23AI51",
}}


@tool("get_my_profile",
      "Safe profile fields for the currently authenticated user. Accepts no target arguments.")
def get_my_profile(db, agents, user, args):
    return _chat_my_profile(db, agents, user, args)


@tool("get_student_overview",
      "Full academic overview of a student: profile, attendance, fees, "
      "hall ticket, scholarship status.", USN_PARAM)
def get_student_overview(db, agents, user, args):
    usn, err = _resolve_usn(db, user, args)
    if err:
        return {"error": err}
    profile = agents["academic_agent"].student_profile(db, usn)
    from .attendance import overall_percentage
    fees = agents["finance_agent"].student_fees(db, usn)
    from ..models import HallTicket, ScholarshipAssessment
    ht = db.query(HallTicket).filter_by(usn=usn).first()
    sch = db.query(ScholarshipAssessment).filter_by(usn=usn).first()
    return {"profile": profile,
            "overall_attendance_pct": overall_percentage(db, usn),
            "fees_cleared": fees["cleared"],
            "fees_outstanding": fees["total_outstanding"],
            "hall_ticket": {"eligible": ht.eligible, "reasons": ht.reasons} if ht else None,
            "scholarship": {"status": sch.status, "reasons": sch.reasons} if sch else None}


@tool("get_attendance", "Per-subject attendance percentages for a student.",
      USN_PARAM)
def get_attendance(db, agents, user, args):
    usn, err = _resolve_usn(db, user, args)
    if err:
        return {"error": err}
    from ..models import AttendanceSummary
    from .attendance import overall_percentage
    subs = db.query(AttendanceSummary).filter_by(usn=usn).all()
    return {"usn": usn, "overall_pct": overall_percentage(db, usn),
            "subjects": [{"subject": s.subject_code, "attended": s.classes_attended,
                          "held": s.classes_held, "pct": s.percentage,
                          "shortage": s.shortage} for s in subs]}


@tool("get_marks", "Internal (CIE) marks per subject for a student.", MARKS_PARAM)
def get_marks(db, agents, user, args):
    usn, err = _resolve_usn(db, user, args)
    if err:
        return {"error": err}
    return {"usn": usn, "marks": agents["academic_agent"].student_marks(db, usn)}


@tool("get_fees", "Fee items, dues, fines and payment status for a student.",
      USN_PARAM)
def get_fees(db, agents, user, args):
    usn, err = _resolve_usn(db, user, args)
    if err:
        return {"error": err}
    return {"usn": usn, **agents["finance_agent"].student_fees(db, usn)}


@tool("get_hall_ticket", "Hall-ticket (exam) eligibility with reasons.", USN_PARAM)
def get_hall_ticket(db, agents, user, args):
    usn, err = _resolve_usn(db, user, args)
    if err:
        return {"error": err}
    # A query must never issue or update a hall ticket.  Event-driven and
    # explicit workflows still use `evaluate_hall_ticket`, which persists.
    return agents["eligibility_agent"].hall_ticket_status(db, usn)


@tool("get_scholarship", "Scholarship eligibility (rules + CART score).", USN_PARAM)
def get_scholarship(db, agents, user, args):
    usn, err = _resolve_usn(db, user, args)
    if err:
        return {"error": err}
    result = agents["eligibility_agent"].evaluate_scholarship(db, usn)
    db.commit()
    return result


@tool("get_placements", "Upcoming placement drives and the student's "
      "eligibility/success probability (final years).", USN_PARAM)
def get_placements(db, agents, user, args):
    if user.role == "student":
        return {"drives": agents["placement_agent"].student_view(db, user.usn)}
    usn = str(args.get("usn") or "").upper().strip()
    if usn:
        return {"drives": agents["placement_agent"].student_view(db, usn)}
    return agents["placement_agent"].stats(db)


@tool("get_timetable", "Weekly class timetable. Students/faculty get their own "
      "automatically; staff may pass dept/year/section.",
      {"dept": {"type": "string"}, "year": {"type": "integer"},
       "section": {"type": "string"}})
def get_timetable(db, agents, user, args):
    from ..timetable import reads
    if user.role == "student":
        student = _student_ctx(db, user)
        return reads.grid(db, student.dept_code, student.year, student.section, semester=student.semester)
    if user.role in ("faculty", "hod") and not args.get("dept"):
        return reads.grid(db, faculty_id=user.faculty_id)
    return reads.authorized_grid(db, user, str(args.get("dept") or user.dept_code or "AIML").upper(),
                                 int(args.get("year") or 3), str(args.get("section") or "A").upper())


@tool("get_exam_schedule", "Semester-end exam schedule for a dept/semester.",
      {"dept": {"type": "string"}, "semester": {"type": "integer"}})
def get_exam_schedule(db, agents, user, args):
    if user.role == "student":
        s = _student_ctx(db, user)
        dept, sem = s.dept_code, s.semester
    else:
        dept = str(args.get("dept") or "AIML").upper()
        sem = int(args.get("semester") or 5)
    return {"dept": dept, "semester": sem,
            "exams": agents["eligibility_agent"].schedule_for(db, dept, sem)}


@tool("get_notifications", "The caller's recent notifications.")
def get_notifications(db, agents, user, args):
    return {"notifications": agents["notification_agent"].for_user(
        db, user_id=user.id)}


@tool("get_dept_analytics", "Department analytics: headcount, average "
      "attendance/CGPA, shortage counts, fee defaulters.",
      {"dept": {"type": "string"}}, roles=STAFF)
def get_dept_analytics(db, agents, user, args):
    dept = str(args.get("dept") or user.dept_code or "AIML").upper()
    if user.role in ("faculty", "hod") and user.dept_code:
        dept = user.dept_code   # staff scoped to their department
    data = agents["academic_agent"].dept_analytics(db, dept)
    data["fee_defaulters"] = agents["finance_agent"].defaulter_list(db, dept, limit=10)
    return data


@tool("get_institution_analytics", "Institution-wide analytics across all "
      "departments (principal/admin view).", roles=("principal", "admin"))
def get_institution_analytics(db, agents, user, args):
    return {"departments": agents["academic_agent"].institution_analytics(db),
            "fee_collection": agents["finance_agent"].collection_stats(db),
            "placements": agents["placement_agent"].stats(db)}


# ---------------------------------------------------------------- chat-only tools
def _chat_attendance(db, agents, user, args):
    student, error = _authorize_chat_attendance(db, user, args)
    if error:
        return {"error": error}
    assert student is not None
    from ..models import AttendanceSummary
    from .attendance import overall_percentage
    subs = db.query(AttendanceSummary).filter_by(usn=student.usn).all()
    return {"usn": student.usn, "overall_pct": overall_percentage(db, student.usn),
            "subjects": [{"subject": s.subject_code, "attended": s.classes_attended,
                          "held": s.classes_held, "pct": s.percentage,
                          "shortage": s.shortage} for s in subs]}


def _chat_marks(db, agents, user, args):
    student, subject_code, error = _authorize_chat_marks(db, user, args)
    if error:
        return {"error": error}
    assert student is not None
    return {"usn": student.usn,
            "marks": agents["academic_agent"].student_marks(
                db, student.usn, subject_code=subject_code)}


def _chat_fees(db, agents, user, args):
    if user.role != "student":
        return {"error": _UNDEFINED_POLICY}
    student, error = _chat_target(db, user, args)
    if error:
        return {"error": error}
    assert student is not None
    result = agents["finance_agent"].student_fees(db, student.usn)
    paid_by_id = {row.id: row.amount_paid or 0.0 for row in
                  db.query(FeeRecord).filter_by(usn=student.usn).all()}
    result["items"] = [{**item, "amount_paid": paid_by_id.get(item["id"], 0.0)}
                       for item in result["items"]]
    return {"usn": student.usn, **result}


def _chat_hall_ticket(db, agents, user, args):
    if user.role != "student":
        return {"error": _UNDEFINED_POLICY}
    student, error = _chat_target(db, user, args)
    if error:
        return {"error": error}
    assert student is not None
    return agents["eligibility_agent"].hall_ticket_status(db, student.usn)


def _chat_my_profile(db, agents, user, args):
    """Return a fixed projection for the authenticated user; targets are forbidden."""
    if args:
        return {"error": "Profile lookup does not accept a target."}
    authenticated_id = getattr(user, "id", None)
    if authenticated_id is None:
        return {"error": _UNDEFINED_POLICY}
    current = db.get(User, authenticated_id)
    if current is None:
        return {"error": _UNDEFINED_POLICY}
    profile = {"display_name": current.display_name, "role": current.role}
    if current.role == "student":
        student = db.get(Student, current.usn) if current.usn else None
        if student is None:
            return {"error": _UNDEFINED_POLICY}
        profile.update(usn=student.usn, department=student.dept_code,
                       year=student.year, semester=student.semester,
                       section=student.section)
    elif current.role in {"faculty", "hod"}:
        faculty = db.get(Faculty, current.faculty_id) if current.faculty_id else None
        profile.update(department=current.dept_code)
        if faculty is not None:
            profile.update(faculty_id=faculty.id, department=faculty.dept_code,
                           designation=faculty.designation)
    elif current.dept_code:
        profile["department"] = current.dept_code
    return profile


def _chat_department_summary(db, agents, user, args):
    if user.role != "hod" or args or not user.dept_code:
        return {"error": _UNDEFINED_POLICY}
    return agents["academic_agent"].department_summary(db, user.dept_code)


_CHAT_EXECUTORS = {
    "get_attendance": _chat_attendance,
    "get_marks": _chat_marks,
    "get_fees": _chat_fees,
    "get_hall_ticket": _chat_hall_ticket,
    "get_my_profile": _chat_my_profile,
    "get_department_summary": _chat_department_summary,
}


def model_facts(tool_name: str, result: dict) -> dict:
    """Return only allowlisted, authorized fields safe to send to Ollama.

    The model never receives USNs, database IDs, credentials, raw exceptions,
    or broad ORM/JSON dumps. Values are data, not instructions.
    """
    if tool_name == "get_attendance":
        return {"overall_pct": result["overall_pct"], "subjects": [
            {key: subject[key] for key in ("subject", "attended", "held", "pct", "shortage")}
            for subject in result["subjects"]
        ]}
    if tool_name == "get_fees":
        return {"cleared": result["cleared"], "total_outstanding": result["total_outstanding"],
                "items": [{key: (item.get(key, 0.0) if key == "amount_paid" else item[key]) for key in
                           ("type", "amount_due", "amount_paid", "fine", "status", "due_date")}
                          for item in result["items"]]}
    if tool_name == "get_marks":
        return {"marks": [{key: mark[key] for key in
                            ("subject", "name", "internals", "cie_average")}
                          for mark in result["marks"]]}
    if tool_name == "get_hall_ticket":
        return {"eligible": result["eligible"], "reasons": list(result["reasons"])}
    if tool_name == "get_my_profile":
        allowed = {"display_name", "role", "usn", "faculty_id", "department",
                   "year", "semester", "section", "designation"}
        return {key: value for key, value in result.items() if key in allowed}
    if tool_name == "get_department_summary":
        return {key: result[key] for key in
                ("department_code", "department_name", "student_count", "faculty_count")}
    raise ValueError("unsupported chat tool")


def schemas_for_role(role: str) -> list[dict]:
    """Ollama tools array, filtered by the caller's role."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["parameters"]}}
            for t in TOOLS.values() if role in t["roles"]]


def chat_schemas_for_role(role: str) -> list[dict]:
    """Schemas for the Phase 1 assistant's read-only capability set.

    Student targets come exclusively from the authenticated session, so their
    model-visible schemas intentionally expose no USN or subject parameters.
    Staff retain only the parameters checked by their resource policy.
    """
    def parameters_for(t: dict) -> dict:
        if role == "student":
            return {"type": "object", "properties": {}, "required": []}
        return t["parameters"]

    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": parameters_for(t)}}
            for name, t in TOOLS.items()
            if name in CHAT_READ_ONLY_TOOLS and role in CHAT_TOOL_ROLES[name]]


def execute(db, agents, user, name: str, args: dict) -> dict:
    t = TOOLS.get(name)
    if t is None:
        return {"error": f"unknown tool {name}"}
    if user.role not in t["roles"]:
        return {"error": f"role '{user.role}' is not permitted to use {name}"}
    try:
        return t["fn"](db, agents, user, args or {})
    except Exception as exc:  # Legacy non-chat callers retain their existing contract.
        return {"error": f"{type(exc).__name__}: {exc}"}


def execute_chat(db, agents, user, name: str, args: dict | None) -> dict:
    """Execute only a Phase 1 read-only tool under resource-specific policy."""
    if name not in CHAT_READ_ONLY_TOOLS:
        return {"error": "That capability is not available in the read-only assistant."}
    if user.role not in CHAT_TOOL_ROLES[name]:
        return {"error": _UNDEFINED_POLICY}
    if not isinstance(args, dict):
        return {"error": "The assistant could not understand the requested record."}
    if name == "get_my_profile" and args:
        return {"error": "Profile lookup does not accept a target."}
    try:
        return _CHAT_EXECUTORS[name](db, agents, user, args)
    except Exception:
        return {"error": "The requested institutional data is temporarily unavailable."}
