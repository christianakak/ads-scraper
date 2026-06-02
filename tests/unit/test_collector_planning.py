"""Unit tests for PlanningIntelCollector."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from verticals.proptech.collectors.planning_intel import (
    PlanningIntelCollector,
    _build_signals,
    _extract_unit_count,
    _parse_date,
)


@pytest.fixture
def settings_dummy():
    s = MagicMock()
    s.site_scanner_dummy_mode = True
    return s


class TestExtractUnitCount:
    def test_dwellings(self):
        assert _extract_unit_count("Erection of 48 dwellings") == 48

    def test_apartments(self):
        assert _extract_unit_count("construction of 124 apartments") == 124

    def test_no_number(self):
        assert _extract_unit_count("residential development") is None


class TestParseDate:
    def test_iso_format(self):
        dt = _parse_date("2026-04-14")
        assert dt is not None
        assert dt.year == 2026

    def test_uk_format(self):
        dt = _parse_date("14/04/2026")
        assert dt is not None

    def test_invalid(self):
        assert _parse_date("not a date") is None


class TestBuildSignals:
    def test_empty_apps(self):
        signals = _build_signals([], {})
        assert signals["development_stage"] == "unknown"
        assert signals["planning_granted_date"] is None

    def test_recent_approval_is_pre_launch(self):
        apps = [{
            "reference": "2026/001",
            "address": "Test St",
            "description": "72 apartments",
            "decision": "APPROVED",
            "decision_date": "2026-05-01",
            "unit_count": 72,
        }]
        signals = _build_signals(apps, {})
        assert signals["development_stage"] == "pre_launch"
        assert signals["estimated_unit_count"] == 72


class TestPlanningDummyMode:
    @pytest.mark.asyncio
    async def test_returns_success(self, settings_dummy):
        collector = PlanningIntelCollector(settings_dummy)
        result = await collector.collect("developer.co.uk", "uk")
        assert result.success is True
        assert "development_stage" in result.data

    @pytest.mark.asyncio
    async def test_deterministic(self, settings_dummy):
        collector = PlanningIntelCollector(settings_dummy)
        r1 = await collector.collect("same.co.uk", "uk")
        r2 = await collector.collect("same.co.uk", "uk")
        assert r1.data == r2.data
