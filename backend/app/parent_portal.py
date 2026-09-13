"""Admin-managed parent identities and strictly linked, read-only child views."""
import datetime as dt
import secrets
import string

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from . import scholarships
from .agents import get_agents
from .agents.attendance import overall_percentage
from .api.schemas import (AdminParentListResponse, AdminParentResponse,
                          ParentDashboardResponse, ParentProfileResponse)
from .auth import hash_password, require_role
from .campus_events import audience_roles, india_today, public_record as event_record
from .database import get_session
from .models import (AttendanceSummary, CampusEvent, HallTicket, Parent,
                     ParentStudent, Student, User, utcnow)
from .timetable import reads as timetable_reads


router = APIRouter(prefix="/api", tags=["parent-portal"])
RELATIONSHIPS = {"Father", "Mother", "Guardian", "Other"}


def normalize_usn(value: str) -> str:
    return str(value or "").strip().upper()


def generated_password() -> str:
    # 18 characters, with every required character class and no ambiguous spaces.
    alphabet = string.ascii_letters + string.digits + "!@#$%*-_"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(18))
        if (any(c.islower() for c in value) and any(c.isupper() for c in value)
                and any(c.isdigit() for c in value) and any(c in "!@#$%*-_" for c in value)):
            return value


def parent_for_user(db, user: User) -> Parent:
    row = db.query(Parent).filter(Parent.user_id == user.id, Parent.active.is_(True)).one_or_none()
    if row is None:
        raise HTTPException(status_code=403, detail="Parent account is inactive")
    return row


def require_parent(user: User = Depends(require_role("parent")), db=Depends(get_session)):
    return parent_for_user(db, user)


def child_for_parent(db, parent: Parent, usn: str) -> Student:
    normalized = normalize_usn(usn)
    row = (db.query(Student).join(ParentStudent, ParentStudent.student_usn == Student.usn)
           .filter(ParentStudent.parent_id == parent.id,
                   ParentStudent.active.is_(True), Student.usn == normalized).one_or_none())
    if row is None:
        # 403 is intentional: authenticated parents must get a stable denial for
        # an unlinked or newly-disabled USN without receiving student data.
        raise HTTPException(status_code=403, detail="Student is not linked to this parent")
    return row


def child_record(student: Student, mapping: ParentStudent | None = None) -> dict:
    result = {"usn": student.usn, "name": student.name, "department": student.dept_code,
              "year": student.year, "semester": student.semester, "section": student.section}
    if mapping is not None:
        result.update(relationship=mapping.relationship, is_primary=bool(mapping.is_primary),
                      mapping_id=mapping.id, active=bool(mapping.active))
    return result


class StudentLinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    student_usn: str = Field(min_length=1, max_length=16)
    relationship: str = "Guardian"
    is_primary: bool = False

    @field_validator("student_usn")
    @classmethod
    def normalized_usn(cls, value):
        return normalize_usn(value)

    @field_validator("relationship")
    @classmethod
    def valid_relationship(cls, value):
        value = value.title()
        if value not in RELATIONSHIPS:
            raise ValueError("Relationship must be Father, Mother, Guardian, or Other")
        return value


class ParentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    full_name: str = Field(min_length=2, max_length=128)
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    email: str | None = Field(default=None, max_length=128)
    mobile: str | None = Field(default=None, max_length=20)
    temporary_password: str | None = Field(default=None, min_length=10, max_length=128)
    students: list[StudentLinkInput] = Field(min_length=1, max_length=25)

    @field_validator("mobile")
    @classmethod
    def clean_mobile(cls, value):
        return value or None

    @field_validator("temporary_password")
    @classmethod
    def strong_temporary_password(cls, value):
        if value is not None and not (any(char.isalpha() for char in value)
                                      and any(char.isdigit() for char in value)):
            raise ValueError("Temporary password must contain letters and numbers")
        return value

    @field_validator("email")
    @classmethod
    def clean_email(cls, value):
        if not value:
            return None
        value = value.lower()
        if value.count("@") != 1 or value.startswith("@") or value.endswith("@"):
            raise ValueError("Enter a valid email address")
        return value


class ParentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    full_name: str = Field(min_length=2, max_length=128)
    email: str | None = Field(default=None, max_length=128)
    mobile: str | None = Field(default=None, max_length=20)

    @field_validator("email")
    @classmethod
    def clean_email(cls, value):
        return ParentCreate.clean_email(value)


class ActiveUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool


def ensure_students(db, links: list[StudentLinkInput]):
    usns = [link.student_usn for link in links]
    if len(usns) != len(set(usns)):
        raise HTTPException(409, "The same student cannot be linked more than once")
    found = {usn for usn, in db.query(Student.usn).filter(Student.usn.in_(usns)).all()}
    missing = sorted(set(usns) - found)
    if missing:
        raise HTTPException(422, f"Unknown student USN: {missing[0]}")


def admin_parent_record(parent, user, links) -> dict:
    return {"id": parent.id, "full_name": parent.full_name, "username": user.username,
            "email": parent.email, "mobile": parent.mobile, "active": bool(parent.active),
            "created_at": parent.created_at,
            "students": [child_record(student, mapping) for mapping, student in links]}


@router.get("/admin/students/search")
def search_students(q: str = Query("", max_length=64), limit: int = Query(20, ge=1, le=50),
                    user=Depends(require_role("admin")), db=Depends(get_session)):
    term = q.strip()
    query = db.query(Student)
    if term:
        like = f"%{term.lower()}%"
        query = query.filter(or_(func.lower(Student.usn).like(like),
                                 func.lower(Student.name).like(like)))
    rows = query.order_by(Student.usn).limit(limit).all()
    return {"students": [child_record(row) for row in rows]}


@router.post("/admin/parents", status_code=201, response_model=AdminParentResponse)
def create_parent(body: ParentCreate, user=Depends(require_role("admin")), db=Depends(get_session)):
    ensure_students(db, body.students)
    if db.query(User.id).filter(func.lower(User.username) == body.username.lower()).first():
        raise HTTPException(409, "Username is already in use")
    supplied = body.temporary_password is not None
    temporary_password = body.temporary_password or generated_password()
    account = User(username=body.username, password_hash=hash_password(temporary_password),
                   role="parent", display_name=body.full_name, must_change_password=True)
    parent = Parent(user=account, full_name=body.full_name,
                    email=str(body.email).lower() if body.email else None, mobile=body.mobile)
    try:
        db.add(parent)
        db.flush()
        for link in body.students:
            db.add(ParentStudent(parent_id=parent.id, **link.model_dump()))
        db.commit()
        db.refresh(parent)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Parent username, email, or student mapping already exists") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Parent account could not be created") from None
    result = admin_parent_record(parent, account, [
        (mapping, mapping.student) for mapping in db.query(ParentStudent).filter_by(parent_id=parent.id).all()])
    # Plaintext exists only in this one response when MAWOS generated it.
    result["generated_credentials"] = None if supplied else {
        "username": account.username, "temporary_password": temporary_password}
    return result


def parent_links(db, parent_ids):
    grouped = {parent_id: [] for parent_id in parent_ids}
    if not parent_ids:
        return grouped
    rows = (db.query(ParentStudent, Student)
            .join(Student, Student.usn == ParentStudent.student_usn)
            .filter(ParentStudent.parent_id.in_(parent_ids))
            .order_by(ParentStudent.parent_id, ParentStudent.active.desc(), Student.usn).all())
    for mapping, student in rows:
        grouped[mapping.parent_id].append((mapping, student))
    return grouped


@router.get("/admin/parents", response_model=AdminParentListResponse)
def list_parents(q: str = Query("", max_length=64), limit: int = Query(100, ge=1, le=200),
                 offset: int = Query(0, ge=0), user=Depends(require_role("admin")),
                 db=Depends(get_session)):
    query = db.query(Parent, User).join(User, User.id == Parent.user_id)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.filter(or_(func.lower(Parent.full_name).like(like),
                                 func.lower(User.username).like(like)))
    total = query.count()
    rows = query.order_by(Parent.created_at.desc(), Parent.id.desc()).offset(offset).limit(limit).all()
    links = parent_links(db, [parent.id for parent, _ in rows])
    return {"parents": [admin_parent_record(parent, account, links[parent.id])
                        for parent, account in rows], "total": total}


