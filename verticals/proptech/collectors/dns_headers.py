"""
DNS/Headers Collector — Phase 0 Screening Layer

Runs first (~1s), zero browser dependency.
If this collector fails (domain unreachable), DomainAuditor returns early.
All remaining collectors only fire if this one succeeds.

Signals produced:
  domain_age_years, hosting_provider, email_provider,
  has_spf, has_dkim, has_dmarc, cdn_provider, has_ssl,
  redirect_chain_length, server_tech
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import dns.exception
import dns.resolver
import httpx
import whois

from core.base.collector import BaseCollector
from core.base.schemas import CollectorResult
from core.normalizer import NormalizationMixin

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

_CDN_HEADERS: dict[str, str] = {
    "cf-ray": "Cloudflare",
    "x-amz-cf-id": "AWS CloudFront",
    "x-azure-ref": "Azure CDN",
    "x-fastly-request-id": "Fastly",
    "x-served-by": "Fastly",
    "x-cache-hits": "Varnish",
    "x-litespeed-cache": "LiteSpeed",
    "x-sucuri-id": "Sucuri",
}

_EMAIL_PROVIDERS: dict[str, str] = {
    "google": "Google Workspace",
    "googlemail": "Google Workspace",
    "outlook": "Microsoft 365",
    "microsoft": "Microsoft 365",
    "protection.outlook": "Microsoft 365",
    "zoho": "Zoho Mail",
    "mimecast": "Mimecast",
    "proofpoint": "Proofpoint",
    "mailprotect": "Mail Protect",
    "spamexperts": "SpamExperts",
    "messagelabs": "Symantec Email",
}

_NS_PROVIDERS: dict[str, str] = {
    "cloudflare": "Cloudflare",
    "awsdns": "AWS Route53",
    "azure-dns": "Azure DNS",
    "googledomains": "Google Domains",
    "domaincontrol": "GoDaddy",
    "registrar-servers": "Namecheap",
    "netlify": "Netlify",
    "vercel-dns": "Vercel",
    "name.com": "Name.com",
}

_HTTP_TIMEOUT = 15.0
_DNS_TIMEOUT = 5.0


class DnsHeadersCollector(NormalizationMixin, BaseCollector):
    collector_id = "dns_headers"
    is_screening = True

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._resolver = dns.resolver.Resolver()
        self._resolver.timeout = _DNS_TIMEOUT
        self._resolver.lifetime = _DNS_TIMEOUT * 2

    async def collect(self, domain: str, geography: str) -> CollectorResult:
        loop = asyncio.get_event_loop()

        # Run all checks concurrently — DNS in thread executor (blocking),
        # HTTP check via async httpx
        http_task = self._check_http(domain)
        dns_tasks = asyncio.gather(
            loop.run_in_executor(None, self._check_txt_records, domain),
            loop.run_in_executor(None, self._check_mx_records, domain),
            loop.run_in_executor(None, self._check_ns_records, domain),
            loop.run_in_executor(None, self._check_whois, domain),
            return_exceptions=True,
        )

        (http_data, dns_results) = await asyncio.gather(http_task, dns_tasks)
        txt_data, mx_data, ns_data, whois_data = [
            r if not isinstance(r, Exception) else {}
            for r in dns_results
        ]

        data: dict[str, Any] = {}
        data.update(http_data)
        data.update(txt_data)
        data.update(mx_data)
        data.update(ns_data)
        data.update(whois_data)

        # Screening: fail if domain is unreachable
        if not data.get("has_ssl") and data.get("redirect_chain_length", 0) == 0:
            return CollectorResult(
                collector_id=self.collector_id,
                domain=domain,
                success=False,
                error="domain_unreachable",
                data=data,
                data_source="real",
            )

        return CollectorResult(
            collector_id=self.collector_id,
            domain=domain,
            success=True,
            data=data,
            data_source="real",
        )

    # ------------------------------------------------------------------
    # HTTP signals
    # ------------------------------------------------------------------

    async def _check_http(self, domain: str) -> dict[str, Any]:
        url = f"https://{domain}"
        redirect_count = 0
        has_ssl = True
        server_tech: str | None = None
        cdn_provider: str | None = None

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=_HTTP_TIMEOUT,
                verify=True,
            ) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; GTMBot/1.0)"},
                )
                redirect_count = len(response.history)
                server_tech = response.headers.get("server") or response.headers.get("x-powered-by")
                cdn_provider = _detect_cdn(dict(response.headers))

        except httpx.ConnectError:
            # Try http:// fallback
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=_HTTP_TIMEOUT) as client:
                    response = await client.get(f"http://{domain}")
                    redirect_count = len(response.history)
                    has_ssl = False
                    server_tech = response.headers.get("server")
                    cdn_provider = _detect_cdn(dict(response.headers))
            except Exception:
                return {"has_ssl": False, "redirect_chain_length": 0, "domain_reachable": False}

        except httpx.SSLError:
            has_ssl = False
        except Exception:
            return {"has_ssl": False, "redirect_chain_length": 0, "domain_reachable": False}

        return {
            "has_ssl": has_ssl,
            "redirect_chain_length": redirect_count,
            "server_tech": server_tech,
            "cdn_provider": cdn_provider,
            "domain_reachable": True,
        }

    # ------------------------------------------------------------------
    # DNS signals (all synchronous — run in executor)
    # ------------------------------------------------------------------

    def _check_txt_records(self, domain: str) -> dict[str, Any]:
        has_spf = False
        has_dkim = False
        has_dmarc = False

        # SPF
        try:
            answers = self._resolver.resolve(domain, "TXT")
            for rdata in answers:
                for string in rdata.strings:
                    txt = string.decode("utf-8", errors="ignore")
                    if txt.startswith("v=spf1"):
                        has_spf = True
        except (dns.exception.DNSException, Exception):
            pass

        # DKIM (check common selector)
        for selector in ("default", "google", "mail", "selector1", "selector2", "dkim"):
            try:
                self._resolver.resolve(f"{selector}._domainkey.{domain}", "TXT")
                has_dkim = True
                break
            except (dns.exception.DNSException, Exception):
                continue

        # DMARC
        try:
            self._resolver.resolve(f"_dmarc.{domain}", "TXT")
            has_dmarc = True
        except (dns.exception.DNSException, Exception):
            pass

        return {"has_spf": has_spf, "has_dkim": has_dkim, "has_dmarc": has_dmarc}

    def _check_mx_records(self, domain: str) -> dict[str, Any]:
        try:
            answers = self._resolver.resolve(domain, "MX")
            exchanges = [str(r.exchange).lower().rstrip(".") for r in answers]
            email_provider = _identify_email_provider(exchanges)
            return {"email_provider": email_provider, "has_mx_records": True}
        except (dns.exception.DNSException, Exception):
            return {"email_provider": None, "has_mx_records": False}

    def _check_ns_records(self, domain: str) -> dict[str, Any]:
        try:
            answers = self._resolver.resolve(domain, "NS")
            nameservers = [str(r).lower().rstrip(".") for r in answers]
            provider = _identify_ns_provider(nameservers)
            return {"hosting_provider": provider, "nameservers": nameservers[:3]}
        except (dns.exception.DNSException, Exception):
            return {"hosting_provider": None, "nameservers": []}

    def _check_whois(self, domain: str) -> dict[str, Any]:
        try:
            info = whois.whois(domain)
            creation_date = info.creation_date
            if isinstance(creation_date, list):
                creation_date = creation_date[0]
            if creation_date:
                if isinstance(creation_date, datetime):
                    age = (datetime.utcnow() - creation_date).days / 365.25
                else:
                    age = None
                return {"domain_age_years": round(age, 2) if age else None}
        except Exception:
            pass
        return {"domain_age_years": None}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_cdn(headers: dict[str, str]) -> str | None:
    lower_headers = {k.lower(): v for k, v in headers.items()}
    for header_key, provider in _CDN_HEADERS.items():
        if header_key in lower_headers:
            return provider
    return None


def _identify_email_provider(exchanges: list[str]) -> str | None:
    combined = " ".join(exchanges)
    for keyword, provider in _EMAIL_PROVIDERS.items():
        if keyword in combined:
            return provider
    return "Unknown" if exchanges else None


def _identify_ns_provider(nameservers: list[str]) -> str | None:
    combined = " ".join(nameservers)
    for keyword, provider in _NS_PROVIDERS.items():
        if keyword in combined:
            return provider
    return None
