"""
FastAPI application — works locally via uvicorn AND as an AWS Lambda handler
via Mangum. No Lambda-specific code lives here.

Clay HTTP Enrichment calls POST /v1/audit and maps fields from clay_flat.
Everything else (CLI, batch scripts, direct API calls) uses the same endpoints.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from config import Settings
from core.base.schemas import (
    AuditRequest,
    ClayFlat,
    Geography,
    OutcomeResponse,
    Vertical,
)
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

# Singletons
_store = None
_auditor: DomainAuditor | None = None
_hook_gen: HookGenerator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    global _store, _auditor, _hook_gen

    # Register verticals
    from verticals.proptech import register as register_proptech
    register_proptech()
    logger.info("Registered verticals: %s", VerticalRegistry.list_verticals())

    # Wire Supabase store if credentials present
    if settings.supabase_url and settings.supabase_service_key:
        try:
            from core.store import SupabaseStore
            _store = SupabaseStore(
                settings.supabase_url,
                settings.supabase_service_key,
                settings.audit_cache_ttl_days,
            )
            logger.info("Supabase store connected")
        except Exception as exc:
            logger.warning("Supabase store unavailable: %s", exc)

    _auditor = DomainAuditor(settings, store=_store)

    if settings.anthropic_api_key:
        _hook_gen = HookGenerator(settings.anthropic_api_key)
        logger.info("Hook generator ready")
    else:
        logger.warning("ANTHROPIC_API_KEY not set — hooks will be skipped")

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


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

@app.post("/v1/audit", tags=["audit"])
async def run_audit(request: AuditRequestModel):
    """
    Run a full domain audit.

    Returns cached result if available and collected within AUDIT_CACHE_TTL_DAYS.
    Set force_refresh=true to bypass cache and re-scrape.

    Clay HTTP Enrichment: POST {domain, geography} → map clay_flat fields.
    """
    if _auditor is None:
        raise HTTPException(status_code=503, detail="Engine not initialised")

    audit_req = AuditRequest(
        domain=request.domain,
        vertical=Vertical(request.vertical),
        geography=Geography(request.geography),
        force_refresh=request.force_refresh,
    )

    report = await _auditor.audit(audit_req)

    # Generate hook if we have signals and the generator is available
    if report.pain_signals and not report.outbound and _hook_gen:
        try:
            report.outbound = await _hook_gen.generate(report)
        except Exception as exc:
            logger.error("Hook generation failed for %s: %s", request.domain, exc)

    # Build clay_flat
    report.clay_flat = _build_clay_flat(report)

    return report


@app.get("/v1/audit/{audit_id}", tags=["audit"])
async def get_audit(audit_id: str):
    """Retrieve a stored audit by ID."""
    if _store is None:
        raise HTTPException(status_code=503, detail="Store not configured (set SUPABASE_URL)")
    report = await _store.get_audit(audit_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Audit not found")
    return report


@app.post("/v1/audit/batch", tags=["audit"])
async def batch_audit(request: BatchAuditRequestModel):
    """Queue a batch audit job. Returns job_id for status polling."""
    raise HTTPException(status_code=501, detail="Batch audit not yet implemented — use CLI: audit batch domains.csv")


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

@app.post("/v1/outcome", tags=["outcomes"])
async def record_outcome(request: OutcomeRequestModel):
    """
    Record a sales outcome for an audit. Feeds the learning loop.

    Call this when a lead books a meeting, replies as uninterested, or stays silent.
    Over time, this data reveals which pain signals convert best per ICP persona.
    """
    if _store is None:
        # Return success stub so Clay sequences don't break when store is offline
        return OutcomeResponse(
            outcome_id=str(uuid.uuid4()),
            audit_id=request.audit_id,
            recorded=False,
        )

    try:
        outcome_id = await _store.record_outcome(
            request.audit_id,
            request.outcome,
            request.notes,
        )
        return OutcomeResponse(outcome_id=outcome_id, audit_id=request.audit_id, recorded=True)
    except Exception as exc:
        logger.error("Failed to record outcome: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to record outcome")


@app.get("/v1/outcome/stats", tags=["outcomes"])
async def outcome_stats(
    vertical: str = Query(default="proptech"),
    signal: str | None = Query(default=None, description="Filter by pain signal ID"),
):
    """
    Conversion rates per pain signal + ICP persona combo.
    Use this to identify which hooks convert best and tune rule weights.
    """
    if _store is None:
        raise HTTPException(status_code=503, detail="Store not configured")
    return await _store.get_outcome_stats(vertical, signal)


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

@app.get("/v1/triage", tags=["triage"])
async def get_triage_queue(
    status: str = Query(default="pending_review"),
    limit: int = Query(default=50, le=200),
):
    """List audits awaiting manual review. Lowest confidence audits surfaced first."""
    if _store is None:
        raise HTTPException(status_code=503, detail="Store not configured")
    return await _store.get_triage_queue(status, limit)


@app.patch("/v1/triage/{audit_id}", tags=["triage"])
async def update_triage(audit_id: str, request: TriageUpdateModel):
    """Approve or reject a triage-flagged audit. Approved audits enter Clay sequences."""
    if _store is None:
        raise HTTPException(status_code=503, detail="Store not configured")
    await _store.update_triage(audit_id, request.review_status, request.reviewer_note)
    return {"audit_id": audit_id, "review_status": request.review_status}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
async def health():
    return HealthResponse(
        registered_verticals=VerticalRegistry.list_verticals(),
        store_connected=_store is not None,
        hook_generator_ready=_hook_gen is not None,
    )


# ---------------------------------------------------------------------------
# clay_flat builder
# ---------------------------------------------------------------------------

def _build_clay_flat(report) -> ClayFlat:  # type: ignore[return]
    top = report.pain_signals[0] if report.pain_signals else None
    dns = report.raw_collector_output.get("dns_headers", {})
    site = report.raw_collector_output.get("site_scanner", {})
    ads = report.raw_collector_output.get("ad_intelligence", {})
    _portal = report.raw_collector_output.get("portal_quality", {})
    _planning = report.raw_collector_output.get("planning_intel", {})
    _reviews = report.raw_collector_output.get("social_review", {})
    ts = report.tech_stack

    return ClayFlat(
        # ICP
        icp_persona=report.icp_persona.value if report.icp_persona else None,
        icp_confidence=report.icp_confidence,
        high_intent=report.high_intent,
        review_status=report.triage.review_status.value if report.triage else "pending_review",

        # Top pain
        top_pain_signal=top.signal_id if top else None,
        top_pain_signal_confidence=top.confidence if top else None,
        top_pain_severity=top.severity.value if top else None,

        # Modules
        primary_module=report.primary_module.value if report.primary_module else None,

        # Outbound copy
        hook_text=report.outbound.hook_text if report.outbound else None,
        subject_line=report.outbound.subject_line if report.outbound else None,

        # Ad signals
        ad_creative_age_days=ads.get("creative_age_days"),

        # Site signals
        has_digital_reservation=site.get("has_digital_reservation"),
        has_virtual_tour=site.get("has_virtual_tour"),
        has_interactive_floor_plans=site.get("has_interactive_floor_plans"),
        cta_type=site.get("cta_type"),
        load_time_ms=site.get("load_time_ms"),
        mobile_score=site.get("mobile_score"),
        project_count=site.get("project_count"),

        # Tech stack
        crm_detected=ts.crm if ts else site.get("tech_stack", {}).get("crm"),
        has_facebook_pixel=(
            ts.has_facebook_pixel if ts else site.get("tech_stack", {}).get("has_facebook_pixel")
        ),
        has_google_tag_manager=(
            ts.has_google_tag_manager if ts else site.get("tech_stack", {}).get("has_google_tag_manager")
        ),

        # DNS
        domain_age_years=dns.get("domain_age_years"),

        # Meta
        rules_version=report.rules_version,
        collected_at=report.cache_meta.collected_at if report.cache_meta else None,

        # Data quality — tells Clay which signals are real vs dummy/skipped
        has_dummy_data=any(
            v == "dummy" for v in (report.cache_meta.data_quality.values() if report.cache_meta else [])
        ),
        dummy_collectors=[
            k for k, v in (report.cache_meta.data_quality.items() if report.cache_meta else [])
            if v == "dummy"
        ],
        skipped_collectors=[
            k for k, v in (report.cache_meta.data_quality.items() if report.cache_meta else [])
            if v == "skipped"
        ],
    )
