"""Strict API contracts for faculty absence and runtime class coverage."""
import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReasonCategory = Literal["MEDICAL", "OFFICIAL_DUTY", "PERSONAL", "OTHER"]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AbsenceCreate(StrictBody):
    starts_on: dt.date
    ends_on: dt.date
    period_id: int | None = Field(default=None, ge=1)
    reason_category: ReasonCategory
    private_note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.ends_on < self.starts_on:
            raise ValueError("Absence end date must be on or after its start date.")
        if (self.ends_on - self.starts_on).days > 31:
            raise ValueError("An absence request may cover at most 32 days.")
        self.private_note = self.private_note or None
        return self


class ReviewBody(StrictBody):
    decision: Literal["APPROVE", "REJECT"]


class CandidateApproval(StrictBody):
    substitute_faculty_id: int = Field(ge=1)


class AttendanceSubmission(StrictBody):
    occurrence_id: int = Field(ge=1)
    date: dt.date
    dept: str = Field(min_length=1, max_length=8)
    year: int = Field(ge=1, le=4)
    section: str = Field(min_length=1, max_length=4)
    subject_code: str = Field(min_length=1, max_length=16)
    absent_usns: list[str] = Field(default_factory=list, max_length=500)

