"""Unit tests for SupabaseStore — Supabase client fully mocked."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from core.base.schemas import (
    AuditReport,
    CacheMeta,
    Geography,
    ReviewStatus,
    TriageMeta,
    Vertical,
)
from core.store import SupabaseStore, _report_to_row


def _make_report(**kwargs) -> AuditReport:
    defaults = dict(
        domain="developer.co.uk",
        geography=Geography.UK,
        vertical=Vertical.PROPTECH,
        rules_version="1.0.0",
        triage=TriageMeta(review_status=ReviewStatus.AUTO_APPROVED, audit_confidence=0.85),
        cache_meta=CacheMeta(
            collected_at=datetime.now(tz=UTC),
            cache_hit=False,
            collectors_run=["dns_headers"],
        ),
    )
    defaults.update(kwargs)
    return AuditReport(**defaults)


@pytest.fixture
def mock_supabase():
    with patch("core.store.SupabaseStore.__init__", lambda self, url, key, ttl=30: None):
        store = SupabaseStore.__new__(SupabaseStore)
        store._ttl = 30
        store._client = MagicMock()
        yield store


class TestReportToRow:
    def test_basic_fields_present(self):
        report = _make_report()
        row = _report_to_row(report)
        assert row["domain"] == "developer.co.uk"
        assert row["vertical"] == "proptech"
        assert row["geography"] == "uk"
        assert row["rules_version"] == "1.0.0"

    def test_review_status_extracted(self):
        report = _make_report()
        row = _report_to_row(report)
        assert row["review_status"] == "auto_approved"

    def test_full_audit_json_present(self):
        report = _make_report()
        row = _report_to_row(report)
        assert row["full_audit_json"] is not None
        assert row["full_audit_json"]["domain"] == "developer.co.uk"

    def test_null_optional_fields(self):
        report = _make_report()
        row = _report_to_row(report)
        assert row["icp_persona"] is None
        assert row["hook_text"] is None
        assert row["top_pain_signal"] is None


class TestSupabaseStoreGetCached:
    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self, mock_supabase):
        mock_chain = MagicMock()
        mock_chain.execute.return_value = MagicMock(data=[])
        mock_supabase._client.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.gte.return_value \
            .order.return_value.limit.return_value = mock_chain

        result = await mock_supabase.get_cached("example.co.uk", "proptech")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_returns_report(self, mock_supabase):
        report = _make_report()
        report_json = report.model_dump(mode="json")

        mock_chain = MagicMock()
        mock_chain.execute.return_value = MagicMock(data=[{"full_audit_json": report_json}])
        mock_supabase._client.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.gte.return_value \
            .order.return_value.limit.return_value = mock_chain

        result = await mock_supabase.get_cached("developer.co.uk", "proptech")
        assert result is not None
        assert result.domain == "developer.co.uk"


class TestSupabaseStoreUpsert:
    @pytest.mark.asyncio
    async def test_upsert_called_with_row(self, mock_supabase):
        report = _make_report()

        mock_chain = MagicMock()
        mock_chain.execute.return_value = MagicMock(data=[])
        mock_supabase._client.table.return_value.upsert.return_value = mock_chain

        await mock_supabase.upsert_audit(report)
        mock_supabase._client.table.assert_called_with("audits")
        mock_supabase._client.table.return_value.upsert.assert_called_once()


class TestSupabaseStoreOutcome:
    @pytest.mark.asyncio
    async def test_record_outcome_returns_id(self, mock_supabase):
        mock_chain = MagicMock()
        mock_chain.execute.return_value = MagicMock(data=[])
        mock_supabase._client.table.return_value.insert.return_value = mock_chain

        outcome_id = await mock_supabase.record_outcome(
            "audit-123", "meeting_booked", "Great fit"
        )
        assert len(outcome_id) == 36  # UUID format
        mock_supabase._client.table.assert_called_with("outcomes")
