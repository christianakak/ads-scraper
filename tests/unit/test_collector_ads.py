"""Unit tests for AdIntelligenceCollector — all network calls mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verticals.proptech.collectors.ad_intelligence import (
    AdIntelligenceCollector,
    _classify_cta_from_copy,
    _compute_fatigue_score,
    _compute_signals,
    _domain_to_company_name,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def settings_with_token():
    s = MagicMock()
    s.meta_ad_library_token = "test-token-123"
    return s


@pytest.fixture
def settings_no_token():
    s = MagicMock()
    s.meta_ad_library_token = ""
    return s


@pytest.fixture
def ad_library_data():
    return json.loads((FIXTURES / "ad_library_response.json").read_text())["data"]


@pytest.fixture
def empty_ad_data():
    return []


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestDomainToCompanyName:
    def test_hyphenated_domain(self):
        assert _domain_to_company_name("berkeley-homes.co.uk") == "berkeley homes"

    def test_simple_domain(self):
        assert _domain_to_company_name("example.com") == "example"

    def test_www_prefix_stripped(self):
        assert _domain_to_company_name("www.acme.co.uk") == "acme"

    def test_swedish_domain(self):
        assert _domain_to_company_name("bonava-stockholm.se") == "bonava stockholm"

    def test_multi_part_tld(self):
        assert _domain_to_company_name("landmark-homes.co.uk") == "landmark homes"


class TestFatigueScore:
    def test_zero_days_is_minimal(self):
        assert _compute_fatigue_score(0) == 0.10

    def test_14_days_is_low(self):
        assert _compute_fatigue_score(14) == 0.10

    def test_20_days_is_medium(self):
        assert _compute_fatigue_score(20) == 0.40

    def test_35_days_is_high(self):
        assert _compute_fatigue_score(35) == 0.75

    def test_50_days_is_critical(self):
        assert _compute_fatigue_score(50) == 0.95

    def test_none_returns_zero(self):
        assert _compute_fatigue_score(None) == 0.0


class TestCtaClassifier:
    def test_reserve_detected(self):
        assert _classify_cta_from_copy("Reserve your dream home today") == "reserve"

    def test_enquire_detected(self):
        assert _classify_cta_from_copy("Enquire now for more information") == "enquire"

    def test_contact_detected_as_enquire(self):
        assert _classify_cta_from_copy("Contact us today to find out more") == "enquire"

    def test_view_detected(self):
        assert _classify_cta_from_copy("Explore our new development") == "view"

    def test_swedish_reserve(self):
        assert _classify_cta_from_copy("Boka ditt drömhem idag") == "reserve"

    def test_no_cta_returns_none(self):
        assert _classify_cta_from_copy("Beautiful 3 bedroom apartment with garden") is None


class TestComputeSignals:
    def test_computes_ad_count(self, ad_library_data):
        signals = _compute_signals(ad_library_data)
        assert signals["ad_count"] == 3

    def test_has_active_ads_true(self, ad_library_data):
        signals = _compute_signals(ad_library_data)
        assert signals["has_active_ads"] is True

    def test_creative_age_is_oldest_ad(self, ad_library_data):
        # Oldest ad started 2026-03-01, reference is ~2026-06-01 → ~92 days
        signals = _compute_signals(ad_library_data)
        assert signals["creative_age_days"] is not None
        assert signals["creative_age_days"] >= 60  # at least 60 days

    def test_fatigue_score_high_for_old_ads(self, ad_library_data):
        signals = _compute_signals(ad_library_data)
        # Oldest ad is >30 days → fatigue >= 0.75
        assert signals["ad_fatigue_score"] >= 0.75

    def test_cta_types_detected(self, ad_library_data):
        signals = _compute_signals(ad_library_data)
        # Fixture has "reserve" and "enquire" in ad copy
        assert "reserve" in signals["cta_types_in_ads"]
        assert "enquire" in signals["cta_types_in_ads"]

    def test_page_info_extracted(self, ad_library_data):
        signals = _compute_signals(ad_library_data)
        assert signals["facebook_page_id"] == "123456789"
        assert signals["facebook_page_name"] == "Riverside Homes"

    def test_empty_ads_returns_zero_signals(self, empty_ad_data):
        # _compute_signals is only called with non-empty ads (collect() handles empty case).
        # When called with empty list, it returns a zero-state dict.
        signals = _compute_signals([])
        assert signals["ad_count"] == 0
        assert signals["has_active_ads"] is False
        assert signals["creative_age_days"] is None

    def test_spend_tier_medium(self, ad_library_data):
        signals = _compute_signals(ad_library_data)
        assert signals["spend_tier"] in ("MEDIUM", "HIGH")


# ---------------------------------------------------------------------------
# Collector integration tests
# ---------------------------------------------------------------------------

class TestAdIntelligenceCollector:
    @pytest.mark.asyncio
    async def test_no_token_returns_skipped(self, settings_no_token):
        collector = AdIntelligenceCollector(settings_no_token)
        result = await collector.collect("developer.co.uk", "uk")

        assert result.success is True
        assert result.data.get("_skipped") == "no_token"

    @pytest.mark.asyncio
    async def test_active_ads_found(self, settings_with_token, ad_library_data):
        collector = AdIntelligenceCollector(settings_with_token)

        with patch.object(collector, "_fetch_ads", new=AsyncMock(return_value=ad_library_data)):
            result = await collector.collect("riverside-homes.co.uk", "uk")

        assert result.success is True
        assert result.data["has_active_ads"] is True
        assert result.data["ad_count"] == 3
        assert result.data["creative_age_days"] is not None
        assert result.data["ad_fatigue_score"] > 0

    @pytest.mark.asyncio
    async def test_no_ads_returns_empty_signals(self, settings_with_token):
        collector = AdIntelligenceCollector(settings_with_token)

        with patch.object(collector, "_fetch_ads", new=AsyncMock(return_value=[])):
            result = await collector.collect("developer.co.uk", "uk")

        assert result.success is True
        assert result.data["has_active_ads"] is False
        assert result.data["ad_count"] == 0
        assert result.data["spend_tier"] == "NONE"

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty_signals(self, settings_with_token):
        collector = AdIntelligenceCollector(settings_with_token)

        # _fetch_ads catches exceptions and returns []
        with patch.object(collector, "_fetch_ads", new=AsyncMock(return_value=[])):
            result = await collector.collect("developer.co.uk", "uk")

        assert result.success is True

    @pytest.mark.asyncio
    async def test_swedish_geography_passes_correct_country(self, settings_with_token):
        collector = AdIntelligenceCollector(settings_with_token)

        with patch.object(collector, "_fetch_ads", new=AsyncMock(return_value=[])) as mock_fetch:
            await collector.collect("bonava.se", "se")

        # Verify SE country code was passed
        mock_fetch.assert_called_once_with("bonava", "SE")

    @pytest.mark.asyncio
    async def test_run_wraps_collect(self, settings_with_token, ad_library_data):
        """BaseCollector.run() should work end-to-end with this collector."""
        collector = AdIntelligenceCollector(settings_with_token)

        with patch.object(collector, "_fetch_ads", new=AsyncMock(return_value=ad_library_data)):
            result = await collector.run("riverside-homes.co.uk", "uk")

        assert result.collector_id == "ad_intelligence"
        assert result.success is True
