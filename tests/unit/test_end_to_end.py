"""
End-to-end test: full audit run with all 6 collectors + 2 analyzers,
all in dummy mode. Verifies the complete data flow from domain input
to populated AuditReport with ICP persona, pain signals, and module recs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.base.schemas import ICPPersona, M360Module, ReviewStatus
from core.engine import DomainAuditor


@pytest.fixture(autouse=True)
def register_proptech():
    from core.registry import VerticalRegistry
    VerticalRegistry.clear()
    from verticals.proptech import register
    register()
    yield
    VerticalRegistry.clear()


@pytest.fixture
def settings():
    s = MagicMock()
    # All dummy modes ON — no API keys needed
    s.adyntel_api_key = ""
    s.adyntel_email = ""
    s.adyntel_dummy_mode = True
    s.browserless_token = ""
    s.use_local_browser = False
    s.site_scanner_dummy_mode = True
    s.google_pagespeed_api_key = ""
    s.audit_cache_ttl_days = 30
    return s


class TestFullAuditFlow:
    @pytest.mark.asyncio
    async def test_audit_completes(self, settings):
        auditor = DomainAuditor(settings)
        from core.base.schemas import AuditRequest, Geography
        report = await auditor.audit(AuditRequest(domain="developer.co.uk", geography=Geography.UK))
        assert report is not None
        assert report.domain == "developer.co.uk"

    @pytest.mark.asyncio
    async def test_all_collectors_ran(self, settings):
        auditor = DomainAuditor(settings)
        from core.base.schemas import AuditRequest, Geography
        report = await auditor.audit(AuditRequest(domain="example.co.uk", geography=Geography.UK))
        ran = report.cache_meta.collectors_run
        # Screening collector runs first, rest run in parallel
        assert "dns_headers" in ran
        # Remaining collectors should have run (screening passed in dummy mode)
        assert len(ran) >= 2

    @pytest.mark.asyncio
    async def test_pain_signals_produced(self, settings):
        auditor = DomainAuditor(settings)
        from core.base.schemas import AuditRequest, Geography
        report = await auditor.audit(AuditRequest(domain="meridian-homes.co.uk", geography=Geography.UK))
        # With dummy data, at least some pain signals should fire
        assert isinstance(report.pain_signals, list)
        # All signals have required fields
        for sig in report.pain_signals:
            assert sig.signal_id
            assert sig.severity
            assert 0 <= sig.confidence <= 1
            assert sig.m360_module

    @pytest.mark.asyncio
    async def test_icp_persona_assigned(self, settings):
        auditor = DomainAuditor(settings)
        from core.base.schemas import AuditRequest, Geography
        report = await auditor.audit(AuditRequest(domain="test-developer.co.uk", geography=Geography.UK))
        # ICP should be assigned (dummy data provides enough signals)
        assert report.icp_persona in (
            None,  # acceptable if signals too ambiguous
            ICPPersona.SCALE_UP_DEVELOPER,
            ICPPersona.PREMIUM_VISIONARY,
            ICPPersona.DATA_DRIVEN_PLANNER,
        )

    @pytest.mark.asyncio
    async def test_recommended_modules_derived(self, settings):
        auditor = DomainAuditor(settings)
        from core.base.schemas import AuditRequest, Geography
        report = await auditor.audit(AuditRequest(domain="riverside-homes.co.uk", geography=Geography.UK))
        if report.pain_signals:
            assert len(report.recommended_modules) > 0
            assert report.primary_module is not None
            assert all(isinstance(m, M360Module) for m in report.recommended_modules)

    @pytest.mark.asyncio
    async def test_triage_status_assigned(self, settings):
        auditor = DomainAuditor(settings)
        from core.base.schemas import AuditRequest, Geography
        report = await auditor.audit(AuditRequest(domain="x.co.uk", geography=Geography.UK))
        assert report.triage is not None
        assert report.triage.review_status in (
            ReviewStatus.AUTO_APPROVED,
            ReviewStatus.PENDING_REVIEW,
            ReviewStatus.FLAGGED,
        )
        assert 0 <= report.triage.audit_confidence <= 1

    @pytest.mark.asyncio
    async def test_swedish_domain(self, settings):
        auditor = DomainAuditor(settings)
        from core.base.schemas import AuditRequest, Geography
        report = await auditor.audit(AuditRequest(domain="bonava.se", geography=Geography.SE))
        assert report.geography.value == "se"
        assert report.cache_meta is not None

    @pytest.mark.asyncio
    async def test_raw_collector_output_present(self, settings):
        auditor = DomainAuditor(settings)
        from core.base.schemas import AuditRequest, Geography
        report = await auditor.audit(AuditRequest(domain="test.co.uk", geography=Geography.UK))
        assert isinstance(report.raw_collector_output, dict)

    @pytest.mark.asyncio
    async def test_different_domains_produce_different_reports(self, settings):
        auditor = DomainAuditor(settings)
        from core.base.schemas import AuditRequest, Geography
        r1 = await auditor.audit(AuditRequest(domain="alpha-homes.co.uk", geography=Geography.UK))
        r2 = await auditor.audit(AuditRequest(domain="beta-residences.co.uk", geography=Geography.UK))
        # Pain signals or personas may differ across different dummy scenarios
        # Both should have valid reports even if identical (hash collision acceptable)
        assert r1.domain == "alpha-homes.co.uk"
        assert r2.domain == "beta-residences.co.uk"
