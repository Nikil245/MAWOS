"""Deterministic scholarship workflow; no LLM participates in decisions."""
import datetime as dt
import json
import uuid
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import func

from .agents.attendance import overall_percentage
from .agents.finance import fees_cleared
from .models import (AttendanceSummary, Notification, Scholarship,
                     ScholarshipApplication, ScholarshipAssessment, Student,
                     WorkflowEvent, utcnow)

STATUSES = {"DRAFT", "PENDING_APPROVAL", "CHANGES_REQUESTED", "REJECTED", "PUBLISHED", "CLOSED"}
RESULTS = {"ELIGIBLE", "NOT_ELIGIBLE", "UNABLE_TO_DETERMINE", "CLOSED", "ALREADY_APPLIED"}
TRANSITIONS = {
    "DRAFT": {"PENDING_APPROVAL"}, "PENDING_APPROVAL": {"CHANGES_REQUESTED", "REJECTED", "PUBLISHED", "DRAFT"},
    "CHANGES_REQUESTED": {"DRAFT"}, "PUBLISHED": {"CLOSED"}, "REJECTED": set(), "CLOSED": set(),
}
SAFE_REASONS = {
    "DEPARTMENT": "Your department is not included.", "SEMESTER": "Your semester is not included.",
    "CGPA": "Your CGPA does not meet the minimum.", "ATTENDANCE": "Your attendance does not meet the minimum.",
    "BACKLOGS": "Your active backlogs exceed the allowed limit.", "FEES": "Fee clearance is required.",
    "INCOME": "Verified income is above the limit.", "CATEGORY": "Your category is not included.",
    "MISSING_ATTENDANCE": "Attendance information is unavailable.", "MISSING_INCOME": "Verified income information is unavailable.",
    "MISSING_CATEGORY": "Category information is unavailable.", "CLOSED": "This scholarship is closed.",
}


def _criteria(row):
    try: return json.loads(row.criteria or "{}")
    except json.JSONDecodeError: return {}

def _dump(value): return json.dumps(value, separators=(",", ":"), sort_keys=True)


def assessment_scheme(scholarship) -> str:
    """A stable, bounded key for workflow assessments.

    ``scholarship_assessments.scheme`` predates the workflow and is limited to
    64 characters.  Scholarship titles are user supplied and may be 160
    characters, so they must not be used as that storage key.
    """
    return f"scholarship:{scholarship.id}:v{scholarship.criteria_version}"


def _legacy_assessment_status(status: str) -> str:
    # The legacy ``status`` column is varchar(16); the workflow-specific
    # ``eligibility_status`` column holds the complete API lifecycle value.
    return "UNDETERMINED" if status == "UNABLE_TO_DETERMINE" else status
def effective_status(row, now=None):
    now = now or utcnow()
    return "CLOSED" if row.status == "PUBLISHED" and row.closes_at <= now else row.status
def _event(db, action, scholarship, actor):
    db.add(WorkflowEvent(workflow_id=str(uuid.uuid4()), topic=f"scholarship.{action.lower()}",
                         agent="scholarship_service", payload=_dump({"scholarship_id": scholarship.id, "actor": actor}), hop=0))
def _notify(db, title, message, *, usn=None, role=None, dept=None):
    db.add(Notification(usn=usn, audience_role=role, dept_code=dept, title=title,
                        message=message, source_agent="scholarship_service"))
def _require(condition, detail="Not authorized"):
    if not condition: raise HTTPException(status_code=403, detail=detail)
def _not_found(row):
    if row is None: raise HTTPException(status_code=404, detail="Scholarship not found")
    return row
def _transition(row, target):
    if target not in TRANSITIONS.get(row.status, set()):
        raise HTTPException(status_code=409, detail=f"Invalid scholarship transition: {row.status} to {target}")
    row.status = target


def validate_payload(data):
    if data["opens_at"] >= data["closes_at"]: raise HTTPException(422, "Opening date must be before closing date")
    if urlparse(data["application_url"]).scheme not in {"http", "https"}: raise HTTPException(422, "Application URL must use HTTP or HTTPS")
    criteria = data["criteria"]
    allowed = {"minimum_cgpa", "minimum_attendance", "maximum_backlogs", "fee_clearance_required", "income_limit", "allowed_departments", "allowed_semesters", "allowed_categories", "required_documents"}
    if set(criteria) - allowed: raise HTTPException(422, "Unsupported eligibility criterion")
    for field, low, high in (("minimum_cgpa", 0, 10), ("minimum_attendance", 0, 100)):
        if criteria.get(field) is not None and not low <= criteria[field] <= high: raise HTTPException(422, f"{field} is out of range")
    if criteria.get("maximum_backlogs") is not None and criteria["maximum_backlogs"] < 0: raise HTTPException(422, "maximum_backlogs must be non-negative")
    if criteria.get("income_limit") is not None and criteria["income_limit"] < 0: raise HTTPException(422, "income_limit must be non-negative")
    if any(not isinstance(x, int) or x < 1 or x > 8 for x in criteria.get("allowed_semesters") or []): raise HTTPException(422, "Invalid allowed semester")


def serialize(row, assessment=None, application=None):
    result = effective_status(row)
    output = {"id": row.id, "name": row.name, "provider": row.provider, "description": row.description, "amount": row.amount,
              "application_url": row.application_url, "opens_at": row.opens_at, "closes_at": row.closes_at, "status": result,
              "department_code": row.department_code, "official_document_reference": row.official_document_reference,
              "criteria_version": row.criteria_version, "criteria": _criteria(row), "published_at": row.published_at,
              "created_at": row.created_at, "updated_at": row.updated_at, "approval_comment": row.approval_comment,
              "rejection_reason": row.rejection_reason, "created_by_faculty_id": row.created_by_faculty_id}
    if assessment:
        output.update({"eligibility_status": assessment.eligibility_status, "reason_codes": json.loads(assessment.reason_codes or "[]"), "evaluated_at": assessment.assessed_at})
    if application:
        output["application"] = {"status": application.application_status, "applied_at": application.applied_at, "external_reference": application.external_reference}
    return output


