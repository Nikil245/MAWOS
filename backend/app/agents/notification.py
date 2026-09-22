"""Notification Agent — event-driven, context-aware alerts for every cascade
topic, including the proactive scans and admissions events."""
import uuid

from ..models import Notification
from ..notifications import notify_role, notify_usns, serialize
from .base import BaseAgent


class NotificationAgent(BaseAgent):
    name = "notification_agent"
    description = "Context-aware alerts triggered by agent events and scans"

    def register_subscriptions(self):
        self.bus.subscribe("attendance.updated", self.name, self.on_attendance_updated)
        self.bus.subscribe("attendance.scan", self.name, self.on_attendance_scan)
        self.bus.subscribe("scholarship.updated", self.name, self.on_scholarship_updated)
        self.bus.subscribe("admission.enrolled", self.name, self.on_admission_enrolled)
        self.bus.subscribe("timetable.proposal_generated", self.name,
                           self.on_timetable_proposal_generated)
        self.bus.subscribe("placement.notification_required", self.name, self.on_placement_shortlisted)

    async def on_placement_shortlisted(self, payload):
        from ..models import PlacementDrive, PlacementShortlist, Student
        from ..placement.service import normalize_usn
        if payload.get('notification_type') != 'PLACEMENT_SHORTLISTED':
            return
        usn, drive_id = normalize_usn(payload.get('usn')), payload.get('drive_id')
        db = self.session()
        try:
            # Serializes duplicate event deliveries for this student on PostgreSQL.
            student = db.query(Student).filter_by(usn=usn).with_for_update().one_or_none()
            drive = db.get(PlacementDrive, drive_id)
            entry = db.query(PlacementShortlist).filter_by(drive_id=drive_id, usn=usn, eligible=True).first()
            if student is None or drive is None or entry is None:
                return
            self._notify(db, f'Placement shortlist: {drive.company}',
                         f'You have been shortlisted for {drive.company} — {drive.role}.',
                         usn=usn, notification_type='PLACEMENT_SHORTLISTED',
                         event_key=f'placement_shortlisted:{drive.id}:{usn}',
                         route=f'/student/placements/{drive.id}',
                         related_entity_type='placement_drive', related_entity_id=drive.id)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _notify(self, db, title, message, usn=None, role=None, dept=None,
                notification_type='GENERAL', event_key=None, route=None,
                related_entity_type=None, related_entity_id=None):
        options = dict(title=title, message=message, notification_type=notification_type,
                       source_agent=self.name,
                       event_key=event_key or f'{notification_type.lower()}:{uuid.uuid4()}',
                       route=route, related_entity_type=related_entity_type,
                       related_entity_id=related_entity_id)
        if usn:
            return notify_usns(db, [usn], **options)
        if role:
            return notify_role(db, role, dept=dept, **options)
        return 0

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

    async def on_timetable_proposal_generated(self, payload: dict):
        db = self.session()
        try:
            self._notify(db, "Timetable proposal ready",
                         f"A proposal was generated for {payload['scope']} "
                         f"({payload['sections']} sections, "
                         f"{payload['placement_rate']}% slots placed, "
                         f"solved in {payload['solve_ms']} ms). Review it in "
                         "Timetable Operations before any version is published.",
                         role="hod",
                         dept=None if payload["scope"] == "ALL" else payload["scope"])
            db.commit()
        finally:
            db.close()

    def for_user(self, db, user_id=None, limit=50, offset=0) -> list[dict]:
        if user_id is None:
            return []
        rows = (db.query(Notification).filter(Notification.recipient_user_id == user_id)
                .order_by(Notification.created_at.desc(), Notification.id.desc())
                .offset(offset).limit(limit).all())
        return [serialize(row) for row in rows]
