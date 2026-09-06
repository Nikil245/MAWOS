"""Notification Agent — event-driven, context-aware alerts for every cascade
topic, including the proactive scans and admissions events."""
from ..models import Notification
from .base import BaseAgent


class NotificationAgent(BaseAgent):
    name = "notification_agent"
    description = "Context-aware alerts triggered by agent events and scans"

    def register_subscriptions(self):
        self.bus.subscribe("attendance.updated", self.name, self.on_attendance_updated)
        self.bus.subscribe("attendance.scan", self.name, self.on_attendance_scan)
        self.bus.subscribe("scholarship.updated", self.name, self.on_scholarship_updated)
        self.bus.subscribe("admission.enrolled", self.name, self.on_admission_enrolled)
        self.bus.subscribe("timetable.generated", self.name, self.on_timetable_generated)

    def _notify(self, db, title, message, usn=None, role=None, dept=None):
        db.add(Notification(usn=usn, audience_role=role, dept_code=dept,
                            title=title, message=message,
                            source_agent=self.name))

    async def on_attendance_updated(self, payload: dict):
        db = self.session()
        try:
            sent = 0
            for upd in payload.get("updates", []):
                if upd.get("shortage"):
                    self._notify(db, "Attendance shortage alert",
                                 f"Your overall attendance is "
                                 f"{upd['overall_percentage']}%, below the 75% "
                                 f"requirement. Your hall ticket is at risk — "
                                 f"meet your class advisor.", usn=upd["usn"])
                    sent += 1
                if upd.get("absence_streak"):
                    self._notify(db, "Consecutive absence alert",
                                 "You have been absent 3+ consecutive class "
                                 "days. Your mentor has been informed.",
                                 usn=upd["usn"])
                    sent += 1
            db.commit()
        finally:
            db.close()
        await self.publish("notification.sent", {
            "workflow_id": payload["workflow_id"],
            "_hop": payload.get("_hop", 1),
            "count": sent, "trigger": "attendance.updated"})

    async def on_attendance_scan(self, payload: dict):
        db = self.session()
        try:
            self._notify(db, "Daily attendance scan",
                         f"Proactive scan: {payload.get('count', 0)} students "
                         f"currently below the 75% attendance threshold.",
                         role="hod")
            db.commit()
        finally:
            db.close()

    async def on_scholarship_updated(self, payload: dict):
        db = self.session()
        try:
            sent = 0
            for res in payload.get("results", []):
                if res.get("status") == "eligible":
                    self._notify(db, "Scholarship eligibility update",
                                 "You are currently ELIGIBLE for the "
                                 "Merit-cum-Means scholarship. Submit documents "
                                 "to the scholarship cell.", usn=res["usn"])
                    sent += 1
            db.commit()
        finally:
            db.close()
        await self.publish("notification.sent", {
            "workflow_id": payload["workflow_id"],
            "_hop": payload.get("_hop", 1),
            "count": sent, "trigger": "scholarship.updated"})

    async def on_admission_enrolled(self, payload: dict):
        db = self.session()
        try:
            self._notify(db, "Welcome to MITE",
                         f"Admission confirmed. Your USN is {payload['usn']} "
                         f"({payload['dept']}). First-term fee is due within "
                         f"21 days.", usn=payload["usn"])
            self._notify(db, "New enrollment",
                         f"{payload['name']} enrolled in {payload['dept']} "
                         f"as {payload['usn']}.", role="admin")
            db.commit()
        finally:
            db.close()

    async def on_timetable_generated(self, payload: dict):
        db = self.session()
        try:
            self._notify(db, "Timetable published",
                         f"Timetable regenerated for {payload['scope']} "
                         f"({payload['sections']} sections, "
                         f"{payload['placement_rate']}% slots placed, "
                         f"solved in {payload['solve_ms']} ms).",
                         role="faculty",
                         dept=None if payload["scope"] == "ALL" else payload["scope"])
            db.commit()
        finally:
            db.close()

    def for_user(self, db, usn=None, role=None, dept=None, limit=15) -> list[dict]:
        q = db.query(Notification).order_by(Notification.created_at.desc())
        conds = []
        from sqlalchemy import and_, or_
        if usn:
            conds.append(Notification.usn == usn)
        if role:
            conds.append(and_(Notification.audience_role == role,
                              or_(Notification.dept_code.is_(None),
                                  Notification.dept_code == dept)))
        if conds:
            q = q.filter(or_(*conds))
        return [{"id": n.id, "title": n.title, "message": n.message,
                 "source_agent": n.source_agent, "at": n.created_at,
                 "read": n.read}
                for n in q.limit(limit).all()]
