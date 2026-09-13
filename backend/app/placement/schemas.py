"""Validated placement commands. Status changes use explicit lifecycle actions."""
import datetime as dt
import ipaddress
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

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
    description: str | None = Field(default=None, max_length=20000)
    application_url: str | None = Field(default=None, max_length=2048)

    @field_validator('departments')
    @classmethod
    def normalize_departments(cls, value):
        codes = list(dict.fromkeys(code.strip().upper() for code in value.split(',')))
        if any(not code or not code.isalnum() or len(code) > 8 for code in codes):
            raise ValueError('Use comma-separated department codes or ALL')
        if 'ALL' in codes and len(codes) != 1:
            raise ValueError('ALL cannot be combined with department codes')
        return ','.join(codes)

    @field_validator('description', mode='before')
    @classmethod
    def normalize_description(cls, value):
        if value is None:
            return None
        value = str(value).replace('\r\n', '\n').replace('\r', '\n').strip()
        if '\x00' in value:
            raise ValueError('Description contains unsupported characters')
        return value or None

    @field_validator('application_url', mode='before')
    @classmethod
    def validate_application_url(cls, value):
        if value is None or not str(value).strip():
            return None
        value = str(value).strip()
        if any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError('Application link contains unsupported characters')
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError('Application link must be a valid HTTP or HTTPS URL') from exc
        if parsed.scheme.lower() not in {'http', 'https'} or not parsed.netloc or not parsed.hostname:
            raise ValueError('Application link must use http:// or https://')
        if parsed.username or parsed.password:
            raise ValueError('Application link must not contain credentials')
        host = parsed.hostname.rstrip('.').lower()
        if host in {'localhost', 'localhost.localdomain'} or host.endswith('.local'):
            raise ValueError('Application link must not target an internal host')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError('Application link must not target an internal host')
        # Store a normalized scheme/host while preserving the official path,
        # query and fragment. This URL is returned to browsers, never fetched.
        try:
            normalized_host = host.encode('idna').decode('ascii')
        except UnicodeError as exc:
            raise ValueError('Application link host is invalid') from exc
        netloc = f'[{normalized_host}]' if ':' in normalized_host else normalized_host
        if port is not None:
            netloc += f':{port}'
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or '/', parsed.query, parsed.fragment))


class GenerationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    regenerate: bool = False


class CancellationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=4000)


class OutcomeInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    outcome_status: OutcomeStatus
    package_offered: float | None = Field(default=None, gt=0)
    allow_multiple_offers: bool = False


class JobDocumentRecord(BaseModel):
    original_name: str
    content_type: str
    size_bytes: int
    uploaded_at: dt.datetime
    download_url: str


class DriveRecord(BaseModel):
    id: int
    company: str
    role: str
    package_lpa: float
    drive_date: dt.date
    departments: str
    status: DriveStatus
    min_cgpa: float
    max_backlogs: int
    min_attendance: float
    requires_fee_clearance: bool
    application_deadline: dt.date | None
    description: str | None
    application_url: str | None
    cancellation_reason: str | None
    job_document: JobDocumentRecord | None
    created_at: dt.datetime
    updated_at: dt.datetime


class DriveListRecord(DriveRecord):
    candidate_count: int | None = None
    shortlisted_count: int | None = None
    eligible: bool | None = None
    ml_probability: float | None = None
    model_version: str | None = None
    reasons: str | None = None
    can_apply: bool | None = None
    apply_message: str | None = None
    eligibility_status: str | None = None


class StudentDriveDetail(BaseModel):
    usn: str
    drive: DriveRecord
    status: str
    eligible: bool | None
    ml_probability: float | None
    model_version: str | None
    reasons: str
    updated_at: dt.datetime | None = None
    can_apply: bool
    apply_message: str


class OutcomeRecord(BaseModel):
    id: int
    drive_id: int
    usn: str
    outcome_status: OutcomeStatus
    package_offered: float | None
    decided_at: dt.datetime
    updated_at: dt.datetime


class ShortlistRecord(BaseModel):
    usn: str
    name: str
    eligible: bool
    ml_probability: float | None
    model_version: str | None
    reasons: str
    updated_at: dt.datetime | None
    outcome: OutcomeRecord | None = None


class AdminPlacementView(BaseModel):
    drive: DriveRecord
    shortlist: list[ShortlistRecord]
    other_outcomes: list[OutcomeRecord]
