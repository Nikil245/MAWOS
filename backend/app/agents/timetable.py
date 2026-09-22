"""Timetable Agent — objective-driven weekly timetable generation.

Hard constraints (never violated):
  * a faculty member cannot be in two sections at the same (day, period),
    checked GLOBALLY across all 40 sections and against any bookings
    outside the scope being regenerated;
  * one section has at most one class per (day, period);
  * each subject receives exactly `credits` periods per week — placement
    rate is 1.000 or the solve is rejected.

Soft objective (minimised): idle gaps inside a day, days that do not
start at first period, uneven daily load, a subject taught twice in one
day, and teacher idle gaps. Weights and definitions live in
`evaluation/benchmark/schedule_metrics`, frozen at P0.

Algorithm: compact greedy seed + simulated annealing (`app/scheduler.py`).

**This replaces the v2 randomised-greedy solver**, which enforced the hard
constraints and nothing else — it had no objective function, so a
timetable with holes at 09:00 and 12:15 scored exactly as well as a
compact one. Measured over 10 seeds on the shipped institution, v2 left
80.8 of 200 section-days starting after first period and 223 idle gaps
inside days. The before/after is `evaluation/scheduler_eval.py` and
`results/v3_scheduler/e4.md`; the v2 solver is preserved verbatim and
still runnable at `evaluation/baselines/scheduler_v2.py`.

Scheduling is **not** claimed as a research contribution — simulated
annealing for timetabling is decades old and the plan (§4.4) retracted
it. What is claimed is a measured before/after on a real defect.
"""
import time

from .. import scheduler, scheduler_live
from ..models import Department, TeachingAssignment, TimetableSlot
from .base import BaseAgent

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
PERIODS = ["9:00", "10:00", "11:15", "12:15", "14:00", "15:00"]
N_DAYS, N_PERIODS = 5, 6

#: Annealing budget. 120k steps lands ~4 objective points above the
#: instance floor and costs ~1.8 s; see the convergence trace in
#: results/v3_scheduler/e4.json. Below ~60k the schedule is visibly worse.
SOLVER_ITERS = 120_000


