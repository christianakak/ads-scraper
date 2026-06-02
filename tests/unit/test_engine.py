"""Unit tests for DomainAuditor — all network calls are mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.base.schemas import (
    AuditRequest,
    CollectorResult,
    Geography,
    ICPPersona,
    M360Module,
    PainSignal,
    ReviewStatus,
    Severity,
)
from core.engine import DomainAuditor
from core.registry import VerticalRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_registry():
    VerticalRegistry.clear()
    yield
    VerticalRegistry.clear()


@pytest.fixture
def settings():
    return MagicMock()


def _make_collector(collector_id: str, data: dict, success: bool = True, is_screening: bool = False):
    """Factory: returns a collector class that returns the given data."""

    class MockCollector:
        pass

    MockCollector.collector_id = collector_id
    MockCollector.is_screening = is_screening

    async def run(self, domain, geography):
        return CollectorResult(
            collector_id=collector_id,
            domain=domain,
            success=success,
            error=None if success else "mock error",
            data=data,
        )

    MockCollector.run = run
    MockCollector.__init__ = lambda self, settings: None
    return MockCollector


def _make_signal(signal_id: str, confidence: float, severity: Severity = Severity.HIGH) -> PainSignal:
    return PainSignal(
        signal_id=signal_id,
        severity=severity,
        confidence=confidence,
        detected_value={"test": True},
        business_pain="Test pain",
        emotional_trigger="Test trigger",
        m360_module=M360Module.JOURNEY,
        hook_angle="velocity",
        icp_fit=[ICPPersona.SCALE_UP_DEVELOPER],
    )


def _make_analyzer(signals: list[PainSignal]):
    class MockAnalyzer:
        def __init__(self, rules_path):
            pass

        def _load_rules(self):
            return {}

        def analyze(self, collector_results):
            return signals

    return MockAnalyzer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDomainAuditorBasic:
    @pytest.mark.asyncio
    async def test_empty_vertical_returns_empty_audit(self, settings):
        VerticalRegistry.register("proptech", [], [], "rules/", "1.0.0")
        auditor = DomainAuditor(settings)
        request = AuditRequest(domain="example.co.uk", geography=Geography.UK)

        report = await auditor.audit(request)

        assert report.domain == "example.co.uk"
        assert report.pain_signals == []
        assert report.triage is not None

    @pytest.mark.asyncio
    async def test_collector_data_appears_in_output(self, settings):
        collector = _make_collector("test_col", {"has_virtual_tour": False})
        VerticalRegistry.register("proptech", [collector], [], "rules/", "1.0.0")
        auditor = DomainAuditor(settings)
        request = AuditRequest(domain="example.co.uk", geography=Geography.UK)

        report = await auditor.audit(request)

        assert "test_col" in report.raw_collector_output
        assert report.cache_meta.collectors_run == ["test_col"]

    @pytest.mark.asyncio
    async def test_pain_signals_appear_in_report(self, settings):
        signals = [_make_signal("stale_creative", 0.9)]
        analyzer = _make_analyzer(signals)
        VerticalRegistry.register("proptech", [], [analyzer], "rules/", "1.0.0")
        auditor = DomainAuditor(settings)
        request = AuditRequest(domain="example.co.uk", geography=Geography.UK)

        report = await auditor.audit(request)

        assert len(report.pain_signals) == 1
        assert report.pain_signals[0].signal_id == "stale_creative"


class TestScreeningGate:
    @pytest.mark.asyncio
    async def test_screening_failure_returns_early(self, settings):
        screener = _make_collector("dns_headers", {}, success=False, is_screening=True)
        expensive = _make_collector("site_scanner", {"data": "should_not_run"})
        VerticalRegistry.register("proptech", [screener, expensive], [], "rules/", "1.0.0")
        auditor = DomainAuditor(settings)
        request = AuditRequest(domain="broken.co.uk", geography=Geography.UK)

        report = await auditor.audit(request)

        assert report.triage.review_status == ReviewStatus.FLAGGED
        assert report.triage.review_reason == "domain_unreachable"
        # Expensive collector should NOT appear
        assert "site_scanner" not in report.raw_collector_output

    @pytest.mark.asyncio
    async def test_screening_success_runs_remaining_collectors(self, settings):
        screener = _make_collector("dns_headers", {"domain_age_years": 3.0}, is_screening=True)
        collector2 = _make_collector("ad_intelligence", {"creative_age_days": 47})
        VerticalRegistry.register("proptech", [screener, collector2], [], "rules/", "1.0.0")
        auditor = DomainAuditor(settings)
        request = AuditRequest(domain="example.co.uk", geography=Geography.UK)

        report = await auditor.audit(request)

        assert "dns_headers" in report.raw_collector_output
        assert "ad_intelligence" in report.raw_collector_output


class TestTriageThresholds:
    @pytest.mark.asyncio
    async def test_high_confidence_auto_approved(self, settings):
        signals = [_make_signal("s1", 0.95), _make_signal("s2", 0.90)]
        VerticalRegistry.register("proptech", [], [_make_analyzer(signals)], "rules/", "1.0.0")
        auditor = DomainAuditor(settings)

        report = await auditor.audit(AuditRequest(domain="x.co.uk", geography=Geography.UK))

        assert report.triage.review_status == ReviewStatus.AUTO_APPROVED

    @pytest.mark.asyncio
    async def test_medium_confidence_pending_review(self, settings):
        signals = [_make_signal("s1", 0.72), _make_signal("s2", 0.68)]
        VerticalRegistry.register("proptech", [], [_make_analyzer(signals)], "rules/", "1.0.0")
        auditor = DomainAuditor(settings)

        report = await auditor.audit(AuditRequest(domain="x.co.uk", geography=Geography.UK))

        assert report.triage.review_status == ReviewStatus.PENDING_REVIEW

    @pytest.mark.asyncio
    async def test_low_confidence_flagged(self, settings):
        signals = [_make_signal("s1", 0.45), _make_signal("s2", 0.40)]
        VerticalRegistry.register("proptech", [], [_make_analyzer(signals)], "rules/", "1.0.0")
        auditor = DomainAuditor(settings)

        report = await auditor.audit(AuditRequest(domain="x.co.uk", geography=Geography.UK))

        assert report.triage.review_status == ReviewStatus.FLAGGED


class TestPartialFailure:
    @pytest.mark.asyncio
    async def test_failed_collector_does_not_crash_audit(self, settings):
        good = _make_collector("dns_headers", {"domain_age_years": 2.0})
        bad = _make_collector("ad_intelligence", {}, success=False)
        VerticalRegistry.register("proptech", [good, bad], [], "rules/", "1.0.0")
        auditor = DomainAuditor(settings)

        report = await auditor.audit(AuditRequest(domain="x.co.uk", geography=Geography.UK))

        assert report.cache_meta is not None
        assert len(report.cache_meta.collector_errors) == 1
        assert report.cache_meta.collector_errors[0]["collector"] == "ad_intelligence"


class TestCacheHit:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_without_running_collectors(self, settings):
        cached_report = MagicMock()
        store = MagicMock()
        store.get_cached = AsyncMock(return_value=cached_report)

        collector = _make_collector("expensive", {"data": "should_not_run"})
        VerticalRegistry.register("proptech", [collector], [], "rules/", "1.0.0")
        auditor = DomainAuditor(settings, store=store)

        result = await auditor.audit(AuditRequest(domain="cached.co.uk", geography=Geography.UK))

        assert result is cached_report

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(self, settings):
        store = MagicMock()
        store.get_cached = AsyncMock(return_value=None)
        store.upsert_audit = AsyncMock()

        VerticalRegistry.register("proptech", [], [], "rules/", "1.0.0")
        auditor = DomainAuditor(settings, store=store)

        await auditor.audit(
            AuditRequest(domain="x.co.uk", geography=Geography.UK, force_refresh=True)
        )

        store.get_cached.assert_not_called()
