"""
Core Pydantic schemas — the data contract between all layers.
Core has zero knowledge of any vertical. Verticals import from here, never vice versa.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Geography(str, Enum):
    UK = "uk"
    SE = "se"


class Vertical(str, Enum):
    PROPTECH = "proptech"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ReviewStatus(str, Enum):
    AUTO_APPROVED = "auto_approved"
    PENDING_REVIEW = "pending_review"
    FLAGGED = "flagged"
    APPROVED = "approved"
    REJECTED = "rejected"


class ICPPersona(str, Enum):
    SCALE_UP_DEVELOPER = "scale_up_developer"
    PREMIUM_VISIONARY = "premium_visionary"
    DATA_DRIVEN_PLANNER = "data_driven_planner"


class M360Module(str, Enum):
    PLOT_AI = "Plot.ai"
    EVE3D = "EVE3D"
    NEWBUILDS = "Newbuilds.com"
    LEMON = "Lemon"
    JOURNEY = "Journey"


class OutcomeType(str, Enum):
    MEETING_BOOKED = "meeting_booked"
    UNINTERESTED = "uninterested"
    NO_REPLY = "no_reply"


# ---------------------------------------------------------------------------
# Collector layer
# ---------------------------------------------------------------------------

class CollectorResult(BaseModel):
    """Envelope returned by every collector. Raw, pre-normalization data."""

    collector_id: str
    domain: str
    collected_at: datetime = Field(default_factory=datetime.utcnow)
    success: bool = True
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    # "real" = live API/scrape, "dummy" = fixture data, "skipped" = no credentials
    data_source: str = "real"


# ---------------------------------------------------------------------------
# Intelligence layer
# ---------------------------------------------------------------------------

class PainSignal(BaseModel):
    """A single diagnosed pain point with full context for outbound use."""

    signal_id: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    detected_value: dict[str, Any]
    business_pain: str
    emotional_trigger: str
    m360_module: M360Module
    hook_angle: str
    icp_fit: list[ICPPersona]
    corroborating_signals: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Output layer components
# ---------------------------------------------------------------------------

class TechStack(BaseModel):
    crm: str | None = None
    analytics: str | None = None
    has_facebook_pixel: bool = False
    has_google_tag_manager: bool = False
    has_cookie_consent: bool = False
    hosting: str | None = None
    chat_platform: str | None = None
    raw_wappalyzer: dict[str, Any] = Field(default_factory=dict)


class EmailInfrastructure(BaseModel):
    has_spf: bool = False
    has_dkim: bool = False
    has_dmarc: bool = False
    email_provider: str | None = None
    domain_age_years: float | None = None


class OutboundCopy(BaseModel):
    hook_text: str
    subject_line: str
    follow_up_angle: str


class TriageMeta(BaseModel):
    review_status: ReviewStatus
    review_reason: str | None = None
    audit_confidence: float = Field(ge=0.0, le=1.0)


class CacheMeta(BaseModel):
    collected_at: datetime
    cache_hit: bool
    collectors_run: list[str]
    collector_errors: list[dict[str, Any]] = Field(default_factory=list)
    # Per-collector data provenance: "real" | "dummy" | "skipped"
    data_quality: dict[str, str] = Field(default_factory=dict)


class ClayFlat(BaseModel):
    """Pre-flattened fields for direct Clay column mapping. No JSON parsing needed."""

    icp_persona: str | None = None
    icp_confidence: float | None = None
    high_intent: bool = False
    review_status: str = "pending_review"
    top_pain_signal: str | None = None
    top_pain_signal_confidence: float | None = None
    top_pain_severity: str | None = None
    primary_module: str | None = None
    hook_text: str | None = None
    subject_line: str | None = None
    ad_creative_age_days: int | None = None
    has_digital_reservation: bool | None = None
    has_virtual_tour: bool | None = None
    has_interactive_floor_plans: bool | None = None
    cta_type: str | None = None
    load_time_ms: int | None = None
    mobile_score: int | None = None
    project_count: int | None = None
    crm_detected: str | None = None
    has_facebook_pixel: bool | None = None
    has_google_tag_manager: bool | None = None
    domain_age_years: float | None = None
    rules_version: str | None = None
    collected_at: datetime | None = None
    # Data quality — surfaces which collectors used real vs dummy/skipped data
    has_dummy_data: bool = False
    dummy_collectors: list[str] = Field(default_factory=list)
    skipped_collectors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level report
# ---------------------------------------------------------------------------

class AuditReport(BaseModel):
    """The complete output of a domain audit. Stored in Supabase and returned by the API."""

    audit_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    domain: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    geography: Geography
    vertical: Vertical
    rules_version: str

    icp_persona: ICPPersona | None = None
    icp_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    high_intent: bool = False
    high_intent_reason: str | None = None

    pain_signals: list[PainSignal] = Field(default_factory=list)
    recommended_modules: list[M360Module] = Field(default_factory=list)
    primary_module: M360Module | None = None

    outbound: OutboundCopy | None = None
    tech_stack: TechStack | None = None
    email_infrastructure: EmailInfrastructure | None = None
    triage: TriageMeta | None = None
    cache_meta: CacheMeta | None = None
    raw_collector_output: dict[str, Any] = Field(default_factory=dict)
    clay_flat: ClayFlat | None = None


# ---------------------------------------------------------------------------
# API request/response types
# ---------------------------------------------------------------------------

class AuditRequest(BaseModel):
    domain: str
    vertical: Vertical = Vertical.PROPTECH
    geography: Geography
    force_refresh: bool = False


class OutcomeRequest(BaseModel):
    audit_id: str
    outcome: OutcomeType
    notes: str | None = None


class OutcomeResponse(BaseModel):
    outcome_id: str
    audit_id: str
    recorded: bool = True
