"""
HTTP-layer request/response models. Separate from core schemas —
HTTP concerns (validation messages, field aliases, examples) live here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class AuditRequestModel(BaseModel):
    domain: str = Field(..., examples=["developer.co.uk"], description="Domain to audit (no scheme)")
    vertical: str = Field(default="proptech", examples=["proptech"])
    geography: str = Field(..., examples=["uk", "se"], description="'uk' or 'se'")
    force_refresh: bool = Field(default=False, description="Bypass cache and re-scrape")

    @field_validator("domain")
    @classmethod
    def strip_scheme(cls, v: str) -> str:
        return v.removeprefix("https://").removeprefix("http://").rstrip("/").lower()

    @field_validator("geography")
    @classmethod
    def validate_geography(cls, v: str) -> str:
        if v not in ("uk", "se"):
            raise ValueError("geography must be 'uk' or 'se'")
        return v.lower()


class BatchAuditRequestModel(BaseModel):
    domains: list[str] = Field(..., min_length=1, max_length=500)
    vertical: str = Field(default="proptech")
    geography: str = Field(..., examples=["uk", "se"])


class OutcomeRequestModel(BaseModel):
    audit_id: str
    outcome: str = Field(..., examples=["meeting_booked", "uninterested", "no_reply"])
    notes: str | None = None

    @field_validator("outcome")
    @classmethod
    def validate_outcome(cls, v: str) -> str:
        valid = {"meeting_booked", "uninterested", "no_reply"}
        if v not in valid:
            raise ValueError(f"outcome must be one of {valid}")
        return v


class TriageUpdateModel(BaseModel):
    review_status: str = Field(..., examples=["approved", "rejected"])
    reviewer_note: str | None = None

    @field_validator("review_status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        valid = {"approved", "rejected"}
        if v not in valid:
            raise ValueError(f"review_status must be one of {valid}")
        return v


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    registered_verticals: list[str] = []
