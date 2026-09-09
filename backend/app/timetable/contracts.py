"""Immutable, serializable contracts shared by solver and independent validator."""
from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class Period(Contract):
    day: int = Field(ge=0, le=6)
    index: int = Field(ge=0, le=23)
    start: int = Field(ge=0, lt=1440)
    end: int = Field(gt=0, le=1440)
    closed: bool = False


class Section(Contract):
    id: int
    size: int = Field(gt=0)


class Teacher(Contract):
    id: int
    subjects: tuple[str, ...] = ()
    daily_limit: int = Field(gt=0, le=24)
    weekly_limit: int = Field(gt=0, le=168)
    unavailable: tuple[tuple[int, int], ...] = ()


class Room(Contract):
    id: int
    kind: str
    capacity: int = Field(gt=0)
    unavailable: tuple[tuple[int, int], ...] = ()


class Requirement(Contract):
    id: int
    section_id: int
    subject: str
    faculty_id: int
    periods: int = Field(gt=0, le=168)
    max_per_day: int = Field(gt=0, le=24)
    block_length: int = Field(gt=0, le=24, default=1)
    room_type: str = 'classroom'
    preferred_room_type: str | None = None
    priority: int = Field(ge=0, le=10, default=1)


class Entry(Contract):
    requirement_id: int
    occurrence: int = Field(ge=0)
    section_id: int
    subject: str
    faculty_id: int
    room_id: int
    day: int
    period_index: int
    locked: bool = False


class SolverInput(Contract):
    periods: tuple[Period, ...]
    sections: tuple[Section, ...]
    teachers: tuple[Teacher, ...]
    rooms: tuple[Room, ...]
    requirements: tuple[Requirement, ...]
    locked: tuple[Entry, ...] = ()


class Issue(Contract):
    code: str
    message: str
    requirement_id: int | None = None


class Unplaced(Contract):
    requirement_id: int
    section_id: int
    subject: str
    missing_periods: int
    reason: str


class Result(Contract):
    status: str
    entries: tuple[Entry, ...]
    violations: tuple[Issue, ...]
    unplaced: tuple[Unplaced, ...]
    steps: int
    search_conflicts: int
    backtracks: int
    restarts: int
    score: float
    termination: str
