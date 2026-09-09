"""Safe, repeatable timetable configuration import from authoritative academic rows.

Only timetable configuration rows are inserted.  Existing rows are never updated,
and academic source tables and timetable versions are never mutated.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import os

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from ..models import Department, Faculty, Student, Subject, TeachingAssignment
from . import models as m
from .room_types import room_kind_satisfies


ALLOW_ENV = "MAWOS_ALLOW_TIMETABLE_BOOTSTRAP"


@dataclass(frozen=True)
class BootstrapOptions:
    weekly_periods: int | None = None
    weekly_period_source: str = "explicit fixed default"
    use_subject_credits: bool = False
    max_per_day: int = 2
    block_length: int = 1
    room_type: str = "classroom"
    faculty_daily_limit: int | None = None
    faculty_weekly_limit: int | None = None
    create_placeholder_rooms: bool = False
    confirm_placeholder_rooms: bool = False

    def validate(self) -> None:
        if self.weekly_periods is not None and not 1 <= self.weekly_periods <= 168:
            raise ValueError("weekly periods must be between 1 and 168")
        if not 1 <= self.max_per_day <= 24 or not 1 <= self.block_length <= 24:
            raise ValueError("daily limit and block length must be between 1 and 24")
        if not self.room_type or len(self.room_type) > 32:
            raise ValueError("room type must contain 1 to 32 characters")
        if (self.faculty_daily_limit is None) != (self.faculty_weekly_limit is None):
            raise ValueError("faculty daily and weekly limits must be configured together")
        if self.faculty_daily_limit is not None and not 1 <= self.faculty_daily_limit <= 24:
            raise ValueError("faculty daily limit must be between 1 and 24")
        if self.faculty_weekly_limit is not None and not 1 <= self.faculty_weekly_limit <= 168:
            raise ValueError("faculty weekly limit must be between 1 and 168")
        if self.create_placeholder_rooms and not self.confirm_placeholder_rooms:
            raise ValueError("placeholder rooms require explicit confirmation")


def writes_allowed() -> bool:
    return os.getenv(ALLOW_ENV, "").strip().lower() == "true"


def _item(**values):
    return values


def _preview_hash(report: dict) -> str:
    payload = {key: value for key, value in report.items() if key not in {"mode", "preview_hash"}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def bootstrap(db: Session, term_id: int, *, departments: list[str] | None = None,
              apply: bool = False, options: BootstrapOptions | None = None) -> dict:
    """Preview or insert derived configuration; callers own commit/rollback."""
    options = options or BootstrapOptions()
    options.validate()
    if apply and not writes_allowed():
        raise ValueError(f"Set {ALLOW_ENV}=true before applying timetable bootstrap changes")
    if apply and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(734091208)"))
    term = db.get(m.Term, term_id)
    if term is None:
        raise ValueError(f"academic term {term_id} was not found")
    if apply and db.query(m.Run).filter(m.Run.term_id == term_id, m.Run.status.in_(("PUBLISHED", "ARCHIVED"))).first():
        raise ValueError("this term has publication history; bootstrap a new term")

    requested = [d.strip().upper() for d in (departments or []) if d.strip()]
    dept_rows = db.query(Department).order_by(Department.code).all()
    by_code = {d.code: d for d in dept_rows}
    unknown = sorted(set(requested) - set(by_code))
    if unknown:
        raise ValueError("unknown department: " + ", ".join(unknown))
    selected = [by_code[d] for d in requested] if requested else dept_rows

    report = {
        "mode": "apply" if apply else "dry-run", "term": {"id": term.id, "name": term.name},
        "departments": [_item(code=d.code, name=d.name) for d in selected],
        "sections": [], "subjects": [], "faculty_assignments": [],
        "qualifications": [], "requirements": [], "faculty_limits": [],
        "missing_subject_mappings": [], "missing_faculty_qualifications": [],
        "missing_rooms": [], "missing_weekly_period_values": [], "defaulted_values": [],
        "warnings": [], "conflicts": [], "placeholders": [], "created": defaultdict(int),
    }
    created = report["created"]
    selected_codes = [d.code for d in selected]
    if not selected_codes:
        report["conflicts"].append(_item(code="missing_departments", message="No departments were found."))
        report["created"] = {}
        report["preview_hash"] = _preview_hash(report)
        return report

    cohorts = (db.query(Student.dept_code, Student.year, Student.semester, Student.section,
                        func.count(Student.usn).label("student_count"))
               .filter(Student.dept_code.in_(selected_codes), Student.status == "enrolled")
               .group_by(Student.dept_code, Student.year, Student.semester, Student.section)
               .order_by(Student.dept_code, Student.year, Student.semester, Student.section).all())
    cohort_map = {(x.dept_code, x.year, x.semester, x.section): x.student_count for x in cohorts}
    cohort_departments = {x.dept_code for x in cohorts}
    for department in selected:
        if department.code not in cohort_departments:
            report["conflicts"].append(_item(code="missing_enrolled_sections", department=department.code,
                message=f"{department.code} has no enrolled student cohorts for section creation."))
    existing_sections = {(x.dept_code, x.year, x.semester, x.name): x for x in
                         db.query(m.Section).filter(m.Section.term_id == term_id,
                         m.Section.dept_code.in_(selected_codes)).all()}
    section_map = {}
    for cohort in cohorts:
        key = (cohort.dept_code, cohort.year, cohort.semester, cohort.section)
        section = existing_sections.get(key)
        state = "already_present" if section else "would_create"
        if section and section.size != cohort.student_count:
            issue = _item(code="section_capacity_mismatch", department=cohort.dept_code,
                message=(f"Existing Year {cohort.year} Semester {cohort.semester} Section {cohort.section} "
                         f"capacity is {section.size}; enrolled student count is {cohort.student_count}. "
                         "The bootstrap will not overwrite it."))
            (report["conflicts"] if section.size < cohort.student_count else report["warnings"]).append(issue)
        if apply and section is None:
            section = m.Section(term_id=term_id, dept_code=cohort.dept_code, year=cohort.year,
                                semester=cohort.semester, name=cohort.section, size=cohort.student_count)
            db.add(section)
            db.flush()
            state, created["sections"] = "created", created["sections"] + 1
        section_map[key] = section
        report["sections"].append(_item(department=cohort.dept_code, year=cohort.year,
            semester=cohort.semester, section=cohort.section, student_count=cohort.student_count,
            capacity=section.size if section else cohort.student_count, state=state))

    subjects = db.query(Subject).filter(Subject.dept_code.in_(selected_codes)).order_by(Subject.dept_code, Subject.semester, Subject.code).all()
    subjects_by_scope = defaultdict(list)
    for subject in subjects:
        subjects_by_scope[(subject.dept_code, (subject.semester + 1)//2, subject.semester)].append(subject)
    assignments = (db.query(TeachingAssignment).filter(TeachingAssignment.dept_code.in_(selected_codes))
                   .order_by(TeachingAssignment.dept_code, TeachingAssignment.year,
                             TeachingAssignment.section, TeachingAssignment.subject_code).all())
    assignments_by_scope = defaultdict(list)
    valid_assignments = []
    for assignment in assignments:
        subject, faculty = db.get(Subject, assignment.subject_code), db.get(Faculty, assignment.faculty_id)
        valid = bool(subject and faculty and subject.dept_code == assignment.dept_code
                     and faculty.dept_code == assignment.dept_code
                     and (subject.semester + 1)//2 == assignment.year)
        semester = subject.semester if subject else None
        if valid:
            assignments_by_scope[(assignment.dept_code, assignment.year, semester, assignment.section)].append(assignment)
            valid_assignments.append(assignment)
        else:
            report["conflicts"].append(_item(code="invalid_teaching_assignment", assignment_id=assignment.id,
                message=f"Teaching assignment {assignment.id} has inconsistent subject, faculty, department, or year references."))
        report["faculty_assignments"].append(_item(id=assignment.id, department=assignment.dept_code,
            year=assignment.year, semester=semester, section=assignment.section,
            subject_code=assignment.subject_code, faculty_id=assignment.faculty_id,
            faculty_name=faculty.name if faculty else None, valid=valid))

    for cohort in cohorts:
        scope = (cohort.dept_code, cohort.year, cohort.semester)
        found = subjects_by_scope.get(scope, [])
        exact_assignments = assignments_by_scope.get((*scope, cohort.section), [])
        report["subjects"].append(_item(department=cohort.dept_code, year=cohort.year,
            semester=cohort.semester, section=cohort.section,
            subjects=[_item(code=s.code, name=s.name, credits=s.credits) for s in found]))
        if not found:
            report["conflicts"].append(_item(code="missing_subjects", department=cohort.dept_code,
                message=f"No subjects exist for {cohort.dept_code} Year {cohort.year} Semester {cohort.semester}."))
        if not exact_assignments:
            report["conflicts"].append(_item(code="missing_assignments", department=cohort.dept_code,
                message=(f"No teaching assignments exist for {cohort.dept_code} Year {cohort.year} "
                         f"Semester {cohort.semester} Section {cohort.section}.")))
        assigned_codes = {a.subject_code for a in exact_assignments}
        for subject in found:
            if subject.code not in assigned_codes:
                mappings = [a for a in valid_assignments if a.subject_code == subject.code]
                locations = [_item(year=a.year, section=a.section) for a in mappings]
                report["missing_subject_mappings"].append(_item(department=cohort.dept_code,
                    year=cohort.year, semester=cohort.semester, section=cohort.section,
                    subject_code=subject.code, subject_name=subject.name, existing_locations=locations))

    existing_quals = {(q.faculty_id, q.subject_code) for q in db.query(m.Qualification).join(
        Faculty, m.Qualification.faculty_id == Faculty.id).filter(Faculty.dept_code.in_(selected_codes)).all()}
    for assignment in valid_assignments:
        key = (assignment.faculty_id, assignment.subject_code)
        if key in existing_quals:
            state = "already_present"
        else:
            report["missing_faculty_qualifications"].append(_item(assignment_id=assignment.id,
                faculty_id=assignment.faculty_id, subject_code=assignment.subject_code))
            state = "would_create"
            if apply:
                db.add(m.Qualification(faculty_id=assignment.faculty_id, subject_code=assignment.subject_code))
                existing_quals.add(key)
                state, created["qualifications"] = "created", created["qualifications"] + 1
        report["qualifications"].append(_item(faculty_id=assignment.faculty_id,
                                             subject_code=assignment.subject_code, state=state))

    assigned_faculty = sorted({a.faculty_id for a in valid_assignments})
    for faculty_id in assigned_faculty:
        limit = db.get(m.FacultyLimit, (faculty_id, term_id))
        state = "already_present" if limit else "missing"
        if limit is None and options.faculty_daily_limit is not None:
            state = "would_create"
            report["defaulted_values"].append(_item(kind="faculty_limits", faculty_id=faculty_id,
                daily_limit=options.faculty_daily_limit, weekly_limit=options.faculty_weekly_limit,
                source="explicit bootstrap configuration"))
            if apply:
                db.add(m.FacultyLimit(faculty_id=faculty_id, term_id=term_id,
                    daily_limit=options.faculty_daily_limit, weekly_limit=options.faculty_weekly_limit))
                state, created["faculty_limits"] = "created", created["faculty_limits"] + 1
        elif limit is None:
            report["conflicts"].append(_item(code="missing_faculty_limits", faculty_id=faculty_id,
                message=f"Faculty {faculty_id} has no daily/weekly limits for term {term_id}."))
        report["faculty_limits"].append(_item(faculty_id=faculty_id, state=state,
            daily_limit=limit.daily_limit if limit else options.faculty_daily_limit,
            weekly_limit=limit.weekly_limit if limit else options.faculty_weekly_limit))

    rooms = db.query(m.Room).filter(m.Room.dept_code.in_(selected_codes)).order_by(m.Room.id).all()
    existing_requirements = {(r.section_id, r.assignment_id): r for r in db.query(m.Requirement).join(
        m.Section, m.Requirement.section_id == m.Section.id).filter(m.Section.term_id == term_id,
        m.Section.dept_code.in_(selected_codes)).all()}
    missing_room_keys = set()
    for scope, scoped_assignments in assignments_by_scope.items():
        dept, year, semester, section_name = scope
        student_count = cohort_map.get(scope)
        if student_count is None:
            report["conflicts"].append(_item(code="assignment_without_enrolled_section",
                message=f"Assignment scope {dept} Year {year} Semester {semester} Section {section_name} has no enrolled cohort."))
            continue
        section = section_map.get(scope)
        for assignment in scoped_assignments:
            existing = existing_requirements.get((section.id, assignment.id)) if section else None
            if existing:
                periods, max_per_day, block_length, room_type = (existing.periods_per_week,
                    existing.max_per_day, existing.block_length, existing.room_type)
                state = "already_present"
            else:
                subject = db.get(Subject, assignment.subject_code)
                max_per_day, block_length, room_type = (options.max_per_day,
                                                         options.block_length,
                                                         options.room_type)
                periods = subject.credits if subject and options.use_subject_credits else None
                source = "subject credits (explicitly enabled)" if periods is not None else options.weekly_period_source
                if periods is None:
                    periods = options.weekly_periods
                if periods is None:
                    report["missing_weekly_period_values"].append(_item(assignment_id=assignment.id,
                        department=dept, year=year, semester=semester, section=section_name,
                        subject_code=assignment.subject_code))
                    report["conflicts"].append(_item(code="missing_weekly_periods", assignment_id=assignment.id,
                        message=f"No weekly periods are configured for assignment {assignment.id} ({assignment.subject_code})."))
                    state = "blocked"
                else:
                    if periods % block_length or block_length > max_per_day:
                        report["conflicts"].append(_item(code="invalid_requirement_defaults", assignment_id=assignment.id,
                            message=f"Configured defaults cannot form valid blocks for assignment {assignment.id}."))
                        state = "blocked"
                    else:
                        state = "would_create"
                        report["defaulted_values"].append(_item(kind="requirement", assignment_id=assignment.id,
                            subject_code=assignment.subject_code, periods_per_week=periods, source=source,
                            max_per_day=max_per_day, block_length=block_length, room_type=room_type))
                        if apply and section:
                            db.add(m.Requirement(section_id=section.id, assignment_id=assignment.id,
                                periods_per_week=periods, max_per_day=max_per_day, block_length=block_length,
                                room_type=room_type, priority=1))
                            state, created["requirements"] = "created", created["requirements"] + 1
            capacity = section.size if section else student_count
            compatible = [r for r in rooms if r.dept_code == dept and r.capacity >= capacity
                          and room_kind_satisfies(room_type, r.kind)]
            if not compatible:
                missing_room_keys.add((dept, room_type, capacity))
            report["requirements"].append(_item(assignment_id=assignment.id,
                department=dept, year=year, semester=semester, section=section_name,
                subject_code=assignment.subject_code, periods_per_week=periods,
                state=state))

    room_needs = {}
    for dept, kind, capacity in missing_room_keys:
        room_needs[dept, kind] = max(capacity, room_needs.get((dept, kind), 0))
    for (dept, kind), capacity in sorted(room_needs.items()):
        placeholder_name = f"PLACEHOLDER-{dept}-{kind}"[:64]
        placeholder = next((r for r in rooms if r.name == placeholder_name), None)
        if options.create_placeholder_rooms:
            state = "already_present" if placeholder and placeholder.capacity >= capacity else "would_create"
            if placeholder and placeholder.capacity < capacity:
                state = "blocked"
                report["conflicts"].append(_item(code="placeholder_capacity_mismatch",
                    message=f"Existing {placeholder_name} capacity is {placeholder.capacity}; {capacity} is required. It will not be overwritten."))
            if apply and placeholder is None:
                placeholder = m.Room(name=placeholder_name, dept_code=dept, kind=kind, capacity=capacity)
                db.add(placeholder)
                db.flush()
                rooms.append(placeholder)
                state, created["placeholder_rooms"] = "created", created["placeholder_rooms"] + 1
            report["placeholders"].append(_item(name=placeholder_name, department=dept,
                kind=kind, capacity=capacity, state=state, placeholder=True))
        else:
            issue = _item(department=dept, room_type=kind, minimum_capacity=capacity,
                          message=f"{dept} has no {kind} room with capacity at least {capacity}.")
            report["missing_rooms"].append(issue)
            report["conflicts"].append(_item(code="missing_room", **issue))

    report["created"] = dict(created)
    if not apply:
        report["preview_hash"] = _preview_hash(report)
    return report


def controlled_bootstrap(db: Session, *args, **kwargs) -> dict:
    """Translate operational validation failures into a controlled API error."""
    try:
        return bootstrap(db, *args, **kwargs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
