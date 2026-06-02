"""
DomainAuditor — orchestrates a full audit run.

Flow:
  1. Cache check (Supabase, skip if force_refresh)
  2. Phase 0: Screening collector (DNS/Headers, ~1s) — gate before expensive work
  3. Phase 1: All remaining collectors in parallel (asyncio.gather)
  4. Analyze: run all analyzers against the collector map
  5. Compute audit_confidence + assign review_status
  6. Assemble AuditReport
  7. Persist to store if present

ICP classification and hook generation are called by the API layer after
this method returns — keeping the engine pure and fully unit-testable.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from statistics import mean
from typing import Any

from .base.schemas import (
    AuditReport,
    AuditRequest,
    CacheMeta,
    CollectorResult,
    ReviewStatus,
    TriageMeta,
)
from .registry import VerticalRegistry

logger = logging.getLogger(__name__)

# Confidence thresholds for triage routing
_AUTO_APPROVE_THRESHOLD = 0.80
_PENDING_REVIEW_THRESHOLD = 0.60


class DomainAuditor:
    def __init__(
        self,
        settings: Any,
        store: Any | None = None,
        on_collector_done: Any | None = None,
    ) -> None:
        """
        Args:
            settings:          Config instance (passed to collectors)
            store:             Optional persistence layer (SupabaseStore).
            on_collector_done: Optional callback(CollectorResult) called after each
                               collector finishes. Used by the terminal UI for live updates.
        """
        self.settings = settings
        self.store = store
        self._on_collector_done = on_collector_done

    async def _run(self, cls: Any, domain: str, geography: str) -> CollectorResult:
        result = await cls(self.settings).run(domain, geography)
        if self._on_collector_done:
            self._on_collector_done(result)
        return result

    async def audit(self, request: AuditRequest) -> AuditReport:
        # ------------------------------------------------------------------
        # 1. Cache check
        # ------------------------------------------------------------------
        if self.store and not request.force_refresh:
            cached = await self.store.get_cached(request.domain, request.vertical.value)
            if cached:
                logger.info("Cache hit for %s (%s)", request.domain, request.vertical.value)
                return cached

        vertical_config = VerticalRegistry.get(request.vertical.value)
        collector_classes: list = vertical_config["collectors"]
        analyzer_classes: list = vertical_config["analyzers"]
        rules_version: str = vertical_config.get("rules_version", "1.0.0")

        # ------------------------------------------------------------------
        # 2. Phase 0: Screening (DNS/Headers)
        # ------------------------------------------------------------------
        screening_class = next(
            (c for c in collector_classes if getattr(c, "is_screening", False)), None
        )
        collector_results: list[CollectorResult] = []

        if screening_class:
            screening_result = await self._run(screening_class, request.domain, request.geography.value)
            collector_results.append(screening_result)

            if not screening_result.success:
                logger.warning("Screening failed for %s: %s", request.domain, screening_result.error)
                return self._unreachable_report(request, rules_version, screening_result.error)

        # ------------------------------------------------------------------
        # 3. Phase 1: Run collectors — browser ones sequentially to avoid 529
        # ------------------------------------------------------------------
        remaining_classes = [c for c in collector_classes if not getattr(c, "is_screening", False)]

        if remaining_classes:
            parallel_classes = [c for c in remaining_classes if not getattr(c, "requires_browser", False)]
            browser_classes = [c for c in remaining_classes if getattr(c, "requires_browser", False)]

            # Parallel: all non-browser collectors at once
            parallel_results: list[CollectorResult] = []
            if parallel_classes:
                tasks = [self._run(cls, request.domain, request.geography.value) for cls in parallel_classes]
                parallel_results = list(await asyncio.gather(*tasks))

            # Sequential: browser collectors one at a time (Browserless concurrent session limit)
            browser_results: list[CollectorResult] = []
            for cls in browser_classes:
                result = await self._run(cls, request.domain, request.geography.value)
                browser_results.append(result)

            collector_results.extend(parallel_results + browser_results)

        collector_map = {r.collector_id: r for r in collector_results}
        errors = [
            {"collector": r.collector_id, "error": r.error}
            for r in collector_results
            if not r.success
        ]

        logger.info(
            "Collected %d/%d collectors for %s (%d errors)",
            sum(1 for r in collector_results if r.success),
            len(collector_results),
            request.domain,
            len(errors),
        )

        # ------------------------------------------------------------------
        # 4. Analyze
        # ------------------------------------------------------------------
        pain_signals = []
        icp_result = None
        for analyzer_class in analyzer_classes:
            analyzer = analyzer_class(vertical_config["rules_path"])
            pain_signals.extend(analyzer.analyze(collector_map))
            # Pick up ICP classification if ICPClassifier ran
            try:
                from verticals.proptech.analyzers.icp_classifier import pop_icp_result
                candidate = pop_icp_result(collector_map)
                if candidate is not None:
                    icp_result = candidate
            except ImportError:
                pass

        # ------------------------------------------------------------------
        # 5. Triage
        # ------------------------------------------------------------------
        if pain_signals:
            audit_confidence = mean(s.confidence for s in pain_signals)
        else:
            audit_confidence = 0.5

        if audit_confidence >= _AUTO_APPROVE_THRESHOLD:
            review_status = ReviewStatus.AUTO_APPROVED
        elif audit_confidence >= _PENDING_REVIEW_THRESHOLD:
            review_status = ReviewStatus.PENDING_REVIEW
        else:
            review_status = ReviewStatus.FLAGGED

        # ------------------------------------------------------------------
        # 6. Assemble report
        # ------------------------------------------------------------------
        # Sort signals: CRITICAL first, then by confidence descending
        _severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        pain_signals.sort(key=lambda s: (_severity_order.get(s.severity.value, 9), -s.confidence))

        # Derive recommended modules from top pain signals
        seen_modules: list = []
        for sig in pain_signals:
            if sig.m360_module not in seen_modules:
                seen_modules.append(sig.m360_module)

        # Tech stack from site scanner
        tech_stack_data = collector_map.get("site_scanner")
        tech_stack = None
        if tech_stack_data and tech_stack_data.success:
            from core.base.schemas import TechStack
            ts = tech_stack_data.data.get("tech_stack", {})
            if ts:
                tech_stack = TechStack(**{k: v for k, v in ts.items() if k != "raw_wappalyzer"})

        # Email infra from dns collector
        dns_data = collector_map.get("dns_headers")
        email_infra = None
        if dns_data and dns_data.success:
            from core.base.schemas import EmailInfrastructure
            email_infra = EmailInfrastructure(
                has_spf=dns_data.data.get("has_spf", False),
                has_dkim=dns_data.data.get("has_dkim", False),
                has_dmarc=dns_data.data.get("has_dmarc", False),
                email_provider=dns_data.data.get("email_provider"),
                domain_age_years=dns_data.data.get("domain_age_years"),
            )

        report = AuditReport(
            domain=request.domain,
            geography=request.geography,
            vertical=request.vertical,
            rules_version=rules_version,
            icp_persona=icp_result.top_persona if icp_result else None,
            icp_confidence=icp_result.top_confidence if icp_result else None,
            high_intent=icp_result.high_intent if icp_result else False,
            high_intent_reason=icp_result.high_intent_reason if icp_result else None,
            pain_signals=pain_signals,
            recommended_modules=seen_modules[:5],
            primary_module=seen_modules[0] if seen_modules else None,
            tech_stack=tech_stack,
            email_infrastructure=email_infra,
            triage=TriageMeta(
                review_status=review_status,
                audit_confidence=round(audit_confidence, 3),
            ),
            cache_meta=CacheMeta(
                collected_at=datetime.utcnow(),
                cache_hit=False,
                collectors_run=[r.collector_id for r in collector_results],
                collector_errors=errors,
                data_quality={r.collector_id: r.data_source for r in collector_results},
            ),
            raw_collector_output={r.collector_id: r.data for r in collector_results},
        )

        # ------------------------------------------------------------------
        # 7. Persist
        # ------------------------------------------------------------------
        if self.store:
            try:
                await self.store.upsert_audit(report)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to persist audit for %s: %s", request.domain, exc)

        return report

    @staticmethod
    def _unreachable_report(
        request: AuditRequest, rules_version: str, error: str | None
    ) -> AuditReport:
        return AuditReport(
            domain=request.domain,
            geography=request.geography,
            vertical=request.vertical,
            rules_version=rules_version,
            triage=TriageMeta(
                review_status=ReviewStatus.FLAGGED,
                review_reason="domain_unreachable",
                audit_confidence=0.0,
            ),
            cache_meta=CacheMeta(
                collected_at=datetime.utcnow(),
                cache_hit=False,
                collectors_run=["dns_headers"],
                collector_errors=[{"collector": "dns_headers", "error": error}],
            ),
        )