def admin_parent(db, parent_id, lock=False):
    query = db.query(Parent).filter(Parent.id == parent_id)
    if lock:
        query = query.populate_existing().with_for_update()
    row = query.one_or_none()
    if row is None:
        raise HTTPException(404, "Parent not found")
    return row


@router.put("/admin/parents/{parent_id}", response_model=AdminParentResponse)
def update_parent(parent_id: int, body: ParentUpdate, user=Depends(require_role("admin")),
                  db=Depends(get_session)):
    parent = admin_parent(db, parent_id, lock=True)
    parent.full_name, parent.email, parent.mobile = (body.full_name,
        str(body.email).lower() if body.email else None, body.mobile or None)
    parent.user.display_name = body.full_name
    parent.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email is already in use") from None
    return admin_parent_record(parent, parent.user, parent_links(db, [parent.id])[parent.id])


@router.patch("/admin/parents/{parent_id}/active")
def set_parent_active(parent_id: int, body: ActiveUpdate,
                      user=Depends(require_role("admin")), db=Depends(get_session)):
    parent = admin_parent(db, parent_id, lock=True)
    parent.active, parent.updated_at = body.active, utcnow()
    db.commit()
    return {"id": parent.id, "active": parent.active}


@router.post("/admin/parents/{parent_id}/students", status_code=201)
def add_parent_student(parent_id: int, body: StudentLinkInput,
                       user=Depends(require_role("admin")), db=Depends(get_session)):
    parent = admin_parent(db, parent_id, lock=True)
    ensure_students(db, [body])
    existing = (db.query(ParentStudent).filter_by(parent_id=parent.id,
                student_usn=body.student_usn, active=True).one_or_none())
    if existing:
        raise HTTPException(409, "This student is already linked to the parent")
    old = (db.query(ParentStudent).filter_by(parent_id=parent.id,
           student_usn=body.student_usn, active=False).order_by(ParentStudent.id.desc()).first())
    mapping = old or ParentStudent(parent_id=parent.id, student_usn=body.student_usn)
    for name, value in body.model_dump().items():
        setattr(mapping, name, value)
    mapping.active, mapping.updated_at = True, utcnow()
    db.add(mapping)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This student is already linked to the parent") from None
    return child_record(mapping.student, mapping)


@router.patch("/admin/parents/{parent_id}/students/{mapping_id}/active")
def set_mapping_active(parent_id: int, mapping_id: int, body: ActiveUpdate,
                       user=Depends(require_role("admin")), db=Depends(get_session)):
    admin_parent(db, parent_id, lock=True)
    mapping = (db.query(ParentStudent).filter_by(id=mapping_id, parent_id=parent_id)
               .populate_existing().with_for_update().one_or_none())
    if mapping is None:
        raise HTTPException(404, "Parent-student mapping not found")
    if body.active and db.query(ParentStudent.id).filter(
            ParentStudent.parent_id == parent_id,
            ParentStudent.student_usn == mapping.student_usn,
            ParentStudent.active.is_(True), ParentStudent.id != mapping.id).first():
        raise HTTPException(409, "This student is already linked to the parent")
    mapping.active, mapping.updated_at = body.active, utcnow()
    db.commit()
    return {"id": mapping.id, "active": mapping.active}


@router.get("/parent/profile", response_model=ParentProfileResponse)
def parent_profile(parent=Depends(require_parent), db=Depends(get_session)):
    rows = (db.query(ParentStudent, Student)
            .join(Student, Student.usn == ParentStudent.student_usn)
            .filter(ParentStudent.parent_id == parent.id, ParentStudent.active.is_(True))
            .order_by(ParentStudent.is_primary.desc(), Student.name, Student.usn).all())
    return {"id": parent.id, "name": parent.full_name, "email": parent.email,
            "mobile": parent.mobile, "children": [child_record(student, mapping)
                                                   for mapping, student in rows]}


