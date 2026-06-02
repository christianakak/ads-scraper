"""Unit tests for AdIntelligenceCollector — Adyntel format, all network calls mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from verticals.proptech.collectors.ad_intelligence import (
    _CTA_TYPE_MAP,
    AdIntelligenceCollector,
    _classify_from_copy,
    _compute_signals,
    _fatigue_score,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings_live():
    s = MagicMock()
    s.adyntel_api_key = "test-key-abc"
    s.adyntel_email = "test@example.com"
    s.adyntel_dummy_mode = False
    return s


@pytest.fixture
def settings_dummy():
    s = MagicMock()
    s.adyntel_api_key = ""
    s.adyntel_email = ""
    s.adyntel_dummy_mode = True
    return s


@pytest.fixture
def settings_no_creds():
    s = MagicMock()
    s.adyntel_api_key = ""
    s.adyntel_email = ""
    s.adyntel_dummy_mode = False
    return s


@pytest.fixture
def scale_up_raw():
    return json.loads((FIXTURES / "adyntel_scale_up.json").read_text())


@pytest.fixture
def premium_raw():
    return json.loads((FIXTURES / "adyntel_premium.json").read_text())


@pytest.fixture
def no_ads_raw():
    return json.loads((FIXTURES / "adyntel_no_ads.json").read_text())


@pytest.fixture
def recently_stopped_raw():
    return json.loads((FIXTURES / "adyntel_recently_stopped.json").read_text())


# ---------------------------------------------------------------------------
# CTA type mapping
# ---------------------------------------------------------------------------

class TestCtaTypeMap:
    def test_contact_us_maps_to_enquire(self):
        assert _CTA_TYPE_MAP["CONTACT_US"] == "enquire"

    def test_book_now_maps_to_reserve(self):
        assert _CTA_TYPE_MAP["BOOK_NOW"] == "reserve"

    def test_sign_up_maps_to_reserve(self):
        assert _CTA_TYPE_MAP["SIGN_UP"] == "reserve"

    def test_learn_more_maps_to_enquire(self):
        assert _CTA_TYPE_MAP["LEARN_MORE"] == "enquire"

    def test_call_now_maps_to_call(self):
        assert _CTA_TYPE_MAP["CALL_NOW"] == "call"


class TestClassifyFromCopy:
    def test_enquire_in_copy(self):
        assert _classify_from_copy("Enquire today about availability") == "enquire"

    def test_reserve_in_copy(self):
        assert _classify_from_copy("Reserve your plot now") == "reserve"

    def test_swedish_reserve(self):
        assert _classify_from_copy("Boka ditt hem idag") == "reserve"

    def test_no_match_returns_none(self):
        assert _classify_from_copy("Beautiful apartments with stunning views") is None


# ---------------------------------------------------------------------------
# Fatigue scoring
# ---------------------------------------------------------------------------

class TestFatigueScore:
    def test_none_returns_zero(self):
        assert _fatigue_score(None) == 0.0

    def test_fresh_creative_low_score(self):
        assert _fatigue_score(5) == 0.10

    def test_medium_fatigue(self):
        assert _fatigue_score(20) == 0.40

    def test_high_fatigue(self):
        assert _fatigue_score(35) == 0.75

    def test_critical_fatigue(self):
        assert _fatigue_score(50) == 0.95


# ---------------------------------------------------------------------------
# Signal computation from Adyntel response
# ---------------------------------------------------------------------------

class TestComputeSignals:
    def test_scale_up_has_active_ads(self, scale_up_raw):
        signals = _compute_signals(scale_up_raw)
        assert signals["has_active_ads"] is True
        assert signals["active_ad_count"] == 5

    def test_scale_up_creative_age_present(self, scale_up_raw):
        signals = _compute_signals(scale_up_raw)
        assert signals["creative_age_days"] is not None
        assert signals["creative_age_days"] > 30  # oldest ad is ~47 days

    def test_scale_up_high_fatigue(self, scale_up_raw):
        signals = _compute_signals(scale_up_raw)
        assert signals["ad_fatigue_score"] >= 0.75

    def test_scale_up_primary_cta_is_enquire(self, scale_up_raw):
        signals = _compute_signals(scale_up_raw)
        # CONTACT_US → enquire
        assert signals["primary_cta_type"] == "enquire"

    def test_scale_up_not_recently_stopped(self, scale_up_raw):
        signals = _compute_signals(scale_up_raw)
        assert signals["recently_stopped_ads"] is False
        assert signals["days_since_stopped"] is None

    def test_scale_up_page_info(self, scale_up_raw):
        signals = _compute_signals(scale_up_raw)
        assert signals["facebook_page_id"] == "112233445566"
        assert signals["facebook_page_name"] == "Meridian Homes UK"

    def test_premium_fresh_creative(self, premium_raw, scale_up_raw):
        premium = _compute_signals(premium_raw)
        scale_up = _compute_signals(scale_up_raw)
        assert premium["creative_age_days"] is not None
        # Premium fixture (~11d old) should be less fatigued than scale-up (~47d old)
        assert premium["ad_fatigue_score"] < scale_up["ad_fatigue_score"]
        assert premium["ad_fatigue_score"] <= 0.40

    def test_no_ads_all_zero(self, no_ads_raw):
        signals = _compute_signals(no_ads_raw)
        assert signals["has_active_ads"] is False
        assert signals["ad_count"] == 0
        assert signals["creative_age_days"] is None
        assert signals["ad_fatigue_score"] == 0.0
        assert signals["spend_tier"] == "NONE"
        assert signals["recently_stopped_ads"] is False

    def test_recently_stopped_detected(self, recently_stopped_raw):
        signals = _compute_signals(recently_stopped_raw)
        assert signals["has_active_ads"] is False
        assert signals["recently_stopped_ads"] is True
        assert signals["ad_count"] == 3
        assert signals["active_ad_count"] == 0
        assert signals["days_since_stopped"] is not None

    def test_recently_stopped_no_active_creative_age(self, recently_stopped_raw):
        signals = _compute_signals(recently_stopped_raw)
        # creative_age_days only applies to ACTIVE ads
        assert signals["creative_age_days"] is None

    def test_cta_deduplication(self, scale_up_raw):
        signals = _compute_signals(scale_up_raw)
        # Should not have duplicates
        assert len(signals["cta_types"]) == len(set(signals["cta_types"]))

    def test_landing_page_domains_extracted(self, scale_up_raw):
        signals = _compute_signals(scale_up_raw)
        assert len(signals["landing_page_domains"]) > 0
        assert all("." in d for d in signals["landing_page_domains"])


# ---------------------------------------------------------------------------
# Collector integration
# ---------------------------------------------------------------------------

class TestAdIntelligenceCollector:
    @pytest.mark.asyncio
    async def test_no_credentials_returns_skipped(self, settings_no_creds):
        collector = AdIntelligenceCollector(settings_no_creds)
        result = await collector.collect("developer.co.uk", "uk")
        assert result.success is True
        assert result.data.get("_skipped") == "no_credentials"

    @pytest.mark.asyncio
    async def test_dummy_mode_returns_data(self, settings_dummy):
        collector = AdIntelligenceCollector(settings_dummy)
        result = await collector.collect("example.co.uk", "uk")
        assert result.success is True
        assert "has_active_ads" in result.data
        assert "ad_fatigue_score" in result.data
        assert "primary_cta_type" in result.data

    @pytest.mark.asyncio
    async def test_dummy_mode_is_deterministic(self, settings_dummy):
        collector = AdIntelligenceCollector(settings_dummy)
        result1 = await collector.collect("same-domain.co.uk", "uk")
        result2 = await collector.collect("same-domain.co.uk", "uk")
        assert result1.data == result2.data

    @pytest.mark.asyncio
    async def test_dummy_mode_different_domains_vary(self, settings_dummy):
        collector = AdIntelligenceCollector(settings_dummy)
        # Use domains that hash to different scenarios
        results = []
        for domain in ["aaa.co.uk", "bbb.co.uk", "ccc.co.uk", "ddd.co.uk",
                       "eee.co.uk", "fff.co.uk", "ggg.co.uk", "hhh.co.uk"]:
            r = await collector.collect(domain, "uk")
            results.append(r.data.get("ad_count", 0))
        # Not all should be identical — different scenarios have different ad counts
        assert len(set(results)) > 1

    @pytest.mark.asyncio
    async def test_dummy_mode_recently_stopped_scenario(self, settings_dummy):
        # Find a domain that hashes to the recently_stopped fixture (index 3)
        import hashlib
        collector = AdIntelligenceCollector(settings_dummy)
        for i in range(100):
            domain = f"test{i}.co.uk"
            idx = int(hashlib.md5(domain.encode()).hexdigest(), 16) % 4
            if idx == 3:  # adyntel_recently_stopped.json
                result = await collector.collect(domain, "uk")
                assert result.data["recently_stopped_ads"] is True
                break

    @pytest.mark.asyncio
    async def test_live_mode_calls_adyntel_api(self, settings_live, scale_up_raw):
        collector = AdIntelligenceCollector(settings_live)
        with patch.object(collector, "_fetch_adyntel", return_value=scale_up_raw) as mock:
            result = await collector.collect("meridian-homes.co.uk", "uk")
        mock.assert_called_once_with("meridian-homes.co.uk", "uk")
        assert result.data["has_active_ads"] is True

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty_gracefully(self, settings_live):
        collector = AdIntelligenceCollector(settings_live)
        with patch.object(collector, "_fetch_adyntel",
                          return_value={"results": [], "number_of_ads": 0, "page_id": None}):
            result = await collector.collect("example.co.uk", "uk")
        assert result.success is True
        assert result.data["has_active_ads"] is False

    @pytest.mark.asyncio
    async def test_collector_id(self, settings_dummy):
        collector = AdIntelligenceCollector(settings_dummy)
        result = await collector.run("example.co.uk", "uk")
        assert result.collector_id == "ad_intelligence"

    @pytest.mark.asyncio
    async def test_swedish_domain_dummy(self, settings_dummy):
        collector = AdIntelligenceCollector(settings_dummy)
        result = await collector.collect("bonava.se", "se")
        assert result.success is True
        assert "has_active_ads" in result.data