class TimetableAgent(BaseAgent):
    name = "timetable_agent"
    description = ("Proposal-only legacy timetable solver. Production writes "
                   "use the versioned timetable operations service.")

    def generate(self, db, dept_code: str | None = None,
                 max_restarts: int = 3, seed: int = 7,
                 iters: int = SOLVER_ITERS) -> dict:
        """Build a legacy proposal without writing timetable rows.

        This solver remains available for benchmark compatibility only.  The
        production workflow persists drafts through ``app.timetable`` after
        an authorized preview/confirmation cycle.
        """
        q = db.query(TeachingAssignment)
        if dept_code:
            q = q.filter(TeachingAssignment.dept_code == dept_code)
        assignments = q.all()
        if not assignments:
            return {"ok": False, "error": "no teaching assignments found"}

        # Bookings by faculty OUTSIDE the scope being regenerated must stay
        # respected, or a scoped regeneration double-books a shared teacher.
        blocked: dict[tuple[int, int], int] = {}
        if dept_code:
            outside = (db.query(TimetableSlot)
                         .filter(TimetableSlot.dept_code != dept_code).all())
            for s in outside:
                key = (s.faculty_id, s.day)
                blocked[key] = blocked.get(key, 0) | (1 << s.period)

        demands = []
        for a in assignments:
            for _ in range(a.subject.credits):
                demands.append(((a.dept_code, a.year, a.section),
                                a.subject_code, a.faculty_id))

        t0 = time.perf_counter()
        try:
            sched, info = scheduler.solve(demands, seed=seed, iters=iters,
                                          restarts=max_restarts,
                                          blocked=blocked)
        except scheduler.Unplaceable as exc:
            return {"ok": False, "error": f"no feasible timetable: {exc}"}
        placed = sched.slots()

        total_needed = len(demands)
        return {
            "ok": True, "proposal_only": True, "scope": dept_code or "ALL",
            "sections": len(sched.sections), "slots_placed": len(placed),
            "slots_required": total_needed,
            "unplaced": total_needed - len(placed),
            "placement_rate": round(100 * len(placed) / total_needed, 2),
            "teacher_conflicts": 0,  # guaranteed by construction
            "solver": "greedy+annealing",
            "objective": round(info["final_cost"], 1),
            "objective_at_seed": round(info["seed_cost"], 1),
            "annealing_steps": info["iterations"],
            "solve_ms": round((time.perf_counter() - t0) * 1000, 1),
            "proposed_slots": placed,
        }

    def generate_live(self, db, dept_code: str | None = None,
                      max_restarts: int = 3, seed: int = 7,
                      iters: int = SOLVER_ITERS) -> dict:
        """Same proposal as `generate`, plus a replayable event trace.

        Visualisation-only: uses `scheduler_live.solve_with_trace` instead
        of `scheduler.solve`.  It never persists timetable rows.
        """
        q = db.query(TeachingAssignment)
        if dept_code:
            q = q.filter(TeachingAssignment.dept_code == dept_code)
        assignments = q.all()
        if not assignments:
            return {"ok": False, "error": "no teaching assignments found"}

        blocked: dict[tuple[int, int], int] = {}
        if dept_code:
            outside = (db.query(TimetableSlot)
                         .filter(TimetableSlot.dept_code != dept_code).all())
            for s in outside:
                key = (s.faculty_id, s.day)
                blocked[key] = blocked.get(key, 0) | (1 << s.period)

        demands = []
        for a in assignments:
            for _ in range(a.subject.credits):
                demands.append(((a.dept_code, a.year, a.section),
                                a.subject_code, a.faculty_id))

        try:
            info = scheduler_live.solve_with_trace(
                demands, seed=seed, iters=iters, restarts=max_restarts,
                blocked=blocked)
        except scheduler.Unplaceable as exc:
            return {"ok": False, "error": f"no feasible timetable: {exc}"}
        sched = info.pop("sched")
        placed = sched.slots()

        total_needed = len(demands)
        return {
            "ok": True, "proposal_only": True, "scope": dept_code or "ALL",
            "sections": len(sched.sections), "slots_placed": len(placed),
            "slots_required": total_needed,
            "unplaced": total_needed - len(placed),
            "placement_rate": round(100 * len(placed) / total_needed, 2),
            "teacher_conflicts": 0,
            "solver": "greedy+annealing",
            "objective": round(info["final_cost"], 1),
            "objective_at_seed": round(info["seed_cost"], 1),
            "annealing_steps": iters,
            "solve_ms": info["solve_ms"],
            "seed_events": info["seed_events"],
            "anneal_trace": info["anneal_trace"],
            "proposed_slots": placed,
        }

    async def generate_and_announce(self, db, dept_code: str | None,
                                    triggered_by: str) -> dict:
        result = self.generate(db, dept_code)
        if result.get("ok"):
            await self.publish("timetable.proposal_generated", {
                "scope": result["scope"], "sections": result["sections"],
                "placement_rate": result["placement_rate"],
                "solve_ms": result["solve_ms"], "triggered_by": triggered_by})
        return result

    def grid(self, db, dept_code: str, year: int, section: str) -> dict:
        slots = (db.query(TimetableSlot)
                   .filter_by(dept_code=dept_code, year=year, section=section)
                   .all())
        cells = {}
        for s in slots:
            cells[f"{s.day}-{s.period}"] = {
                "subject": s.subject_code, "subject_name": s.subject.name,
                "faculty": s.faculty.name, "room": s.room}
        return {"dept": dept_code, "year": year, "section": section,
                "days": DAYS, "periods": PERIODS, "cells": cells}

    def faculty_grid(self, db, faculty_id: int) -> dict:
        slots = db.query(TimetableSlot).filter_by(faculty_id=faculty_id).all()
        cells = {}
        for s in slots:
            cells[f"{s.day}-{s.period}"] = {
                "subject": s.subject_code, "subject_name": s.subject.name,
                "room": s.room,
                "class": f"{s.dept_code} {s.year}{s.section}"}
        return {"days": DAYS, "periods": PERIODS, "cells": cells}

    def csv_export(self, db, dept_code: str, year: int, section: str) -> str:
        g = self.grid(db, dept_code, year, section)
        lines = [f"Timetable,{dept_code} Year {year} Section {section}"]
        lines.append("Day," + ",".join(PERIODS))
        for d, day in enumerate(DAYS):
            row = [day]
            for p in range(N_PERIODS):
                c = g["cells"].get(f"{d}-{p}")
                row.append(f"{c['subject']} ({c['faculty']})" if c else "-")
            lines.append(",".join('"' + v + '"' if "," in v else v for v in row))
        return "\n".join(lines)
