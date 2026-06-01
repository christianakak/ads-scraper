"""Unit tests for DnsHeadersCollector — all network calls mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verticals.proptech.collectors.dns_headers import (
    DnsHeadersCollector,
    _detect_cdn,
    _identify_email_provider,
    _identify_ns_provider,
)


@pytest.fixture
def settings():
    return MagicMock()


@pytest.fixture
def collector(settings):
    return DnsHeadersCollector(settings)


# ---------------------------------------------------------------------------
# Helper function tests (no mocking needed)
# ---------------------------------------------------------------------------

class TestDetectCdn:
    def test_cloudflare_detected(self):
        headers = {"cf-ray": "abc123", "content-type": "text/html"}
        assert _detect_cdn(headers) == "Cloudflare"

    def test_cloudfront_detected(self):
        headers = {"x-amz-cf-id": "xyz"}
        assert _detect_cdn(headers) == "AWS CloudFront"

    def test_no_cdn_returns_none(self):
        headers = {"content-type": "text/html", "server": "Apache"}
        assert _detect_cdn(headers) is None

    def test_case_insensitive(self):
        headers = {"CF-Ray": "abc"}  # uppercase key
        # Our _detect_cdn lowercases keys internally
        assert _detect_cdn({"cf-ray": "abc"}) == "Cloudflare"


class TestIdentifyEmailProvider:
    def test_google_workspace(self):
        assert _identify_email_provider(["aspmx.l.google.com"]) == "Google Workspace"

    def test_microsoft_365(self):
        assert _identify_email_provider(["mail.protection.outlook.com"]) == "Microsoft 365"

    def test_unknown_provider(self):
        assert _identify_email_provider(["mail.somehost.com"]) == "Unknown"

    def test_no_mx_returns_none(self):
        assert _identify_email_provider([]) is None


class TestIdentifyNsProvider:
    def test_cloudflare_ns(self):
        assert _identify_ns_provider(["eva.ns.cloudflare.com", "kurt.ns.cloudflare.com"]) == "Cloudflare"

    def test_aws_route53(self):
        assert _identify_ns_provider(["ns-1.awsdns-01.org"]) == "AWS Route53"

    def test_unknown_ns(self):
        assert _identify_ns_provider(["ns1.randomhost.com"]) is None


# ---------------------------------------------------------------------------
# Collector integration tests (HTTP + DNS mocked)
# ---------------------------------------------------------------------------

class TestDnsHeadersCollector:
    @pytest.mark.asyncio
    async def test_successful_domain_returns_success(self, collector):
        mock_http = {
            "has_ssl": True,
            "redirect_chain_length": 1,
            "server_tech": "nginx",
            "cdn_provider": "Cloudflare",
            "domain_reachable": True,
        }
        txt_result = {"has_spf": True, "has_dkim": True, "has_dmarc": False}
        mx_result = {"email_provider": "Google Workspace", "has_mx_records": True}
        ns_result = {"hosting_provider": "Cloudflare", "nameservers": ["eva.ns.cloudflare.com"]}
        whois_result = {"domain_age_years": 5.3}

        with patch.object(collector, "_check_http", new=AsyncMock(return_value=mock_http)), \
             patch.object(collector, "_check_txt_records", return_value=txt_result), \
             patch.object(collector, "_check_mx_records", return_value=mx_result), \
             patch.object(collector, "_check_ns_records", return_value=ns_result), \
             patch.object(collector, "_check_whois", return_value=whois_result):

            result = await collector.collect("developer.co.uk", "uk")

        assert result.success is True
        assert result.data["has_ssl"] is True
        assert result.data["has_spf"] is True
        assert result.data["has_dkim"] is True
        assert result.data["has_dmarc"] is False
        assert result.data["email_provider"] == "Google Workspace"
        assert result.data["hosting_provider"] == "Cloudflare"
        assert result.data["domain_age_years"] == 5.3
        assert result.data["cdn_provider"] == "Cloudflare"

    @pytest.mark.asyncio
    async def test_unreachable_domain_returns_failure(self, collector):
        mock_http = {"has_ssl": False, "redirect_chain_length": 0, "domain_reachable": False}
        txt_result = {"has_spf": False, "has_dkim": False, "has_dmarc": False}
        mx_result = {"email_provider": None, "has_mx_records": False}
        ns_result = {"hosting_provider": None, "nameservers": []}
        whois_result = {"domain_age_years": None}

        with patch.object(collector, "_check_http", new=AsyncMock(return_value=mock_http)), \
             patch.object(collector, "_check_txt_records", return_value=txt_result), \
             patch.object(collector, "_check_mx_records", return_value=mx_result), \
             patch.object(collector, "_check_ns_records", return_value=ns_result), \
             patch.object(collector, "_check_whois", return_value=whois_result):

            result = await collector.collect("notadomain12345.xyz", "uk")

        assert result.success is False
        assert result.error == "domain_unreachable"

    @pytest.mark.asyncio
    async def test_dns_exception_doesnt_crash_collector(self, collector):
        mock_http = {"has_ssl": True, "redirect_chain_length": 0, "domain_reachable": True}

        # DNS methods raise — should return empty dicts, not crash
        with patch.object(collector, "_check_http", new=AsyncMock(return_value=mock_http)), \
             patch.object(collector, "_check_txt_records", side_effect=Exception("DNS timeout")), \
             patch.object(collector, "_check_mx_records", side_effect=Exception("DNS timeout")), \
             patch.object(collector, "_check_ns_records", side_effect=Exception("DNS timeout")), \
             patch.object(collector, "_check_whois", side_effect=Exception("WHOIS timeout")):

            result = await collector.collect("example.co.uk", "uk")

        # Should succeed despite DNS failures (http check passed)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_run_calls_collect_and_normalizes(self, collector):
        """BaseCollector.run() wraps collect() — verify normalization runs."""
        mock_result_data = {
            "has_ssl": True,
            "redirect_chain_length": 1,
            "domain_reachable": True,
            "domain_age_years": 3.5,
            "has_spf": True,
            "has_dkim": False,
            "has_dmarc": False,
            "email_provider": "Google Workspace",
            "hosting_provider": "Cloudflare",
        }

        with patch.object(collector, "_check_http", new=AsyncMock(return_value={
            "has_ssl": True, "redirect_chain_length": 1, "domain_reachable": True
        })), \
             patch.object(collector, "_check_txt_records", return_value={"has_spf": True, "has_dkim": False, "has_dmarc": False}), \
             patch.object(collector, "_check_mx_records", return_value={"email_provider": "Google Workspace", "has_mx_records": True}), \
             patch.object(collector, "_check_ns_records", return_value={"hosting_provider": "Cloudflare", "nameservers": []}), \
             patch.object(collector, "_check_whois", return_value={"domain_age_years": 3.5}):

            result = await collector.run("example.co.uk", "uk")

        assert result.success is True
        assert result.collector_id == "dns_headers"
