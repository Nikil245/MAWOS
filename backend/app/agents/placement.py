"""Placement event adapter; domain rules live in placement.service."""
from ..placement import scoring
from ..placement.service import PlacementService, normalize_usn
from .base import BaseAgent


class PlacementAgent(BaseAgent):
    name = 'placement_agent'
    description = 'Final-year recruitment drives, eligibility and placement outcomes'

    def __init__(self, bus):
        super().__init__(bus)
        scoring.threshold()
        self.model, self.model_version = scoring.load_model()

    @property
    def service(self):
        return PlacementService(self.model, self.model_version)

    def register_subscriptions(self):
        self.bus.subscribe('attendance.updated', self.name, self.on_upstream_change)
        self.bus.subscribe('fees.updated', self.name, self.on_upstream_change)

    async def on_upstream_change(self, payload):
        updates = payload.get('updates', payload.get('usns', []))
        usns = sorted({normalize_usn(item['usn'] if isinstance(item, dict) else item) for item in updates})
        db = self.session()
        evaluated, changed = [], 0
        try:
            # Acquire drive locks in a stable order for multi-student batches.
            for drive in self.service.active_drives(db):
                for usn in usns:
                    count = self.service.reevaluate_student(db, usn, drives=[drive])
                    changed += count
                    if count and usn not in evaluated:
                        evaluated.append(usn)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        await self.publish('placement.updated', dict(workflow_id=payload.get('workflow_id'),
                           _hop=payload.get('_hop', 1), usns=evaluated, entries_updated=changed))

    def _upcoming_drives(self, db):
        return self.service.active_drives(db)

    def evaluate_student(self, db, usn, drives=None, attendance=None):
        return self.service.reevaluate_student(db, usn, drives, attendance)

    def student_view(self, db, usn):
        # Preserve dashboard / assistant field names, with stored-or-preview reads.
        from ..models import Student
        if db.get(Student, normalize_usn(usn)) is None:
            return []
        result = []
        for drive in self._upcoming_drives(db)[:15]:
            entry = self.service.eligibility(db, drive.id, usn)
            result.append(dict(company=drive.company, role=drive.role, package_lpa=drive.package_lpa,
                               date=str(drive.drive_date), departments=drive.departments,
                               application_deadline=(str(drive.application_deadline)
                                                     if drive.application_deadline else None),
                               application_url=drive.application_url,
                               eligible=bool(entry['eligible']), probability=entry['ml_probability'],
                               reasons=entry['reasons'], status=entry['status']))
        return result

    def stats(self, db):
        return self.service.stats(db)
