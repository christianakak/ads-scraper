"""
Site Scanner Collector

Runs a full headless browser audit of the developer's website.
Production: connects to Browserless.io via Playwright CDP.
POC / dummy mode: returns deterministic fixture data based on domain hash.

Signals produced:
  has_interactive_floor_plans, floor_plan_provider,
  has_virtual_tour, virtual_tour_provider,
  has_digital_reservation, reservation_url_pattern,
  cta_type, cta_text,
  pricing_transparency, price_range_text,
  project_count, has_chat_automation, chat_provider,
  has_cookie_consent, cookie_consent_provider,
  content_freshness_days,
  load_time_ms, mobile_score, desktop_score (from PageSpeed API),
  tech_stack (Wappalyzer — crm, analytics, pixels, hosting)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx

from core.base.collector import BaseCollector
from core.base.schemas import CollectorResult
from core.normalizer import NormalizationMixin

_FIXTURES_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures"
_DUMMY_SCENARIOS = [
    "site_scanner_scale_up.json",
    "site_scanner_premium.json",
    "site_scanner_planner.json",
    "site_scanner_high_intent.json",
]

_PAGESPEED_BASE = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# iFrame src patterns → provider name
_FLOOR_PLAN_PROVIDERS: dict[str, str] = {
    "giraffe360.com":    "Giraffe360",
    "ispyproperty.com":  "iSpy",
    "matterport.com":    "Matterport",
    "plotai.co":         "Plot.ai",
    "floorplanner.com":  "Floorplanner",
    "immoviewer.com":    "Immoviewer",
    "cupix.com":         "Cupix",
}

_VIRTUAL_TOUR_PROVIDERS: dict[str, str] = {
    "matterport.com":   "Matterport",
    "giraffe360.com":   "Giraffe360",
    "eyespy360.com":    "EyeSpy360",
    "kuula.co":         "Kuula",
    "cloudpano.com":    "CloudPano",
    "roundme.com":      "RoundMe",
    "vr-tour":          "VR Tour",
    "3d-tour":          "3D Tour",
    "virtualtour":      "Virtual Tour",
}

_RESERVATION_PATTERNS = [
    "/reserve", "/book", "/reservation", "/buy-now", "/buy_now",
    "/purchase", "/secure-your", "/boka", "/reservera",
]

_CHAT_PROVIDERS: dict[str, str] = {
    "intercom":    "Intercom",
    "drift.com":   "Drift",
    "tidio.com":   "Tidio",
    "crisp.chat":  "Crisp",
    "livechat":    "LiveChat",
    "freshchat":   "Freshchat",
    "zopim":       "Zendesk Chat",
    "tawk.to":     "Tawk.to",
}

_COOKIE_PROVIDERS: dict[str, str] = {
    "onetrust":         "OneTrust",
    "cookiebot":        "Cookiebot",
    "cookieyes":        "CookieYes",
    "usercentrics":     "Usercentrics",
    "cookiefirst":      "CookieFirst",
    "termly":           "Termly",
    "cookieinformation": "Cookie Information",
}

# Wappalyzer category → our internal field
_WAPPALYZER_CRM = {
    "HubSpot", "Salesforce", "Pipedrive", "Zoho CRM",
    "Dynamics 365", "Marketo", "Pardot",
}
_WAPPALYZER_ANALYTICS = {
    "Google Analytics", "Google Analytics 4", "Plausible",
    "Mixpanel", "Heap", "Hotjar", "Matomo",
}
_WAPPALYZER_CHAT = {
    "Intercom", "Drift", "Tidio", "Crisp",
    "LiveChat", "Freshchat", "Zendesk Chat", "Tawk.to",
}
_WAPPALYZER_HOSTING = {
    "Cloudflare", "Vercel", "Netlify", "WordPress",
    "Webflow", "Squarespace", "Wix", "Shopify",
    "AWS", "Azure", "Google Cloud",
}


class SiteScannerCollector(NormalizationMixin, BaseCollector):
    collector_id = "site_scanner"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._browserless_token: str = getattr(settings, "browserless_token", "")
        self._use_local_browser: bool = getattr(settings, "use_local_browser", False)
        self._dummy_mode: bool = getattr(settings, "site_scanner_dummy_mode", True)
        self._pagespeed_key: str = (
            getattr(settings, "google_api_key", "")
            or getattr(settings, "google_pagespeed_api_key", "")
        )

    async def collect(self, domain: str, geography: str) -> CollectorResult:
        has_browser = bool(self._browserless_token) or self._use_local_browser
        url = f"https://{domain}"

        if has_browser:
            playwright_data, pagespeed_data = await asyncio.gather(
                self._playwright_scan(url, geography),
                self._pagespeed_scan(url),
                return_exceptions=True,
            )
            data = {
                **(playwright_data if isinstance(playwright_data, dict) else {}),
                **(pagespeed_data if isinstance(pagespeed_data, dict) else {}),
            }
            return CollectorResult(
                collector_id=self.collector_id,
                domain=domain,
                success=True,
                data=data,
            )

        if self._dummy_mode:
            # PageSpeed still runs in dummy mode — it's free and needs no browser
            pagespeed_data = await self._pagespeed_scan(url)
            dummy = self._load_dummy(domain)
            if pagespeed_data:
                dummy.data.update(pagespeed_data)
            return dummy

        # No browser, no dummy mode — still run PageSpeed
        pagespeed_data = await self._pagespeed_scan(url)
        return CollectorResult(
            collector_id=self.collector_id,
            domain=domain,
            success=True,
            data=pagespeed_data or {"_skipped": "no_browser_configured"},
        )

    # ------------------------------------------------------------------
    # Playwright scan (live)
    # ------------------------------------------------------------------

    async def _playwright_scan(self, url: str, geography: str) -> dict[str, Any]:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            if self._browserless_token:
                browser = await p.chromium.connect_over_cdp(
                    f"wss://chrome.browserless.io?token={self._browserless_token}"
                )
            else:
                browser = await p.chromium.launch(headless=True)

            context = await browser.new_context(
                locale="en-GB" if geography == "uk" else "sv-SE",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                # Let JS settle — most PropTech sites are React/Next.js
                await page.wait_for_timeout(2_000)
                html = await page.content()
                signals = await self._extract_signals(page, html, url)
            except Exception:
                signals = {}
            finally:
                await browser.close()

        return signals

    async def _extract_signals(self, page: Any, html: str, url: str) -> dict[str, Any]:
        html_lower = html.lower()

        # Run JS queries in parallel where possible
        (
            floor_plan_result,
            virtual_tour_result,
            reservation_result,
            cta_result,
            pricing_result,
            project_count,
            content_date,
        ) = await asyncio.gather(
            page.evaluate(_JS_FLOOR_PLAN),
            page.evaluate(_JS_VIRTUAL_TOUR),
            page.evaluate(_JS_RESERVATION),
            page.evaluate(_JS_PRIMARY_CTA),
            page.evaluate(_JS_PRICING),
            page.evaluate(_JS_PROJECT_COUNT),
            page.evaluate(_JS_CONTENT_DATE),
            return_exceptions=True,
        )

        def safe(v: Any, default: Any = None) -> Any:
            return default if isinstance(v, Exception) else v

        floor_plan_url = safe(floor_plan_result)
        virtual_tour_url = safe(virtual_tour_result)
        reservation_path = safe(reservation_result)
        cta_text_raw = safe(cta_result, "")
        pricing_raw = safe(pricing_result, "")
        proj_count = safe(project_count, 0)
        last_post = safe(content_date)

        # Wappalyzer tech detection from HTML
        tech_stack = _detect_tech_stack(html, html_lower)

        # Chat / cookie from script tags (complements Wappalyzer)
        chat_provider = _detect_from_scripts(html_lower, _CHAT_PROVIDERS)
        cookie_provider = _detect_from_scripts(html_lower, _COOKIE_PROVIDERS)

        # Content freshness in days
        freshness_days: int | None = None
        if last_post:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(last_post.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    from datetime import timezone
                    dt = dt.replace(tzinfo=timezone.utc)
                from datetime import datetime, timezone
                freshness_days = (datetime.now(tz=timezone.utc) - dt).days
            except Exception:
                pass

        return {
            "has_interactive_floor_plans": floor_plan_url is not None,
            "floor_plan_provider": _match_provider(floor_plan_url, _FLOOR_PLAN_PROVIDERS),
            "has_virtual_tour": virtual_tour_url is not None,
            "virtual_tour_provider": _match_provider(virtual_tour_url, _VIRTUAL_TOUR_PROVIDERS),
            "has_digital_reservation": reservation_path is not None,
            "reservation_url_pattern": reservation_path,
            "cta_type": _classify_cta_text(cta_text_raw or ""),
            "cta_text": (cta_text_raw or "").strip()[:80],
            "pricing_transparency": _classify_pricing(pricing_raw or ""),
            "price_range_text": pricing_raw if pricing_raw else None,
            "project_count": proj_count if isinstance(proj_count, int) else 0,
            "has_chat_automation": bool(chat_provider or tech_stack.get("chat_platform")),
            "chat_provider": chat_provider or tech_stack.get("chat_platform"),
            "has_cookie_consent": bool(cookie_provider),
            "cookie_consent_provider": cookie_provider,
            "content_freshness_days": freshness_days,
            "tech_stack": tech_stack,
        }

    # ------------------------------------------------------------------
    # PageSpeed API (live, free)
    # ------------------------------------------------------------------

    async def _pagespeed_scan(self, url: str) -> dict[str, Any]:
        params: dict[str, str] = {"url": url, "strategy": "mobile"}
        if self._pagespeed_key:
            params["key"] = self._pagespeed_key

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(_PAGESPEED_BASE, params=params)
                data = response.json()

            categories = data.get("lighthouseResult", {}).get("categories", {})
            audits = data.get("lighthouseResult", {}).get("audits", {})

            mobile_score = int((categories.get("performance", {}).get("score") or 0) * 100)
            fcp_ms = int(
                (audits.get("first-contentful-paint", {}).get("numericValue") or 0)
            )

            # Fetch desktop score separately
            params["strategy"] = "desktop"
            resp2 = await client.get(_PAGESPEED_BASE, params=params)
            desktop_score = int(
                (resp2.json().get("lighthouseResult", {})
                 .get("categories", {})
                 .get("performance", {})
                 .get("score") or 0) * 100
            )
        except Exception:
            return {}

        return {
            "load_time_ms": fcp_ms,
            "mobile_score": mobile_score,
            "desktop_score": desktop_score,
        }

    # ------------------------------------------------------------------
    # Dummy / POC mode
    # ------------------------------------------------------------------

    def _load_dummy(self, domain: str) -> CollectorResult:
        idx = int(hashlib.md5(domain.encode()).hexdigest(), 16) % len(_DUMMY_SCENARIOS)
        fixture_path = _FIXTURES_DIR / _DUMMY_SCENARIOS[idx]
        data = json.loads(fixture_path.read_text())
        # Strip internal metadata keys
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        return CollectorResult(
            collector_id=self.collector_id,
            domain=domain,
            success=True,
            data=data,
        )


# ---------------------------------------------------------------------------
# JavaScript snippets — injected into browser page
# ---------------------------------------------------------------------------

_JS_FLOOR_PLAN = """
() => {
    const providers = ['giraffe360', 'ispyproperty', 'matterport', 'plotai',
                        'floorplanner', 'immoviewer', 'cupix'];
    const iframes = [...document.querySelectorAll('iframe[src]')];
    const match = iframes.find(f => providers.some(p => f.src.toLowerCase().includes(p)));
    return match ? match.src : null;
}
"""

_JS_VIRTUAL_TOUR = """
() => {
    const tourProviders = ['matterport', 'giraffe360', 'eyespy360', 'kuula',
                            'cloudpano', 'roundme', 'vr-tour', '3d-tour',
                            'virtualtour', '360tour'];
    const iframes = [...document.querySelectorAll('iframe[src]')];
    const iframeMatch = iframes.find(f =>
        tourProviders.some(p => f.src.toLowerCase().includes(p))
    );
    if (iframeMatch) return iframeMatch.src;
    const links = [...document.querySelectorAll('a[href]')];
    const linkMatch = links.find(l =>
        tourProviders.some(p => (l.href + l.textContent).toLowerCase().includes(p))
    );
    return linkMatch ? linkMatch.href : null;
}
"""

_JS_RESERVATION = """
() => {
    const patterns = ['/reserve', '/book', '/reservation', '/buy-now',
                       '/buy_now', '/purchase', '/secure', '/boka', '/reservera'];
    const links = [...document.querySelectorAll('a[href]')];
    const match = links.find(l =>
        patterns.some(p => l.pathname.toLowerCase().includes(p))
    );
    return match ? match.pathname : null;
}
"""

_JS_PRIMARY_CTA = """
() => {
    const selectors = [
        '.hero a[class*="btn"]', '.hero button', '.banner a[class*="btn"]',
        'header a[class*="btn"]', 'header button:not([class*="nav"])',
        '[class*="hero"] a[class*="cta"]', '[class*="cta"]:first-of-type',
        'nav a[class*="btn"]', '.navbar a[class*="btn"]',
        'a[class*="button"][href]'
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) {
            const text = el.textContent.trim();
            if (text.length > 1 && text.length < 60) return text;
        }
    }
    const allButtons = [...document.querySelectorAll('a.btn, .btn, button')];
    const visible = allButtons.find(el => {
        const rect = el.getBoundingClientRect();
        return rect.top < 600 && rect.width > 0;
    });
    return visible ? visible.textContent.trim() : null;
}
"""

_JS_PRICING = """
() => {
    const body = document.body.innerText;
    const poaPatterns = ['price on application', 'prices on application',
                          'poa', 'price on request', 'p.o.a'];
    for (const p of poaPatterns) {
        if (body.toLowerCase().includes(p)) return 'poa';
    }
    const priceMatch = body.match(/prices? from [£€]?\\s?[\\d,]+(?:k)?/i);
    if (priceMatch) return priceMatch[0];
    const singlePrice = body.match(/[£€]\\s?[\\d,]{6,}/);
    if (singlePrice) return singlePrice[0];
    return null;
}
"""

_JS_PROJECT_COUNT = """
() => {
    const selectors = [
        '[class*="development-card"]', '[class*="project-card"]',
        '[class*="scheme-card"]', '[class*="property-card"]',
        '.development', '.scheme', '[data-type="development"]'
    ];
    for (const sel of selectors) {
        const count = document.querySelectorAll(sel).length;
        if (count > 0) return count;
    }
    return 0;
}
"""

_JS_CONTENT_DATE = """
() => {
    const dateSelectors = [
        'article time[datetime]', '.post time[datetime]',
        '.news-item time[datetime]', '[class*="blog"] time[datetime]',
        'meta[property="article:published_time"]'
    ];
    for (const sel of dateSelectors) {
        const el = document.querySelector(sel);
        if (el) {
            return el.getAttribute('datetime') || el.getAttribute('content') || null;
        }
    }
    return null;
}
"""


# ---------------------------------------------------------------------------
# Tech stack detection from HTML (lightweight Wappalyzer-style)
# ---------------------------------------------------------------------------

_TECH_FINGERPRINTS: dict[str, dict[str, list[str]]] = {
    # CRM / Marketing
    "HubSpot":             {"scripts": ["hs-scripts.com", "hubspot.com/hs"]},
    "Salesforce":          {"scripts": ["salesforce.com", "pardot.com", "krux.com"]},
    "Marketo":             {"scripts": ["marketo.net", "mktoresp.com"]},
    "ActiveCampaign":      {"scripts": ["trackcmp.net", "activecampaign.com"]},

    # Analytics
    "Google Analytics 4":  {"scripts": ["gtag/js", "google-analytics.com/g/"]},
    "Google Analytics":    {"scripts": ["google-analytics.com/analytics.js", "ga.js"]},
    "Hotjar":              {"scripts": ["hotjar.com"]},
    "Microsoft Clarity":   {"scripts": ["clarity.ms"]},

    # Ads / pixels
    "Facebook Pixel":      {"scripts": ["connect.facebook.net", "fbevents.js"]},
    "Google Ads":          {"scripts": ["googleadservices.com", "googlesyndication.com"]},
    "LinkedIn Insight":    {"scripts": ["snap.licdn.com"]},
    "TikTok Pixel":        {"scripts": ["analytics.tiktok.com"]},

    # Tag managers
    "Google Tag Manager":  {"scripts": ["googletagmanager.com/gtm.js"]},

    # Chat
    "Intercom":            {"scripts": ["intercomcdn.com", "intercom.io/js"]},
    "Drift":               {"scripts": ["js.driftt.com", "drift.com"]},
    "Tidio":               {"scripts": ["tidiochat.com", "tidio.com"]},
    "Crisp":               {"scripts": ["crisp.chat", "client.crisp.chat"]},
    "LiveChat":            {"scripts": ["livechatinc.com"]},
    "Tawk.to":             {"scripts": ["tawk.to"]},
    "Zendesk":             {"scripts": ["zopim.com", "zendesk.com/embeddable"]},

    # Cookie consent
    "OneTrust":            {"scripts": ["onetrust.com", "optanon"]},
    "Cookiebot":           {"scripts": ["cookiebot.com", "cookieconsent"]},
    "CookieYes":           {"scripts": ["cookieyes.com"]},
    "Usercentrics":        {"scripts": ["usercentrics.eu"]},

    # Hosting / CMS signals
    "WordPress":           {"meta": ["wp-content", "wp-includes"]},
    "Webflow":             {"scripts": ["webflow.com"], "meta": ["webflow.io"]},
    "Squarespace":         {"scripts": ["squarespace.com"]},
    "Wix":                 {"scripts": ["wix.com", "wixstatic.com"]},
    "Shopify":             {"scripts": ["shopify.com", "cdn.shopify.com"]},
    "Vercel":              {"meta": ["vercel.app", "_next/"]},
    "Netlify":             {"meta": ["netlify.app", "netlify.com"]},
    "Cloudflare":          {"scripts": ["cloudflare.com/cdn-cgi"]},
}

_ANALYTICS_PRIORITY = [
    "Google Analytics 4", "Google Analytics", "Hotjar", "Microsoft Clarity"
]
_HOSTING_PRIORITY = [
    "Cloudflare", "Vercel", "Netlify", "Webflow", "WordPress",
    "Squarespace", "Wix", "Shopify"
]


def _detect_tech_stack(html: str, html_lower: str) -> dict[str, Any]:
    detected: dict[str, dict] = {}
    for tech, fingerprints in _TECH_FINGERPRINTS.items():
        for pattern in fingerprints.get("scripts", []) + fingerprints.get("meta", []):
            if pattern.lower() in html_lower:
                detected[tech] = {"version": "", "categories": []}
                break

    crm = next((t for t in _WAPPALYZER_CRM if t in detected), None)
    analytics = next((t for t in _ANALYTICS_PRIORITY if t in detected), None)
    chat = next((t for t in _WAPPALYZER_CHAT if t in detected), None)
    hosting = next((t for t in _HOSTING_PRIORITY if t in detected), None)

    return {
        "crm": crm,
        "analytics": analytics,
        "has_facebook_pixel": "Facebook Pixel" in detected,
        "has_google_tag_manager": "Google Tag Manager" in detected,
        "has_cookie_consent": any(
            t in detected for t in ("OneTrust", "Cookiebot", "CookieYes", "Usercentrics")
        ),
        "hosting": hosting,
        "chat_platform": chat,
        "raw_wappalyzer": detected,
    }


def _detect_from_scripts(html_lower: str, provider_map: dict[str, str]) -> str | None:
    for pattern, name in provider_map.items():
        if pattern in html_lower:
            return name
    return None


def _match_provider(url: str | None, provider_map: dict[str, str]) -> str | None:
    if not url:
        return None
    url_lower = url.lower()
    for pattern, name in provider_map.items():
        if pattern in url_lower:
            return name
    return "Unknown"


_CTA_RESERVE_PATTERNS = [
    "reserve", "book", "secure your", "buy now", "purchase",
    "boka", "reservera",
]
_CTA_ENQUIRE_PATTERNS = [
    "enquire", "enquiry", "contact", "find out", "get in touch",
    "register", "request", "download brochure", "förfrågan",
]
_CTA_CALL_PATTERNS = ["call us", "ring us", "phone"]


def _classify_cta_text(text: str) -> str:
    low = text.lower().strip()
    if any(p in low for p in _CTA_RESERVE_PATTERNS):
        return "reserve"
    if any(p in low for p in _CTA_ENQUIRE_PATTERNS):
        return "enquire"
    if any(p in low for p in _CTA_CALL_PATTERNS):
        return "call"
    if low:
        return "other"
    return "unknown"


def _classify_pricing(raw: str) -> str:
    if not raw:
        return "none"
    if raw == "poa":
        return "poa"
    return "shown"
