"""Eligibility Agent — hall-ticket (exam) eligibility and scholarship
scoring, merged under P2 (docs/RESEARCH_PLAN_V3.md §7).

Exam and Scholarship were split in v2 but own the same two upstream
triggers (attendance.updated, fees.updated) and the same shape of policy
(deterministic rule checks against attendance/fees, with a reason-coded
verdict). Under the pre-registered agent criterion — owns state/policy
that outlives a request, and acts on events without direct invocation —
they are one agent, not two. Merging the agent does not merge the tools:
`get_hall_ticket` and `get_scholarship` stay two distinct tools (§7.1),
so the dev benchmark's gold labels are untouched.
"""
import joblib

from .. import config
from ..models import ExamSchedule, HallTicket, ScholarshipAssessment, Student
from .attendance import overall_percentage
from .base import BaseAgent
from .finance import fees_cleared

SCHEME = "Merit-cum-Means"
_MODEL_PATH = config.ML_MODELS_DIR / "scholarship_cart.joblib"


class EligibilityAgent(BaseAgent):
    name = "eligibility_agent"
    description = ("Hall-ticket eligibility and scholarship scoring "
                   "(rules + CART), with reason codes")

    def __init__(self, bus):
        super().__init__(bus)
        self.model = None
        if _MODEL_PATH.exists():
            # Safe: artifact produced locally by ml/train.py in this repo.
            self.model = joblib.load(_MODEL_PATH)

    def register_subscriptions(self):
        self.bus.subscribe("attendance.updated", self.name, self.on_upstream_change)
        self.bus.subscribe("fees.updated", self.name, self.on_upstream_change)

    async def on_upstream_change(self, payload: dict):
        usns = [u["usn"] if isinstance(u, dict) else u
                for u in payload.get("updates", payload.get("usns", []))]
        db = self.session()
        try:
            hall_ticket_results = [self.evaluate_hall_ticket(db, usn) for usn in usns]
            scholarship_results = [self.evaluate_scholarship(db, usn) for usn in usns]
            db.commit()
        finally:
            db.close()
        await self.publish("exam.updated", {
            "workflow_id": payload["workflow_id"],
            "_hop": payload.get("_hop", 1), "results": hall_ticket_results})
        await self.publish("scholarship.updated", {
            "workflow_id": payload["workflow_id"],
            "_hop": payload.get("_hop", 1), "results": scholarship_results})

    # ---------- hall-ticket eligibility -----------------------------------
    def hall_ticket_status(self, db, usn: str) -> dict:
        """Calculate hall-ticket eligibility without changing stored state."""
        student = db.get(Student, usn)
        if student is None:
            return {"usn": usn, "eligible": False, "reasons": ["unknown student"]}
        attendance = overall_percentage(db, usn)
        cleared = fees_cleared(db, usn)
        reasons = []
        if attendance < config.ATTENDANCE_THRESHOLD:
            reasons.append(f"attendance {attendance}% below {config.ATTENDANCE_THRESHOLD}%")
        if not cleared:
            reasons.append("outstanding fees pending")
        eligible = not reasons
        if eligible:
            reasons.append(f"attendance {attendance}% ok; fees cleared")
        return {"usn": usn, "eligible": eligible, "reasons": reasons}

    def evaluate_hall_ticket(self, db, usn: str) -> dict:
        """Calculate and persist eligibility for explicit workflow updates."""
        result = self.hall_ticket_status(db, usn)
        student = db.get(Student, usn)
        if student is None:
            return result
        ticket = db.query(HallTicket).filter_by(usn=usn,
                                                semester=student.semester).first()
        if ticket is None:
            ticket = HallTicket(usn=usn, semester=student.semester,
                                eligible=result["eligible"])
            db.add(ticket)
        ticket.eligible = result["eligible"]
        ticket.reasons = "; ".join(result["reasons"])
        return result

    def schedule_for(self, db, dept_code: str, semester: int) -> list[dict]:
        rows = (db.query(ExamSchedule)
                  .filter_by(dept_code=dept_code, semester=semester)
                  .order_by(ExamSchedule.exam_date).all())
        return [{"subject": e.subject_code, "date": str(e.exam_date),
                 "session": e.session} for e in rows]

    # ---------- scholarship scoring -----------------------------------------
    def evaluate_scholarship(self, db, usn: str) -> dict:
        student = db.get(Student, usn)
        if student is None:
            return {"usn": usn, "status": "not_eligible", "reasons": ["unknown student"]}
        attendance = overall_percentage(db, usn)
        cleared = fees_cleared(db, usn)
        reasons = []
        if attendance < config.ATTENDANCE_THRESHOLD:
            reasons.append(f"attendance {attendance}% below 75%")
        if not cleared:
            reasons.append("outstanding fees pending")
        if 0 < student.cgpa < 6.0:
            reasons.append(f"CGPA {student.cgpa} below 6.0 minimum")
        ml_score = None
        if reasons:
            status = "not_eligible"
        elif self.model is not None:
            features = [[student.cgpa, attendance, student.family_income,
                         student.backlogs, 1 if cleared else 0]]
            ml_score = float(self.model.predict_proba(features)[0][1])
            if ml_score >= 0.60:
                status = "eligible"
                reasons.append(f"CART score {ml_score:.2f} >= 0.60")
            elif ml_score >= 0.40:
                status = "waitlist"
                reasons.append(f"CART score {ml_score:.2f} in waitlist band")
            else:
                status = "not_eligible"
                reasons.append(f"CART score {ml_score:.2f} < 0.40")
        else:
            status = "eligible" if student.cgpa >= 7.5 else "waitlist"
            reasons.append("rules-only evaluation (model unavailable)")
        assessment = db.query(ScholarshipAssessment).filter_by(
            usn=usn, scheme=SCHEME).first()
        if assessment is None:
            assessment = ScholarshipAssessment(usn=usn, scheme=SCHEME, status=status)
            db.add(assessment)
        assessment.status = status
        assessment.ml_score = ml_score
        assessment.reasons = "; ".join(reasons)
        return {"usn": usn, "status": status, "ml_score": ml_score,
                "reasons": reasons}
