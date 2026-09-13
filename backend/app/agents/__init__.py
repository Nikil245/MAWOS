"""Agent registry v3 — P2 (docs/RESEARCH_PLAN_V3.md §7): 4 components meet
the pre-registered agent criterion (own state/policy that outlives a
request, act on events without direct invocation) — Orchestrator,
Attendance, Eligibility, Scheduling. The rest of this registry still holds
Academic, Admission, Finance, Placement and Notification, because tools.py
and the REST routes need somewhere to call into for records, workflows and
alerts — they are tool-backed components (or, for Notification, a bus
subscriber), not agents, and CORE_AGENTS below is what "4 agents" means in
code rather than only in prose.
"""
from ..bus import bus
from .academic import AcademicAgent
from .admission import AdmissionAgent
from .attendance import AttendanceAgent
from .eligibility import EligibilityAgent
from .finance import FinanceAgent
from .notification import NotificationAgent
from .orchestrator import OrchestratorAgent
from .placement import PlacementAgent
from .library import LibraryAgent
from .timetable import TimetableAgent

#: The four that meet the pre-registered agent criterion (plan §7).
#: Everything else in the registry is a tool-backed component or (for
#: notification_agent) a bus subscriber that is not counted as an agent.
CORE_AGENTS = ("orchestrator_agent", "attendance_agent",
               "eligibility_agent", "timetable_agent")

_agents: dict | None = None


def get_agents() -> dict:
    global _agents
    if _agents is None:
        registry = {}
        for cls in (AdmissionAgent, TimetableAgent, AcademicAgent,
                    AttendanceAgent, FinanceAgent, EligibilityAgent,
                    PlacementAgent, NotificationAgent, LibraryAgent):
            agent = cls(bus)
            registry[agent.name] = agent
        registry["orchestrator_agent"] = OrchestratorAgent(bus, registry)
        _agents = registry
    return _agents
