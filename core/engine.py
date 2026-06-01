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
    def __init__(self, settings: Any, store: Any | None = None) -> None:
        """
        Args:
            settings: Config instance (passed to collectors)
            store:    Optional persistence layer (SupabaseStore).
                      When None, operates fully stateless — useful for CLI and tests.
        """
        self.settings = settings
        self.store = store

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
            screener = screening_class(self.settings)
            screening_result = await screener.run(request.domain, request.geography.value)
            collector_results.append(screening_result)

            if not screening_result.success:
                logger.warning("Screening failed for %s: %s", request.domain, screening_result.error)
                return self._unreachable_report(request, rules_version, screening_result.error)

        # ------------------------------------------------------------------
        # 3. Phase 1: Remaining collectors in parallel
        # ------------------------------------------------------------------
        remaining_classes = [c for c in collector_classes if not getattr(c, "is_screening", False)]

        if remaining_classes:
            instances = [cls(self.settings) for cls in remaining_classes]
            tasks = [inst.run(request.domain, request.geography.value) for inst in instances]
            phase1_results: list[CollectorResult] = list(await asyncio.gather(*tasks))
            collector_results.extend(phase1_results)

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
        for analyzer_class in analyzer_classes:
            analyzer = analyzer_class(vertical_config["rules_path"])
            pain_signals.extend(analyzer.analyze(collector_map))

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

        report = AuditReport(
            domain=request.domain,
            geography=request.geography,
            vertical=request.vertical,
            rules_version=rules_version,
            pain_signals=pain_signals,
            triage=TriageMeta(
                review_status=review_status,
                audit_confidence=round(audit_confidence, 3),
            ),
            cache_meta=CacheMeta(
                collected_at=datetime.utcnow(),
                cache_hit=False,
                collectors_run=[r.collector_id for r in collector_results],
                collector_errors=errors,
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
