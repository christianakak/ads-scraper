"""
SupabaseStore — persistence layer for audit results and outcomes.

Wraps the Supabase Python client (synchronous) with asyncio.to_thread()
so it integrates cleanly with the async engine and API.

Cache strategy:
  - On audit: upsert row with all extracted columns + full_audit_json
  - On cache hit: deserialize full_audit_json → AuditReport (no re-scraping)
  - TTL: configurable, default 30 days (AUDIT_CACHE_TTL_DAYS)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core.base.schemas import AuditReport


class SupabaseStore:
    def __init__(self, url: str, service_key: str, cache_ttl_days: int = 30) -> None:
        try:
            from supabase import create_client
            self._client = create_client(url, service_key)
        except ImportError as exc:
            raise RuntimeError(
                "supabase package not installed. Run: pip install supabase"
            ) from exc
        self._ttl = cache_ttl_days

    # ------------------------------------------------------------------
    # Cache reads
    # ------------------------------------------------------------------

    async def get_cached(self, domain: str, vertical: str) -> AuditReport | None:
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=self._ttl)).isoformat()
        try:
            result = await asyncio.to_thread(
                lambda: (
                    self._client.table("audits")
                    .select("full_audit_json, collected_at")
                    .eq("domain", domain)
                    .eq("vertical", vertical)
                    .gte("collected_at", cutoff)
                    .order("collected_at", desc=True)
                    .limit(1)
                    .execute()
                )
            )
            rows = result.data or []
            if rows and rows[0].get("full_audit_json"):
                return AuditReport.model_validate(rows[0]["full_audit_json"])
        except Exception:
            pass
        return None

    async def get_audit(self, audit_id: str) -> AuditReport | None:
        try:
            result = await asyncio.to_thread(
                lambda: (
                    self._client.table("audits")
                    .select("full_audit_json")
                    .eq("id", audit_id)
                    .limit(1)
                    .execute()
                )
            )
            rows = result.data or []
            if rows and rows[0].get("full_audit_json"):
                return AuditReport.model_validate(rows[0]["full_audit_json"])
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def upsert_audit(self, report: AuditReport) -> None:
        row = _report_to_row(report)
        await asyncio.to_thread(
            lambda: self._client.table("audits").upsert(row).execute()
        )

    async def record_outcome(
        self,
        audit_id: str,
        outcome: str,
        notes: str | None = None,
    ) -> str:
        outcome_id = str(uuid.uuid4())
        await asyncio.to_thread(
            lambda: self._client.table("outcomes").insert({
                "id": outcome_id,
                "audit_id": audit_id,
                "outcome": outcome,
                "notes": notes,
            }).execute()
        )
        return outcome_id

    async def update_triage(
        self,
        audit_id: str,
        review_status: str,
        reviewer_note: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"review_status": review_status}
        if reviewer_note:
            payload["review_note"] = reviewer_note
        await asyncio.to_thread(
            lambda: (
                self._client.table("audits")
                .update(payload)
                .eq("id", audit_id)
                .execute()
            )
        )

    async def get_triage_queue(
        self, status: str = "pending_review", limit: int = 50
    ) -> list[dict[str, Any]]:
        try:
            result = await asyncio.to_thread(
                lambda: (
                    self._client.table("audits")
                    .select(
                        "id, domain, icp_persona, top_pain_signal, top_pain_severity, "
                        "hook_text, audit_confidence, review_status, collected_at"
                    )
                    .eq("review_status", status)
                    .order("audit_confidence", desc=False)  # lowest confidence first
                    .limit(limit)
                    .execute()
                )
            )
            return result.data or []
        except Exception:
            return []

    async def get_outcome_stats(
        self, vertical: str = "proptech", signal_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Conversion rates per pain signal + ICP persona combo."""
        try:
            query = (
                self._client.table("outcomes")
                .select("outcome, audits!inner(icp_persona, top_pain_signal, vertical)")
                .eq("audits.vertical", vertical)
            )
            result = await asyncio.to_thread(lambda: query.execute())
            return _aggregate_outcome_stats(result.data or [], signal_id)
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Row mapping helpers
# ---------------------------------------------------------------------------

