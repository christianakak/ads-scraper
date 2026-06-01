"""
FastAPI application — works locally via uvicorn AND as an AWS Lambda handler
via Mangum (see lambda_handler.py). No Lambda-specific code lives here.

Clay HTTP Enrichment calls POST /v1/audit and maps the response fields.
Everything else (CLI, batch scripts, direct API calls) uses the same endpoints.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import Settings
from core.base.schemas import AuditRequest, Geography, OutcomeRequest, OutcomeResponse, Vertical
from core.engine import DomainAuditor
from core.hook_generator import HookGenerator
from core.registry import VerticalRegistry

from .models import (
    AuditRequestModel,
    BatchAuditRequestModel,
    HealthResponse,
    OutcomeRequestModel,
    TriageUpdateModel,
)

logger = logging.getLogger(__name__)
settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    # Register all verticals at startup
    from verticals.proptech import register as register_proptech
    register_proptech()
    logger.info("Registered verticals: %s", VerticalRegistry.list_verticals())
    yield


app = FastAPI(
    title="GTM Intelligence Engine",
    description="Digital Sales Forensic Suite — domain-to-pain-audit for B2B outbound",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)

# Singletons — instantiated once, shared across requests
_auditor = DomainAuditor(settings, store=None)  # store wired in Phase 6 (Supabase)
_hook_gen = HookGenerator(settings.anthropic_api_key)


# ---------------------------------------------------------------------------
# Audit endpoints
# ---------------------------------------------------------------------------

@app.post("/v1/audit", tags=["audit"])
async def run_audit(request: AuditRequestModel):
    """
    Run a full domain audit. Returns cached result if available and < 30 days old.
    Set force_refresh=true to bypass cache.

    Clay HTTP Enrichment: POST this endpoint with {domain, geography} and map
    the clay_flat fields directly to your Clay table columns.
    """
    audit_request = AuditRequest(
        domain=request.domain,
        vertical=Vertical(request.vertical),
        geography=Geography(request.geography),
        force_refresh=request.force_refresh,
    )

    report = await _auditor.audit(audit_request)

    # Generate hook if we have pain signals and no outbound copy yet
    if report.pain_signals and not report.outbound and settings.anthropic_api_key:
        try:
            report.outbound = await _hook_gen.generate(report)
        except Exception as exc:  # noqa: BLE001
            logger.error("Hook generation failed for %s: %s", request.domain, exc)

    # Build clay_flat from report
    report.clay_flat = _build_clay_flat(report)

    return report


@app.get("/v1/audit/{audit_id}", tags=["audit"])
async def get_audit(audit_id: str):
    """Retrieve a stored audit by ID."""
    # Store not wired yet — returns 501 until Phase 6
    raise HTTPException(status_code=501, detail="Supabase store not yet configured")


@app.post("/v1/audit/batch", tags=["audit"])
async def batch_audit(request: BatchAuditRequestModel):
    """Queue a batch audit job. Returns job_id for status polling."""
    # Implemented in Phase 5 (async job queue)
    raise HTTPException(status_code=501, detail="Batch audit not yet implemented")


# ---------------------------------------------------------------------------
# Outcome Feed
# ---------------------------------------------------------------------------

@app.post("/v1/outcome", tags=["outcomes"])
async def record_outcome(request: OutcomeRequestModel):
    """
    Record a sales outcome for an audit. Feeds the learning loop.
    Use this when a lead books a meeting, replies as uninterested, or stays silent.
    """
    # Store not wired yet — stub response until Phase 6
    return OutcomeResponse(
        outcome_id=str(uuid.uuid4()),
        audit_id=request.audit_id,
        recorded=False,
    )


@app.get("/v1/outcome/stats", tags=["outcomes"])
async def outcome_stats(
    vertical: str = Query(default="proptech"),
    signal: str | None = Query(default=None),
):
    """Conversion rates per pain signal + ICP persona combo."""
    raise HTTPException(status_code=501, detail="Outcome stats not yet implemented")


# ---------------------------------------------------------------------------
# Triage Queue
# ---------------------------------------------------------------------------

@app.get("/v1/triage", tags=["triage"])
async def get_triage_queue(
    status: str = Query(default="pending_review"),
    limit: int = Query(default=50, le=200),
):
    """List audits awaiting manual review."""
    raise HTTPException(status_code=501, detail="Triage queue requires Supabase store")


@app.patch("/v1/triage/{audit_id}", tags=["triage"])
async def update_triage(audit_id: str, request: TriageUpdateModel):
    """Approve or reject a triage-flagged audit."""
    raise HTTPException(status_code=501, detail="Triage update requires Supabase store")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
async def health():
    return HealthResponse(registered_verticals=VerticalRegistry.list_verticals())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_clay_flat(report):  # type: ignore[return]
    from core.base.schemas import ClayFlat

    top = report.pain_signals[0] if report.pain_signals else None
    dns_data = report.raw_collector_output.get("dns_headers", {})
    site_data = report.raw_collector_output.get("site_scanner", {})
    ad_data = report.raw_collector_output.get("ad_intelligence", {})

    return ClayFlat(
        icp_persona=report.icp_persona.value if report.icp_persona else None,
        icp_confidence=report.icp_confidence,
        high_intent=report.high_intent,
        review_status=report.triage.review_status.value if report.triage else "pending_review",
        top_pain_signal=top.signal_id if top else None,
        top_pain_signal_confidence=top.confidence if top else None,
        top_pain_severity=top.severity.value if top else None,
        primary_module=report.primary_module.value if report.primary_module else None,
        hook_text=report.outbound.hook_text if report.outbound else None,
        subject_line=report.outbound.subject_line if report.outbound else None,
        ad_creative_age_days=ad_data.get("creative_age_days"),
        has_digital_reservation=site_data.get("has_digital_reservation"),
        has_virtual_tour=site_data.get("has_virtual_tour"),
        has_interactive_floor_plans=site_data.get("has_interactive_floor_plans"),
        cta_type=site_data.get("cta_type"),
        load_time_ms=site_data.get("load_time_ms"),
        mobile_score=site_data.get("mobile_score"),
        project_count=site_data.get("project_count"),
        crm_detected=report.tech_stack.crm if report.tech_stack else None,
        has_facebook_pixel=report.tech_stack.has_facebook_pixel if report.tech_stack else None,
        has_google_tag_manager=report.tech_stack.has_google_tag_manager if report.tech_stack else None,
        domain_age_years=dns_data.get("domain_age_years"),
        rules_version=report.rules_version,
        collected_at=report.cache_meta.collected_at if report.cache_meta else None,
    )
