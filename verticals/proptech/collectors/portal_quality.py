"""
Portal Quality Collector

Checks whether the developer has active listings on Rightmove (UK) and
Hemnet (SE), and scores the listing quality — photo count, floor plan
presence, virtual tour badge, price visibility, days on market.

A high-quality portal listing is one that a buyer would engage with;
a low-quality one is a signal the developer is losing leads at the
distribution layer regardless of how good the product is.

Signals produced:
  portal_listed, rightmove_listed, hemnet_listed,
  listing_photo_count, has_floorplan_on_portal, has_virtual_tour_on_portal,
  listing_quality_score, days_on_market, price_shown, price_text,
  portal_cta_type, listing_count, portal_names[]
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from core.base.collector import BaseCollector
from core.base.schemas import CollectorResult
from core.normalizer import NormalizationMixin
from core.stealth import StealthClient

_FIXTURES_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures"
_DUMMY_SCENARIOS = [
    "portal_quality_scale_up.json",
    "portal_quality_premium.json",
    "portal_quality_planner.json",
    "portal_quality_strong.json",
]

_RIGHTMOVE_NEW_HOMES = "https://www.rightmove.co.uk/new-homes/find.html"
_HEMNET_SEARCH = "https://www.hemnet.se/bostader"


class PortalQualityCollector(NormalizationMixin, BaseCollector):
    collector_id = "portal_quality"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._browserless_token: str = getattr(settings, "browserless_token", "")
        self._dummy_mode: bool = getattr(settings, "site_scanner_dummy_mode", True)

    async def collect(self, domain: str, geography: str) -> CollectorResult:
        if self._browserless_token and not self._dummy_mode:
            data = await self._live_scrape(domain, geography)
            return CollectorResult(
                collector_id=self.collector_id, domain=domain,
                success=True, data=data, data_source="real",
            )
        if self._dummy_mode:
            result = self._load_dummy(domain)
            result.data_source = "dummy"
            return result
        return CollectorResult(
            collector_id=self.collector_id, domain=domain,
            success=True, data={"_skipped": "no_browser_configured"},
            data_source="skipped",
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=15))
    async def _live_scrape(self, domain: str, geography: str) -> dict[str, Any]:
        from playwright.async_api import async_playwright

        company = _domain_to_name(domain)
        results: dict[str, Any] = {
            "portal_listed": False,
            "rightmove_listed": False,
            "hemnet_listed": False,
            "listing_photo_count": 0,
            "has_floorplan_on_portal": False,
            "has_virtual_tour_on_portal": False,
            "listing_quality_score": 0.0,
            "days_on_market": None,
            "price_shown": False,
            "price_text": None,
            "portal_cta_type": None,
            "listing_count": 0,
            "description_length": 0,
            "portal_names": [],
        }

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f"wss://chrome.browserless.io?token={self._browserless_token}",
                timeout=30_000,
            )
            page = await browser.new_page()
            try:
                if geography == "uk":
                    await _scrape_rightmove(page, company, results)
                else:
                    await _scrape_hemnet(page, company, results)
            finally:
                await browser.close()

        results["listing_quality_score"] = _compute_quality_score(results)
        return results

    def _load_dummy(self, domain: str) -> CollectorResult:
        idx = int(hashlib.md5(domain.encode()).hexdigest(), 16) % len(_DUMMY_SCENARIOS)
        path = _FIXTURES_DIR / _DUMMY_SCENARIOS[idx]
        data = {k: v for k, v in json.loads(path.read_text()).items() if not k.startswith("_")}
        return CollectorResult(collector_id=self.collector_id, domain=domain, success=True, data=data)


# ---------------------------------------------------------------------------
# Live scraping helpers
# ---------------------------------------------------------------------------

async def _scrape_rightmove(page: Any, company: str, results: dict) -> None:
    try:
        # Search by developer keyword — more reliable than filling a search box
        search_url = (
            f"{_RIGHTMOVE_NEW_HOMES}?searchType=DEVELOPMENT"
            f"&keywords={company.replace(' ', '%20')}"
            f"&locationIdentifier=USERDEFINEDAREA%5E%7B%22id%22%3A8%7D"
        )
        await page.goto(search_url, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(2000)

        # Multiple card selectors — Rightmove updates their markup regularly
        card_selectors = [
            '[data-test="propertyCard"]',
            '.propertyCard',
            '[class*="property-card"]',
            '[class*="l-searchResult"]',
        ]
        cards = []
        for sel in card_selectors:
            cards = await page.query_selector_all(sel)
            if cards:
                break

        if cards:
            results["rightmove_listed"] = True
            results["portal_listed"] = True
            results["portal_names"].append("Rightmove")
            results["listing_count"] = len(cards)

            first = cards[0]
            # Photo count from image elements
            imgs = await first.query_selector_all("img[src]")
            results["listing_photo_count"] = len(imgs)
            # Floor plan badge
            fp = await first.query_selector(
                '[data-test*="floorplan"], [class*="floorplan"], [aria-label*="floor plan" i]'
            )
            results["has_floorplan_on_portal"] = fp is not None
            # Virtual tour badge
            vt = await first.query_selector(
                '[data-test*="virtual"], [class*="virtual-tour"], [aria-label*="virtual tour" i]'
            )
            results["has_virtual_tour_on_portal"] = vt is not None
            # Price
            price_el = await first.query_selector(
                '[class*="price"], [data-test*="price"], .propertyCard-priceValue'
            )
            if price_el:
                price_text = (await price_el.inner_text()).strip()
                results["price_shown"] = bool(re.search(r"[£€]|kr", price_text))
                results["price_text"] = price_text[:80]
            # Description length (quality signal)
            desc_el = await first.query_selector(
                '[class*="description"], [data-test*="description"], .propertyCard-description'
            )
            if desc_el:
                desc_text = (await desc_el.inner_text()).strip()
                results["description_length"] = len(desc_text)
    except Exception:
        pass


async def _scrape_hemnet(page: Any, company: str, results: dict) -> None:
    try:
        await page.goto(
            f"{_HEMNET_SEARCH}?utf8=%E2%9C%93&q={company}&item_types[]=project",
            wait_until="domcontentloaded", timeout=20_000,
        )
        cards = await page.query_selector_all('[class*="listing-card"], [class*="property-listing"]')
        if cards:
            results["hemnet_listed"] = True
            results["portal_listed"] = True
            results["portal_names"].append("Hemnet")
            results["listing_count"] = len(cards)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _domain_to_name(domain: str) -> str:
    clean = re.sub(r"^www\.", "", domain)
    clean = re.sub(r"\.[a-z]{2,6}(\.[a-z]{2})?$", "", clean)
    return re.sub(r"[-_]", " ", clean).strip()


def _compute_quality_score(data: dict) -> float:
    score = 0.0
    photos = data.get("listing_photo_count", 0)
    if photos >= 15:
        score += 0.35
    elif photos >= 8:
        score += 0.20
    elif photos >= 4:
        score += 0.10
    if data.get("has_floorplan_on_portal"):
        score += 0.20
    if data.get("has_virtual_tour_on_portal"):
        score += 0.20
    if data.get("price_shown"):
        score += 0.15
    desc_len = data.get("description_length", 0)
    if desc_len >= 400:
        score += 0.10
    elif desc_len >= 200:
        score += 0.05
    return round(min(score, 1.0), 2)
