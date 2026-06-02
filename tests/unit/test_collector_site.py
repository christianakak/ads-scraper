"""Unit tests for SiteScannerCollector — no browser or network calls."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from verticals.proptech.collectors.site_scanner import (
    _FLOOR_PLAN_PROVIDERS,
    _VIRTUAL_TOUR_PROVIDERS,
    SiteScannerCollector,
    _classify_cta_text,
    _classify_pricing,
    _detect_tech_stack,
    _match_provider,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings_dummy():
    s = MagicMock()
    s.browserless_token = ""
    s.use_local_browser = False
    s.site_scanner_dummy_mode = True
    s.google_pagespeed_api_key = ""
    return s


@pytest.fixture
def settings_no_browser():
    s = MagicMock()
    s.browserless_token = ""
    s.use_local_browser = False
    s.site_scanner_dummy_mode = False
    s.google_pagespeed_api_key = ""
    return s


@pytest.fixture
def scale_up_fixture():
    return json.loads((FIXTURES / "site_scanner_scale_up.json").read_text())


@pytest.fixture
def premium_fixture():
    return json.loads((FIXTURES / "site_scanner_premium.json").read_text())


@pytest.fixture
def planner_fixture():
    return json.loads((FIXTURES / "site_scanner_planner.json").read_text())


@pytest.fixture
def high_intent_fixture():
    return json.loads((FIXTURES / "site_scanner_high_intent.json").read_text())


# ---------------------------------------------------------------------------
# CTA classification
# ---------------------------------------------------------------------------

class TestClassifyCtaText:
    def test_enquire_now(self):
        assert _classify_cta_text("Enquire Now") == "enquire"

    def test_reserve_plot(self):
        assert _classify_cta_text("Reserve Your Plot") == "reserve"

    def test_book_viewing(self):
        assert _classify_cta_text("Book a Viewing") == "reserve"

    def test_contact_us(self):
        assert _classify_cta_text("Contact Us") == "enquire"

    def test_call_us(self):
        assert _classify_cta_text("Call Us Today") == "call"

    def test_request_details(self):
        assert _classify_cta_text("Request Details") == "enquire"

    def test_download_brochure(self):
        assert _classify_cta_text("Download Brochure") == "enquire"

    def test_swedish_boka(self):
        assert _classify_cta_text("Boka visning") == "reserve"

    def test_empty_returns_unknown(self):
        assert _classify_cta_text("") == "unknown"

    def test_generic_other(self):
        assert _classify_cta_text("View Development") == "other"


# ---------------------------------------------------------------------------
# Pricing classification
# ---------------------------------------------------------------------------

class TestClassifyPricing:
    def test_poa_string(self):
        assert _classify_pricing("poa") == "poa"

    def test_prices_from(self):
        assert _classify_pricing("Prices from £325,000") == "shown"

    def test_none_returns_none(self):
        assert _classify_pricing("") == "none"

    def test_null_returns_none(self):
        assert _classify_pricing(None) == "none"


# ---------------------------------------------------------------------------
# Provider matching
# ---------------------------------------------------------------------------

class TestMatchProvider:
    def test_matterport_url(self):
        assert _match_provider("https://my.matterport.com/show/?m=abc", _VIRTUAL_TOUR_PROVIDERS) == "Matterport"

    def test_giraffe360_floor_plan(self):
        assert _match_provider("https://app.giraffe360.com/embed/xyz", _FLOOR_PLAN_PROVIDERS) == "Giraffe360"

    def test_none_returns_none(self):
        assert _match_provider(None, _FLOOR_PLAN_PROVIDERS) is None

    def test_unknown_url_returns_unknown(self):
        assert _match_provider("https://some-unknown-provider.com/tour", _VIRTUAL_TOUR_PROVIDERS) == "Unknown"


# ---------------------------------------------------------------------------
# Tech stack detection from HTML
# ---------------------------------------------------------------------------

class TestDetectTechStack:
    def test_hubspot_detected(self):
        html = '<script src="https://js.hs-scripts.com/12345.js"></script>'
        result = _detect_tech_stack(html, html.lower())
        assert result["crm"] == "HubSpot"

    def test_facebook_pixel_detected(self):
        html = 'fbq("init"); https://connect.facebook.net/en_US/fbevents.js'
        result = _detect_tech_stack(html, html.lower())
        assert result["has_facebook_pixel"] is True

    def test_gtm_detected(self):
        html = '<script src="https://www.googletagmanager.com/gtm.js?id=GTM-XXX"></script>'
        result = _detect_tech_stack(html, html.lower())
        assert result["has_google_tag_manager"] is True

    def test_intercom_detected_as_chat(self):
        html = 'window.Intercom("boot"); https://js.intercomcdn.com/shim.latest.js'
        result = _detect_tech_stack(html, html.lower())
        assert result["chat_platform"] == "Intercom"

    def test_cookiebot_detected(self):
        html = '<script src="https://consent.cookiebot.com/uc.js"></script>'
        result = _detect_tech_stack(html, html.lower())
        assert result["has_cookie_consent"] is True

    def test_wordpress_detected(self):
        html = '/wp-content/themes/developer-theme/'
        result = _detect_tech_stack(html, html.lower())
        assert result["hosting"] == "WordPress"

    def test_ga4_detected(self):
        html = 'https://www.googletagmanager.com/gtag/js?id=G-XXXXXXX'
        result = _detect_tech_stack(html, html.lower())
        assert result["analytics"] == "Google Analytics 4"

    def test_clean_page_returns_none_values(self):
        html = '<html><body><h1>Hello</h1></body></html>'
        result = _detect_tech_stack(html, html.lower())
        assert result["crm"] is None
        assert result["has_facebook_pixel"] is False
        assert result["has_google_tag_manager"] is False


# ---------------------------------------------------------------------------
# Collector dummy mode — fixture loading
# ---------------------------------------------------------------------------

class TestSiteScannerDummyMode:
    @pytest.mark.asyncio
    async def test_returns_success(self, settings_dummy):
        collector = SiteScannerCollector(settings_dummy)
        result = await collector.collect("developer.co.uk", "uk")
        assert result.success is True
        assert result.collector_id == "site_scanner"

    @pytest.mark.asyncio
    async def test_returns_expected_signals(self, settings_dummy):
        collector = SiteScannerCollector(settings_dummy)
        result = await collector.collect("example.co.uk", "uk")
        expected_keys = [
            "has_interactive_floor_plans", "has_virtual_tour",
            "has_digital_reservation", "cta_type", "pricing_transparency",
            "load_time_ms", "mobile_score", "tech_stack",
        ]
        for key in expected_keys:
            assert key in result.data, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_deterministic_output(self, settings_dummy):
        collector = SiteScannerCollector(settings_dummy)
        r1 = await collector.collect("same-domain.co.uk", "uk")
        r2 = await collector.collect("same-domain.co.uk", "uk")
        assert r1.data == r2.data

    @pytest.mark.asyncio
    async def test_different_domains_vary(self, settings_dummy):
        collector = SiteScannerCollector(settings_dummy)
        cta_types = set()
        for domain in ["alpha.co.uk", "beta.co.uk", "gamma.co.uk", "delta.co.uk",
                       "epsilon.co.uk", "zeta.co.uk", "eta.co.uk", "theta.co.uk"]:
            r = await collector.collect(domain, "uk")
            cta_types.add(r.data.get("cta_type"))
        assert len(cta_types) > 1

    @pytest.mark.asyncio
    async def test_no_browser_no_dummy_skips(self, settings_no_browser):
        collector = SiteScannerCollector(settings_no_browser)
        result = await collector.collect("example.co.uk", "uk")
        assert result.success is True
        assert result.data.get("_skipped") == "no_browser_configured"

    @pytest.mark.asyncio
    async def test_swedish_domain_works(self, settings_dummy):
        collector = SiteScannerCollector(settings_dummy)
        result = await collector.collect("bonava.se", "se")
        assert result.success is True


# ---------------------------------------------------------------------------
# Fixture data content validation
# ---------------------------------------------------------------------------

class TestScaleUpFixture:
    def test_no_virtual_tour(self, scale_up_fixture):
        assert scale_up_fixture["has_virtual_tour"] is False

    def test_enquire_cta(self, scale_up_fixture):
        assert scale_up_fixture["cta_type"] == "enquire"

    def test_multiple_projects(self, scale_up_fixture):
        assert scale_up_fixture["project_count"] >= 3

    def test_hubspot_crm(self, scale_up_fixture):
        assert scale_up_fixture["tech_stack"]["crm"] == "HubSpot"

    def test_slow_load_time(self, scale_up_fixture):
        assert scale_up_fixture["load_time_ms"] > 3000

    def test_no_reservation_flow(self, scale_up_fixture):
        assert scale_up_fixture["has_digital_reservation"] is False


class TestPremiumFixture:
    def test_poa_pricing(self, premium_fixture):
        assert premium_fixture["pricing_transparency"] == "poa"

    def test_no_virtual_tour(self, premium_fixture):
        assert premium_fixture["has_virtual_tour"] is False

    def test_single_project(self, premium_fixture):
        assert premium_fixture["project_count"] == 1

    def test_good_performance(self, premium_fixture):
        assert premium_fixture["mobile_score"] > 75


class TestPlannerFixture:
    def test_no_ads_pixel(self, planner_fixture):
        assert planner_fixture["tech_stack"]["has_facebook_pixel"] is False

    def test_no_project_listings(self, planner_fixture):
        assert planner_fixture["project_count"] == 0

    def test_no_pricing(self, planner_fixture):
        assert planner_fixture["pricing_transparency"] == "none"

    def test_no_cookie_consent(self, planner_fixture):
        assert planner_fixture["has_cookie_consent"] is False


class TestHighIntentFixture:
    def test_has_digital_reservation(self, high_intent_fixture):
        assert high_intent_fixture["has_digital_reservation"] is True

    def test_reserve_cta(self, high_intent_fixture):
        assert high_intent_fixture["cta_type"] == "reserve"

    def test_has_chat(self, high_intent_fixture):
        assert high_intent_fixture["has_chat_automation"] is True
        assert high_intent_fixture["chat_provider"] == "Intercom"

    def test_fast_load(self, high_intent_fixture):
        assert high_intent_fixture["load_time_ms"] < 2500
