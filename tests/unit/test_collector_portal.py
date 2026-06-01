"""Unit tests for PortalQualityCollector."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from verticals.proptech.collectors.portal_quality import (
    PortalQualityCollector,
    _compute_quality_score,
    _domain_to_name,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def settings_dummy():
    s = MagicMock()
    s.browserless_token = ""
    s.site_scanner_dummy_mode = True
    return s


class TestDomainToName:
    def test_hyphenated(self):
        assert _domain_to_name("berkeley-homes.co.uk") == "berkeley homes"

    def test_www_stripped(self):
        assert _domain_to_name("www.barratt.co.uk") == "barratt"


class TestComputeQualityScore:
    def test_zero_photos_zero_score(self):
        assert _compute_quality_score({"listing_photo_count": 0}) == 0.0

    def test_many_photos_boosts_score(self):
        assert _compute_quality_score({"listing_photo_count": 18}) > 0.30

    def test_floorplan_adds_score(self):
        base = _compute_quality_score({"listing_photo_count": 10})
        with_fp = _compute_quality_score({"listing_photo_count": 10, "has_floorplan_on_portal": True})
        assert with_fp > base

    def test_max_score_is_one(self):
        score = _compute_quality_score({
            "listing_photo_count": 20,
            "has_floorplan_on_portal": True,
            "has_virtual_tour_on_portal": True,
            "price_shown": True,
            "description_length": 600,
        })
        assert score <= 1.0


class TestPortalQualityDummyMode:
    @pytest.mark.asyncio
    async def test_returns_success(self, settings_dummy):
        collector = PortalQualityCollector(settings_dummy)
        result = await collector.collect("developer.co.uk", "uk")
        assert result.success is True
        assert result.collector_id == "portal_quality"

    @pytest.mark.asyncio
    async def test_has_expected_fields(self, settings_dummy):
        collector = PortalQualityCollector(settings_dummy)
        result = await collector.collect("developer.co.uk", "uk")
        for key in ["portal_listed", "listing_quality_score", "days_on_market"]:
            assert key in result.data

    @pytest.mark.asyncio
    async def test_planner_scenario_not_listed(self, settings_dummy):
        import hashlib
        collector = PortalQualityCollector(settings_dummy)
        for i in range(100):
            domain = f"test{i}.co.uk"
            idx = int(hashlib.md5(domain.encode()).hexdigest(), 16) % 4
            if idx == 2:  # portal_quality_planner.json
                result = await collector.collect(domain, "uk")
                assert result.data["portal_listed"] is False
                break
