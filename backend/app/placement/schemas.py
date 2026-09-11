"""Validated placement commands. Status changes use explicit lifecycle actions."""
import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DriveStatus = Literal['DRAFT', 'OPEN', 'SHORTLIST_GENERATED', 'CLOSED', 'CANCELLED']
OutcomeStatus = Literal['OFFER_MADE', 'OFFER_ACCEPTED', 'OFFER_DECLINED', 'REJECTED']


class DriveInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)
    company: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=128)
    package_lpa: float = Field(gt=0)
    min_cgpa: float = Field(default=6, ge=0, le=10)
    max_backlogs: int = Field(default=0, ge=0)
    min_attendance: float = Field(default=75, ge=0, le=100)
    drive_date: dt.date
    departments: str = Field(default='ALL', min_length=1, max_length=64)
    status: DriveStatus = 'OPEN'
    requires_fee_clearance: bool = False
    application_deadline: dt.date | None = None

    @field_validator('departments')
    @classmethod
    def normalize_departments(cls, value):
        codes = list(dict.fromkeys(code.strip().upper() for code in value.split(',')))
        if any(not code or not code.isalnum() or len(code) > 8 for code in codes):
            raise ValueError('Use comma-separated department codes or ALL')
        if 'ALL' in codes and len(codes) != 1:
            raise ValueError('ALL cannot be combined with department codes')
        return ','.join(codes)


class GenerationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    regenerate: bool = False


class OutcomeInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    outcome_status: OutcomeStatus
    package_offered: float | None = Field(default=None, gt=0)
    allow_multiple_offers: bool = False