def evaluate(db, scholarship, student, *, publishing=False):
    """Calculate and persist a result for a published scholarship only."""
    if effective_status(scholarship) == "CLOSED": status, codes = "CLOSED", ["CLOSED"]
    elif (not publishing and scholarship.status != "PUBLISHED") or not (scholarship.opens_at <= utcnow() < scholarship.closes_at): return None
    else:
        c, codes, missing = _criteria(scholarship), [], []
        if c.get("allowed_departments") and student.dept_code not in c["allowed_departments"]: codes.append("DEPARTMENT")
        if c.get("allowed_semesters") and student.semester not in c["allowed_semesters"]: codes.append("SEMESTER")
        if c.get("minimum_cgpa") is not None and round(student.cgpa, 2) < c["minimum_cgpa"]: codes.append("CGPA")
        if c.get("maximum_backlogs") is not None and student.backlogs > c["maximum_backlogs"]: codes.append("BACKLOGS")
        if c.get("minimum_attendance") is not None:
            if not db.query(AttendanceSummary).filter_by(usn=student.usn).first(): missing.append("MISSING_ATTENDANCE")
            elif round(overall_percentage(db, student.usn), 2) < c["minimum_attendance"]: codes.append("ATTENDANCE")
        if c.get("fee_clearance_required") and not fees_cleared(db, student.usn): codes.append("FEES")
        if c.get("income_limit") is not None:
            if student.family_income is None: missing.append("MISSING_INCOME")
            elif student.family_income > c["income_limit"]: codes.append("INCOME")
        if c.get("allowed_categories"):
            if not student.category: missing.append("MISSING_CATEGORY")
            elif student.category not in c["allowed_categories"]: codes.append("CATEGORY")
        status, codes = ("UNABLE_TO_DETERMINE", missing) if missing else ("NOT_ELIGIBLE", codes) if codes else ("ELIGIBLE", [])
    assessment = db.query(ScholarshipAssessment).filter_by(scholarship_id=scholarship.id, usn=student.usn, criteria_version=scholarship.criteria_version).first()
    if assessment is None:
        assessment = ScholarshipAssessment(scholarship_id=scholarship.id, usn=student.usn,
                                           scheme=assessment_scheme(scholarship),
                                           status=_legacy_assessment_status(status),
                                           criteria_version=scholarship.criteria_version)
        db.add(assessment)
    assessment.status = _legacy_assessment_status(status); assessment.eligibility_status = status; assessment.reason_codes = _dump(codes); assessment.reasons = "; ".join(SAFE_REASONS[x] for x in codes); assessment.assessed_at = utcnow()
    return assessment


def evaluate_applicable(db, scholarship, *, publishing=False):
    assessments = [evaluate(db, scholarship, s, publishing=publishing) for s in db.query(Student).filter_by(dept_code=scholarship.department_code).all()]
    return [x for x in assessments if x]


def aggregate(db, scholarship):
    rows = db.query(ScholarshipAssessment.eligibility_status, func.count()).filter_by(scholarship_id=scholarship.id, criteria_version=scholarship.criteria_version).group_by(ScholarshipAssessment.eligibility_status).all()
    counts = dict(rows); return {"total_applicable_students": sum(counts.values()), "eligible": counts.get("ELIGIBLE", 0), "not_eligible": counts.get("NOT_ELIGIBLE", 0), "unable_to_determine": counts.get("UNABLE_TO_DETERMINE", 0)}


def student_summary(db, student):
    """Current workflow-only scholarship summary for the student dashboard."""
    rows = db.query(Scholarship).filter_by(department_code=student.dept_code).filter(
        Scholarship.status.in_(["PUBLISHED", "CLOSED"])).order_by(Scholarship.closes_at).all()
    visible = []
    for row in rows:
        status = effective_status(row)
        assessment = db.query(ScholarshipAssessment).filter_by(
            scholarship_id=row.id, usn=student.usn, criteria_version=row.criteria_version).first()
        application = db.query(ScholarshipApplication).filter_by(
            scholarship_id=row.id, student_usn=student.usn).first()
        visible.append({"id": row.id, "name": row.name, "status": status,
                        "closes_at": row.closes_at, "eligibility_status": assessment.eligibility_status if assessment else None,
                        "applied": application is not None})
    available = [item for item in visible if item["status"] == "PUBLISHED"]
    eligible = [item for item in available if item["eligibility_status"] == "ELIGIBLE" and not item["applied"]]
    if eligible:
        return {"state": "ELIGIBLE", "eligible_count": len(eligible), "opportunity": eligible[0], "total_available": len(available)}
    if any(item["applied"] for item in available):
        return {"state": "APPLIED", "eligible_count": 0, "opportunity": next(item for item in available if item["applied"]), "total_available": len(available)}
    if any(item["eligibility_status"] == "UNABLE_TO_DETERMINE" for item in available):
        return {"state": "UNABLE_TO_DETERMINE", "eligible_count": 0, "opportunity": None, "total_available": len(available)}
    if available:
        return {"state": "NOT_ELIGIBLE", "eligible_count": 0, "opportunity": None, "total_available": len(available)}
    return {"state": "CLOSED" if visible else "NONE", "eligible_count": 0, "opportunity": None, "total_available": 0}
