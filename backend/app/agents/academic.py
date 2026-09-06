"""Academic Agent — student records, internal marks (CIE), analytics
aggregations that power the role dashboards."""
from sqlalchemy import case, func

from ..models import (
    AttendanceSummary, Department, Faculty, MarksRecord, Student, Subject,
    TeachingAssignment,
)
from ..marks_policy import INTERNALS, MAX_MARKS
from .attendance import overall_percentage
from .base import BaseAgent


class AcademicAgent(BaseAgent):
    name = "academic_agent"
    description = "Student records, internal marks (CIE), department analytics"

    def student_profile(self, db, usn: str) -> dict | None:
        s = db.get(Student, usn)
        if s is None:
            return None
        dept = db.get(Department, s.dept_code)
        return {"usn": s.usn, "name": s.name, "dept": s.dept_code,
                "dept_name": dept.name if dept else s.dept_code,
                "year": s.year, "semester": s.semester, "section": s.section,
                "cgpa": s.cgpa, "backlogs": s.backlogs, "category": s.category,
                "admission_year": s.admission_year}

    def student_marks(self, db, usn: str, subject_code: str | None = None) -> list[dict]:
        query = (db.query(MarksRecord, Subject)
                   .join(Subject, Subject.code == MarksRecord.subject_code)
                   .filter(MarksRecord.usn == usn))
        if subject_code:
            query = query.filter(MarksRecord.subject_code == subject_code)
        rows = query.order_by(MarksRecord.subject_code, MarksRecord.internal).all()
        by_subject: dict[str, dict] = {}
        for m, sub in rows:
            e = by_subject.setdefault(m.subject_code, {
                "subject": m.subject_code, "name": sub.name, "internals": {},
                # Additive detail for API clients that need to render marks
                # against the model's actual maximum without changing the
                # established internals/CIE-average response shape.
                "assessment_details": {}})
            e["internals"][f"CIE-{m.internal}"] = m.marks
            e["assessment_details"][f"CIE-{m.internal}"] = {
                "marks": m.marks, "max_marks": m.max_marks}
        for e in by_subject.values():
            vals = list(e["internals"].values())
            e["cie_average"] = round(sum(vals) / len(vals), 1) if vals else None
        return list(by_subject.values())

    def enter_marks(self, db, entered_by: str, records: list[dict]) -> dict:
        """Persist a complete, prevalidated sheet in one transaction.

        Validation deliberately finishes before any ORM object is changed so a
        bad row can never create a partial marks submission.
        """
        if not records:
            raise ValueError("at least one student mark is required")
        validated = []
        seen_usns = set()
        for r in records:
            usn = str(r.get("usn", "")).upper().strip()
            subject_code = str(r.get("subject_code", "")).upper().strip()
            if not usn:
                raise ValueError("student USN is required")
            if usn in seen_usns:
                raise ValueError("marks sheet contains a duplicate student")
            seen_usns.add(usn)
            if not subject_code or db.get(Subject, subject_code) is None:
                raise ValueError("marks sheet contains an unknown subject")
            if db.get(Student, usn) is None:
                raise ValueError(f"marks sheet contains an unknown student: {usn}")
            try:
                internal = int(r["internal"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("assessment must be an integer") from exc
            mark = r.get("marks")
            if isinstance(mark, bool) or not isinstance(mark, (int, float)):
                raise ValueError("mark must be a numeric value")
            if internal not in INTERNALS or not 0 <= mark <= MAX_MARKS:
                raise ValueError(f"mark must be between 0 and {MAX_MARKS:g}")
            validated.append((usn, subject_code, internal, float(mark)))

        try:
            for usn, subject_code, internal, mark in validated:
                existing = db.query(MarksRecord).filter_by(
                    usn=usn, subject_code=subject_code, internal=internal).first()
                if existing:
                    existing.marks = mark
                    existing.max_marks = MAX_MARKS
                    existing.entered_by = entered_by
                else:
                    db.add(MarksRecord(usn=usn, subject_code=subject_code,
                                       internal=internal, marks=mark,
                                       max_marks=MAX_MARKS,
                                       entered_by=entered_by))
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"accepted": len(validated), "rejected": []}

    def class_roster(self, db, dept: str, year: int, section: str) -> list[dict]:
        rows = (db.query(Student).filter_by(dept_code=dept, year=year,
                                            section=section)
                  .order_by(Student.usn).all())
        return [{"usn": s.usn, "name": s.name, "cgpa": s.cgpa,
                 "attendance": overall_percentage(db, s.usn)} for s in rows]

    def dept_analytics(self, db, dept: str) -> dict:
        students = db.query(Student).filter_by(dept_code=dept).count()
        shortage = (db.query(func.count(func.distinct(AttendanceSummary.usn)))
                      .join(Student, Student.usn == AttendanceSummary.usn)
                      .filter(Student.dept_code == dept,
                              AttendanceSummary.shortage.is_(True)).scalar() or 0)
        avg_att = (db.query(func.avg(AttendanceSummary.percentage))
                     .join(Student, Student.usn == AttendanceSummary.usn)
                     .filter(Student.dept_code == dept).scalar() or 0)
        avg_cgpa = (db.query(func.avg(Student.cgpa))
                      .filter(Student.dept_code == dept,
                              Student.cgpa > 0).scalar() or 0)
        by_year = dict(db.query(Student.year, func.count())
                         .filter(Student.dept_code == dept)
                         .group_by(Student.year).all())
        return {"dept": dept, "students": students,
                "shortage_students": int(shortage),
                "avg_attendance": round(float(avg_att), 1),
                "avg_cgpa": round(float(avg_cgpa), 2),
                "by_year": {int(k): v for k, v in by_year.items()}}

    def department_summary(self, db, dept: str) -> dict:
        """Dashboard-consistent counts of all currently stored department rows."""
        department = db.get(Department, dept)
        return {
            "department_code": dept,
            "department_name": department.name if department else dept,
            "student_count": int(db.query(func.count(func.distinct(Student.usn)))
                                 .filter(Student.dept_code == dept).scalar() or 0),
            "faculty_count": int(db.query(func.count(func.distinct(Faculty.id)))
                                 .filter(Faculty.dept_code == dept).scalar() or 0),
        }

    def institution_analytics(self, db) -> dict:
        return {d.code: self.dept_analytics(db, d.code)
                for d in db.query(Department).all()}

    def faculty_assignments(self, db, faculty_id: int) -> list[dict]:
        rows = (db.query(TeachingAssignment)
                  .filter_by(faculty_id=faculty_id).all())
        return [{"subject": a.subject_code, "subject_name": a.subject.name,
                 "dept": a.dept_code, "year": a.year, "section": a.section,
                 "credits": a.subject.credits} for a in rows]
