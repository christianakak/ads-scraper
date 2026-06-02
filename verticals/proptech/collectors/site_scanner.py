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
from datetime import UTC
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
    requires_browser = True

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
                collector_id=self.collector_id, domain=domain,
                success=True, data=data, data_source="real",
            )

        if self._dummy_mode:
            # PageSpeed still runs in dummy mode — free, no browser needed
            pagespeed_data = await self._pagespeed_scan(url)
            dummy = self._load_dummy(domain)
            if pagespeed_data:
                dummy.data.update(pagespeed_data)
            dummy.data_source = "dummy"
            return dummy

        # No browser, no dummy — PageSpeed only (real load time at minimum)
        pagespeed_data = await self._pagespeed_scan(url)
        return CollectorResult(
            collector_id=self.collector_id, domain=domain,
            success=True,
            data=pagespeed_data or {"_skipped": "no_browser_configured"},
            data_source="real" if pagespeed_data else "skipped",
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
                ignore_https_errors=True,
            )
            page = await context.new_page()

            try:
                # Try bare domain first; on SSL error try www-prefixed version
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                except Exception as e:
                    if "ERR_CERT" in str(e) or "SSL" in str(e).upper() or "net::" in str(e):
                        www_url = url.replace("https://", "https://www.", 1) if "www." not in url else url
                        await page.goto(www_url, wait_until="domcontentloaded", timeout=30_000)
                    else:
                        raise
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
            trustpilot_id,
            register_interest,
        ) = await asyncio.gather(
            page.evaluate(_JS_FLOOR_PLAN),
            page.evaluate(_JS_VIRTUAL_TOUR),
            page.evaluate(_JS_RESERVATION),
            page.evaluate(_JS_PRIMARY_CTA),
            page.evaluate(_JS_PRICING),
            page.evaluate(_JS_PROJECT_COUNT),
            page.evaluate(_JS_CONTENT_DATE),
            page.evaluate(_JS_TRUSTPILOT_ID),
            page.evaluate(_JS_REGISTER_INTEREST),
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
        tp_id = safe(trustpilot_id)
        reg_interest = safe(register_interest)

        # Wappalyzer tech detection from HTML
        tech_stack = _detect_tech_stack(html, html_lower)

        # Chat / cookie from script tags (complements Wappalyzer)
        chat_provider = _detect_from_scripts(html_lower, _CHAT_PROVIDERS)
        cookie_provider = _detect_from_scripts(html_lower, _COOKIE_PROVIDERS)

        # Content freshness in days
        freshness_days: int | None = None
        if last_post:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(last_post.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                from datetime import datetime
                freshness_days = (datetime.now(tz=UTC) - dt).days
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
            "trustpilot_business_id": tp_id,
            "has_register_interest": reg_interest is not None,
            "register_interest_signal": reg_interest,
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
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Mobile
                resp_mobile = await client.get(_PAGESPEED_BASE, params=params)
                mobile_data = resp_mobile.json()
                # Desktop
                params["strategy"] = "desktop"
                resp_desktop = await client.get(_PAGESPEED_BASE, params=params)
                desktop_data = resp_desktop.json()

            def _score(d: dict) -> int:
                return int((d.get("lighthouseResult", {}).get("categories", {})
                            .get("performance", {}).get("score") or 0) * 100)

            mobile_score = _score(mobile_data)
            desktop_score = _score(desktop_data)
            fcp_ms = int(
                mobile_data.get("lighthouseResult", {}).get("audits", {})
                .get("first-contentful-paint", {}).get("numericValue") or 0
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
                        'floorplanner', 'immoviewer', 'cupix', 'vr-360', 'eyespy'];
    // 1. Embedded iframe providers
    const iframes = [...document.querySelectorAll('iframe[src]')];
    const iframeMatch = iframes.find(f => providers.some(p => f.src.toLowerCase().includes(p)));
    if (iframeMatch) return iframeMatch.src;
    // 2. Script tags (some providers inject via JS)
    const scripts = [...document.querySelectorAll('script[src]')];
    const scriptMatch = scripts.find(s => providers.some(p => s.src.toLowerCase().includes(p)));
    if (scriptMatch) return scriptMatch.src;
    // 3. Links and buttons with floor plan text
    const els = [...document.querySelectorAll('a, button')];
    const textMatch = els.find(el =>
        /floor\\s*plan|interactive\\s*plan|view\\s*plan/i.test(el.textContent)
    );
    return textMatch ? (textMatch.href || 'text_detected') : null;
}
"""

_JS_VIRTUAL_TOUR = """
() => {
    const tourProviders = ['matterport', 'giraffe360', 'eyespy360', 'kuula',
                            'cloudpano', 'roundme', 'vr-tour', '3d-tour',
                            'virtualtour', '360tour', 'cupix', 'walkthrough'];
    // 1. Iframes
    const iframes = [...document.querySelectorAll('iframe[src]')];
    const iframeMatch = iframes.find(f =>
        tourProviders.some(p => f.src.toLowerCase().includes(p))
    );
    if (iframeMatch) return iframeMatch.src;
    // 2. Script tags
    const scripts = [...document.querySelectorAll('script[src]')];
    const scriptMatch = scripts.find(s =>
        tourProviders.some(p => s.src.toLowerCase().includes(p))
    );
    if (scriptMatch) return scriptMatch.src;
    // 3. Link text and href
    const links = [...document.querySelectorAll('a[href]')];
    const linkMatch = links.find(l =>
        /virtual\\s*tour|3d\\s*tour|360\\s*(tour|view)|walk.?through/i.test(l.textContent + l.href)
    );
    return linkMatch ? linkMatch.href : null;
}
"""

_JS_RESERVATION = """
() => {
    const urlPatterns = ['/reserve', '/reservation', '/book', '/buy-now', '/buy_now',
                          '/purchase', '/secure-your', '/boka', '/reservera',
                          '/pre-reserv', '/online-reserv', '/plot-reservation'];
    const textPatterns = /\\b(reserve\\s*(now|a\\s*plot|your)?|pre-reserv|book\\s*(now|a\\s*(home|viewing))|secure\\s*your|online\\s*reserv|reserv\\s*online|reserve\\s*plot)\\b/i;
    const links = [...document.querySelectorAll('a[href]')];
    // URL path match (highest confidence)
    const urlMatch = links.find(l =>
        urlPatterns.some(p => (l.pathname || '').toLowerCase().includes(p))
    );
    if (urlMatch) return urlMatch.href;
    // Link text match (catches "Online Pre-reservation", "Reserve your plot" etc.)
    const textMatch = links.find(l => textPatterns.test(l.textContent));
    if (textMatch) return textMatch.href || 'text_detected';
    // Button text match
    const buttons = [...document.querySelectorAll('button')];
    const btnMatch = buttons.find(b => textPatterns.test(b.textContent));
    return btnMatch ? 'button_detected' : null;
}
"""

_JS_PRIMARY_CTA = """
() => {
    const isNav = el => !!el.closest('nav, header, [role="navigation"], [class*="nav"], [class*="header"], [class*="menu"], [class*="toolbar"]');
    const isUsable = (el) => {
        const text = el.textContent.trim();
        const rect = el.getBoundingClientRect();
        return text.length > 2 && text.length < 60 && rect.width > 10 && rect.top < 900 && !isNav(el);
    };
    // Priority 1: explicit hero/banner areas
    const heroSelectors = [
        '.hero a', '.hero button', '[class*="hero"] a', '[class*="hero"] button',
        '[class*="banner"] a', '[class*="banner"] button',
        '[class*="development-header"] a',
        'main > section:first-of-type a[class*="btn"]',
        'main > section:first-of-type a[class*="button"]',
        'main > section:first-of-type button',
    ];
    for (const sel of heroSelectors) {
        for (const el of document.querySelectorAll(sel)) {
            if (isUsable(el)) return el.textContent.trim();
        }
    }
    // Priority 2: any visible CTA-like element not in nav
    for (const el of document.querySelectorAll('a[class*="btn"], a[class*="button"], button, [class*="cta"] a')) {
        if (isUsable(el)) return el.textContent.trim();
    }
    return null;
}
"""

_JS_PRICING = """
() => {
    const body = document.body.innerText;
    const poaPatterns = ['price on application', 'prices on application',
                          'poa', 'price on request', 'p.o.a', 'price tbc', 'prices tbc'];
    for (const p of poaPatterns) {
        if (body.toLowerCase().includes(p)) return 'poa';
    }
    // "Prices from £XXX,XXX"
    const fromMatch = body.match(/prices?\\s+from\\s+[£€]?\\s?[\\d,]+(?:k)?/i);
    if (fromMatch) return fromMatch[0];
    // Large GBP/EUR figures (property prices start at 6 digits)
    const singlePrice = body.match(/[£€]\\s?[\\d]{3}[,.]?[\\d]{3}/);
    if (singlePrice) return singlePrice[0];
    // SEK amounts (Swedish)
    const sekMatch = body.match(/[\\d]{1,3}\\s?[\\d]{3}\\s?(?:kr|SEK)/);
    if (sekMatch) return sekMatch[0];
    return null;
}
"""

_JS_PROJECT_COUNT = """
() => {
    const selectors = [
        '[class*="development-card"]', '[class*="project-card"]',
        '[class*="scheme-card"]', '[class*="property-card"]',
        '[class*="home-card"]', '[class*="plot-card"]',
        '[class*="listing-card"]', '[class*="result-card"]',
        '.development', '.scheme', '[data-type="development"]',
        '[class*="development-item"]', '[class*="homes-item"]',
    ];
    for (const sel of selectors) {
        const count = document.querySelectorAll(sel).length;
        if (count > 0 && count < 500) return count;
    }
    return 0;
}
"""

_JS_CONTENT_DATE = """
() => {
    const selectors = [
        'article time[datetime]', '.post time[datetime]',
        '.news-item time[datetime]', '[class*="blog"] time[datetime]',
        'meta[property="article:published_time"]',
        'meta[name="date"]',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) return el.getAttribute('datetime') || el.getAttribute('content') || null;
    }
    return null;
}
"""

_JS_TRUSTPILOT_ID = """
() => {
    // Extract Trustpilot businessunitId from embedded widget iframes or data attrs
    const iframe = document.querySelector('iframe[src*="trustpilot.com"]');
    if (iframe) {
        const match = iframe.src.match(/businessunitId=([a-f0-9]+)/i);
        if (match) return match[1];
    }
    const dataEl = document.querySelector('[data-businessunit-id]');
    if (dataEl) return dataEl.getAttribute('data-businessunit-id');
    // Check scripts for businessunit ID pattern
    const scripts = [...document.querySelectorAll('script:not([src])')];
    for (const s of scripts) {
        const match = s.textContent.match(/businessunitId[\"']?:\\s*[\"']([a-f0-9]{24})[\"']/i);
        if (match) return match[1];
    }
    return null;
}
"""

_JS_REGISTER_INTEREST = """
() => {
    // Detect pre-launch / register interest signals
    const textPatterns = /register\\s*(your\\s*)?(interest|now)|coming\\s*soon|launching\\s*soon|notify\\s*me|pre.?launch|be\\s*(the\\s*)?first/i;
    const links = [...document.querySelectorAll('a')];
    const linkMatch = links.find(l => textPatterns.test(l.textContent + l.href));
    if (linkMatch) return linkMatch.href || linkMatch.textContent.trim().slice(0, 60);
    // Check page-level text
    const bodyMatch = document.body.innerText.match(textPatterns);
    return bodyMatch ? bodyMatch[0] : null;
}
"""


# ---------------------------------------------------------------------------
# Tech stack detection from HTML (lightweight Wappalyzer-style)
# ---------------------------------------------------------------------------

_TECH_FINGERPRINTS: dict[str, dict[str, list[str]]] = {
    # CRM / Marketing automation
    "HubSpot":             {"scripts": ["hs-scripts.com", "hubspot.com/hs"]},
    "Salesforce":          {"scripts": ["salesforce.com", "pardot.com", "krux.com"]},
    "Marketo":             {"scripts": ["marketo.net", "mktoresp.com"]},
    "ActiveCampaign":      {"scripts": ["trackcmp.net", "activecampaign.com"]},

    # Analytics
    "Google Analytics 4":  {"scripts": ["gtag/js", "google-analytics.com/g/"]},
    "Google Analytics":    {"scripts": ["google-analytics.com/analytics.js", "ga.js"]},
    "Hotjar":              {"scripts": ["hotjar.com"]},
    "Microsoft Clarity":   {"scripts": ["clarity.ms"]},
    "Azure Monitor":       {"scripts": ["js.monitor.azure.com", "ai.2.min.js"]},

    # Paid media pixels
    "Facebook Pixel":      {"scripts": ["connect.facebook.net", "fbevents.js"]},
    "Google Ads":          {"scripts": ["googleadservices.com", "googlesyndication.com"]},
    "LinkedIn Insight":    {"scripts": ["snap.licdn.com"]},
    "TikTok Pixel":        {"scripts": ["analytics.tiktok.com"]},
    "Bing UET":            {"scripts": ["bat.bing.com"]},
    "Pinterest Tag":       {"scripts": ["pintrk", "s.pinimg.com"]},

    # Tag managers
    "Google Tag Manager":  {"scripts": ["googletagmanager.com/gtm.js"]},

    # Reputation / reviews
    "Trustpilot":          {"scripts": ["widget.trustpilot.com"]},
    "HomeViews":           {"scripts": ["homeviews.com"]},

    # Chat / lead capture
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
    "Usercentrics":        {"scripts": ["usercentrics.eu", "app.usercentrics.eu"]},

    # Hosting / CMS
    "WordPress":           {"meta": ["wp-content", "wp-includes"]},
    "Webflow":             {"scripts": ["webflow.com"], "meta": ["webflow.io"]},
    "Squarespace":         {"scripts": ["squarespace.com"]},
    "Wix":                 {"scripts": ["wix.com", "wixstatic.com"]},
    "Shopify":             {"scripts": ["shopify.com", "cdn.shopify.com"]},
    "Vercel":              {"meta": ["vercel.app", "_next/"]},
    "Netlify":             {"meta": ["netlify.app", "netlify.com"]},
    "Cloudflare":          {"scripts": ["cloudflare.com/cdn-cgi"]},
    "Sitecore":            {"scripts": ["sitecore"], "meta": ["sitecore"]},
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