def _report_to_row(report: AuditReport) -> dict[str, Any]:
    """Flatten AuditReport to Supabase table columns."""
    top_signal = report.pain_signals[0] if report.pain_signals else None
    dns = report.email_infrastructure
    ts = report.tech_stack
    site_raw = report.raw_collector_output.get("site_scanner", {})
    ad_raw = report.raw_collector_output.get("ad_intelligence", {})
    portal_raw = report.raw_collector_output.get("portal_quality", {})
    planning_raw = report.raw_collector_output.get("planning_intel", {})
    reviews_raw = report.raw_collector_output.get("social_review", {})

    return {
        "id": report.audit_id,
        "domain": report.domain,
        "vertical": report.vertical.value,
        "geography": report.geography.value,
        "rules_version": report.rules_version,

        "icp_persona": report.icp_persona.value if report.icp_persona else None,
        "icp_confidence": report.icp_confidence,
        "high_intent": report.high_intent,
        "high_intent_reason": report.high_intent_reason,

        "top_pain_signal": top_signal.signal_id if top_signal else None,
        "top_pain_severity": top_signal.severity.value if top_signal else None,
        "top_pain_confidence": top_signal.confidence if top_signal else None,

        "primary_module": report.primary_module.value if report.primary_module else None,
        "recommended_modules": [m.value for m in report.recommended_modules],

        "hook_text": report.outbound.hook_text if report.outbound else None,
        "subject_line": report.outbound.subject_line if report.outbound else None,
        "follow_up_angle": report.outbound.follow_up_angle if report.outbound else None,

        # Key signals extracted for Clay column mapping
        "ad_creative_age_days": ad_raw.get("creative_age_days"),
        "has_digital_reservation": site_raw.get("has_digital_reservation"),
        "has_virtual_tour": site_raw.get("has_virtual_tour"),
        "has_interactive_floor_plans": site_raw.get("has_interactive_floor_plans"),
        "cta_type": site_raw.get("cta_type"),
        "load_time_ms": site_raw.get("load_time_ms"),
        "mobile_score": site_raw.get("mobile_score"),
        "project_count": site_raw.get("project_count"),
        "crm_detected": ts.crm if ts else None,
        "has_facebook_pixel": ts.has_facebook_pixel if ts else None,
        "has_google_tag_manager": ts.has_google_tag_manager if ts else None,
        "domain_age_years": dns.domain_age_years if dns else None,
        "has_spf": dns.has_spf if dns else None,
        "has_dkim": dns.has_dkim if dns else None,
        "has_dmarc": dns.has_dmarc if dns else None,
        "days_on_market": portal_raw.get("days_on_market"),
        "listing_quality_score": portal_raw.get("listing_quality_score"),
        "avg_review_rating": reviews_raw.get("avg_rating"),
        "review_count": reviews_raw.get("review_count"),
        "planning_granted_date": planning_raw.get("planning_granted_date"),
        "development_stage": planning_raw.get("development_stage"),

        "review_status": report.triage.review_status.value if report.triage else "pending_review",
        "audit_confidence": report.triage.audit_confidence if report.triage else None,

        "pain_signals": [s.model_dump(mode="json") for s in report.pain_signals],
        "tech_stack": ts.model_dump(mode="json") if ts else {},
        "email_infrastructure": report.email_infrastructure.model_dump(mode="json") if report.email_infrastructure else {},
        "raw_collector_output": {k: v for k, v in report.raw_collector_output.items()},

        "full_audit_json": report.model_dump(mode="json"),
        "collector_errors": report.cache_meta.collector_errors if report.cache_meta else [],
    }


def _aggregate_outcome_stats(rows: list[dict], signal_filter: str | None) -> list[dict]:
    from collections import defaultdict
    stats: dict[tuple, dict] = defaultdict(lambda: {"meeting_booked": 0, "uninterested": 0, "no_reply": 0, "total": 0})
    for row in rows:
        audit = row.get("audits", {})
        key = (audit.get("icp_persona", "unknown"), audit.get("top_pain_signal", "unknown"))
        outcome = row.get("outcome", "no_reply")
        stats[key]["total"] += 1
        stats[key][outcome] = stats[key].get(outcome, 0) + 1

    result = []
    for (persona, signal), counts in stats.items():
        if signal_filter and signal != signal_filter:
            continue
        total = counts["total"]
        result.append({
            "icp_persona": persona,
            "pain_signal": signal,
            "total": total,
            "meeting_booked": counts.get("meeting_booked", 0),
            "conversion_rate": round(counts.get("meeting_booked", 0) / total, 3) if total else 0,
        })
    return sorted(result, key=lambda x: x["conversion_rate"], reverse=True)