def parent_events(db, student, limit=5):
    roles = (CampusEvent.audience == "ALL") | CampusEvent.audience.like("%PARENT%")
    rows = (db.query(CampusEvent).filter(
        CampusEvent.status == "PUBLISHED", CampusEvent.event_date >= india_today(), roles,
        or_(CampusEvent.department_code.is_(None), CampusEvent.department_code == student.dept_code))
        .order_by(CampusEvent.event_date, CampusEvent.start_time, CampusEvent.id).limit(limit).all())
    return [event_record(row) for row in rows if "ALL" in audience_roles(row.audience)
            or "PARENT" in audience_roles(row.audience)]


def safe_timetable(data):
    fields = ("subject_code", "subject_name", "room", "section", "day",
              "day_name", "start_time", "end_time", "starts_at", "ends_at", "date")
    def safe_entry(entry):
        return {field: entry.get(field) for field in fields if field in entry}
    return {"timezone": data["timezone"], "date": data["date"],
            "published": data["published"], "state": data["state"],
            "message": data["message"],
            "weekly": [safe_entry(item) for item in data["weekly"]],
            "today": [safe_entry(item) for item in data["today"]],
            "current": safe_entry(data["current"]) if data["current"] else None,
            "next": safe_entry(data["next"]) if data["next"] else None,
            "next_message": data["next_message"]}


@router.get("/parent/children/{usn}/dashboard", response_model=ParentDashboardResponse)
def parent_dashboard(usn: str, parent=Depends(require_parent), db=Depends(get_session)):
    student = child_for_parent(db, parent, usn)
    agents = get_agents()
    attendance_rows = db.query(AttendanceSummary).filter_by(usn=student.usn).all()
    marks = agents["academic_agent"].student_marks(db, student.usn)
    averages = [item["cie_average"] for item in marks if item.get("cie_average") is not None]
    ticket = db.query(HallTicket).filter_by(usn=student.usn, semester=student.semester).first()
    fees = agents["finance_agent"].student_fees(db, student.usn)
    placement = [{field: item.get(field) for field in
                  ("company", "role", "package_lpa", "date", "departments",
                   "eligible", "reasons", "status")}
                 for item in agents["placement_agent"].student_view(db, student.usn)]
    scholarship = scholarships.student_summary(db, student)
    exam_rows = agents["eligibility_agent"].schedule_for(db, student.dept_code, student.semester)
    today = str(dt.date.today())
    next_exams = [item for item in exam_rows if item["date"] >= today][:5]
    timetable = timetable_reads.view(db, dept=student.dept_code, year=student.year,
                                     semester=student.semester, section=student.section)
    from .library.service import summary as library_summary
    return {
        "library": library_summary(db, student.usn, parent=True),
        "child": child_record(student),
        "attendance": {"overall": overall_percentage(db, student.usn),
            "warning": any(row.shortage for row in attendance_rows),
            "subjects": [{"subject": row.subject_code, "held": row.classes_held,
                           "attended": row.classes_attended, "percentage": row.percentage,
                           "shortage": bool(row.shortage)} for row in attendance_rows]},
        "marks": {"subjects": marks,
                  "average": round(sum(averages) / len(averages), 1) if averages else None},
        "fees": fees,
        "exams": {"eligible": ticket.eligible if ticket else None,
                  "hall_ticket_status": "available" if ticket and ticket.eligible else
                                        "blocked" if ticket else "unavailable",
                  "reasons": ticket.reasons if ticket else None, "next": next_exams},
        "timetable": safe_timetable(timetable),
        "scholarship": scholarship,
        "placements": placement,
        "events": parent_events(db, student),
    }


@router.get("/parent/children/{usn}/timetable")
def parent_timetable(usn: str, parent=Depends(require_parent), db=Depends(get_session)):
    student = child_for_parent(db, parent, usn)
    return safe_timetable(timetable_reads.view(
        db, dept=student.dept_code, year=student.year,
        semester=student.semester, section=student.section))
