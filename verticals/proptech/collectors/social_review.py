"""
Social/Review Scanner Collector

Aggregates review signals from Google Places, Trustpilot, and HomeViews (UK).
Low response rate = team overwhelmed (Journey signal).
Negative sentiment keywords = CX breakdown.

Signals produced:
  review_count, avg_rating, response_rate,
  sentiment_keywords[], negative_keywords[],
  has_reviews_page, review_platforms[],
  trustpilot_score, google_rating, homeviews_score
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx

from core.base.collector import BaseCollector
from core.base.schemas import CollectorResult
from core.normalizer import NormalizationMixin
from core.stealth import StealthClient

_FIXTURES_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures"
_DUMMY_SCENARIOS = [
    "reviews_good.json",
    "reviews_poor.json",
    "reviews_none.json",
    "reviews_mixed.json",
]

_GOOGLE_PLACES_BASE = "https://maps.googleapis.com/maps/api/place"

# Keywords that indicate specific pains
_JOURNEY_NEGATIVE_KEYWORDS = [
    "slow response", "no communication", "difficult to reach", "ignored",
    "after sales", "handover", "snagging", "not respond",
]
_VELOCITY_KEYWORDS = [
    "delays", "late", "behind schedule", "overdue", "slow build",
]


class SocialReviewCollector(NormalizationMixin, BaseCollector):
    collector_id = "social_review"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._dummy_mode: bool = getattr(settings, "reviews_dummy_mode", False)
        self._google_api_key: str = (
            getattr(settings, "google_api_key", "")
            or getattr(settings, "google_pagespeed_api_key", "")
        )
        self._trustpilot_api_key: str = getattr(settings, "trustpilot_api_key", "")

    async def collect(self, domain: str, geography: str) -> CollectorResult:
        if not self._dummy_mode:
            data = await self._live_fetch(domain, geography)
            return CollectorResult(
                collector_id=self.collector_id, domain=domain,
                success=True, data=data, data_source="real",
            )
        result = self._load_dummy(domain)
        result.data_source = "dummy"
        return result

    async def _live_fetch(self, domain: str, geography: str) -> dict[str, Any]:
        import asyncio

        company = _domain_to_name(domain)
        client = StealthClient(geography)

        google_data, trustpilot_data = await asyncio.gather(
            self._fetch_google_places(company),
            self._fetch_trustpilot(client, domain, company, geography),
            return_exceptions=True,
        )

        g = google_data if isinstance(google_data, dict) else {}
        t = trustpilot_data if isinstance(trustpilot_data, dict) else {}

        return _merge_reviews(g, t)

    async def _fetch_google_places(self, company: str) -> dict[str, Any]:
        if not self._google_api_key:
            return {}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{_GOOGLE_PLACES_BASE}/findplacefromtext/json",
                    params={
                        "input": company,
                        "inputtype": "textquery",
                        "fields": "place_id,name,rating,user_ratings_total",
                        "key": self._google_api_key,
                    },
                )
                data = r.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return {}
            place = candidates[0]
            return {
                "google_rating": place.get("rating"),
                "google_review_count": place.get("user_ratings_total", 0),
            }
        except Exception:
            return {}

    async def _fetch_trustpilot(
        self, client: StealthClient, domain: str, company: str, geography: str
    ) -> dict[str, Any]:
        # Step 1: extract businessunitId from developer's homepage HTML (no browser)
        tp_id = await _extract_trustpilot_id(domain, client)

        # Step 2: use Trustpilot Business API if key is available (free tier OK)
        if self._trustpilot_api_key and tp_id:
            api_data = await _fetch_trustpilot_api(tp_id, self._trustpilot_api_key)
            if api_data:
                api_data["trustpilot_business_id"] = tp_id
                return api_data

        # Step 3: HTML scraping (Trustpilot blocks most headless requests — best-effort)
        bare_domain = domain.removeprefix("www.")
        www_domain = f"www.{bare_domain}"
        name_slug = company.lower().replace(" ", "-")
        for host in ("uk.trustpilot.com", "www.trustpilot.com"):
            for slug in (bare_domain, www_domain, name_slug):
                try:
                    r = await client.get(
                        f"https://{host}/review/{slug}",
                        referer="https://www.trustpilot.com",
                    )
                    if r.status_code == 200 and "ratingValue" in r.text:
                        result = _parse_trustpilot_html(r.text)
                        result["trustpilot_business_id"] = tp_id
                        return result
                except Exception:
                    continue

        return {"trustpilot_business_id": tp_id} if tp_id else {}

    def _load_dummy(self, domain: str) -> CollectorResult:
        idx = int(hashlib.md5(domain.encode()).hexdigest(), 16) % len(_DUMMY_SCENARIOS)
        path = _FIXTURES_DIR / _DUMMY_SCENARIOS[idx]
        data = {k: v for k, v in json.loads(path.read_text()).items() if not k.startswith("_")}
        return CollectorResult(
            collector_id=self.collector_id, domain=domain, success=True, data=data,
        )


# ---------------------------------------------------------------------------
# Parsers + merging
# ---------------------------------------------------------------------------

async def _fetch_trustpilot_api(business_unit_id: str, api_key: str) -> dict[str, Any]:
    """Fetch rating summary from Trustpilot Business API (free tier)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"https://api.trustpilot.com/v1/business-units/{business_unit_id}",
                headers={"apikey": api_key},
            )
            if r.status_code != 200:
                return {}
            data = r.json()
            score = data.get("score", {})
            return {
                "trustpilot_score": score.get("trustScore"),
                "trustpilot_review_count": data.get("numberOfReviews", {}).get("total", 0),
            }
    except Exception:
        return {}


async def _extract_trustpilot_id(domain: str, client: StealthClient) -> str | None:
    """
    Fetch the developer's homepage and extract the Trustpilot businessunitId
    from embedded widget markup — no browser required.
    """
    try:
        resp = await client.get(f"https://{domain}", skip_jitter=True)
        html = resp.text
        # Widget iframe: businessunitId=5803fd7b0000ff0005962025
        m = re.search(r"businessunitId[=:]['\"\s]*([a-f0-9]{24})", html, re.I)
        if m:
            return m.group(1)
        # Data attribute: data-businessunit-id="..."
        m = re.search(r'data-businessunit-id=["\']([a-f0-9]{24})["\']', html, re.I)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _parse_trustpilot_html(html: str) -> dict[str, Any]:
    rating_match = re.search(r'"ratingValue":\s*"?([\d.]+)"?', html)
    count_match = re.search(r'"reviewCount":\s*"?(\d+)"?', html)
    return {
        "trustpilot_score": float(rating_match.group(1)) if rating_match else None,
        "trustpilot_review_count": int(count_match.group(1)) if count_match else 0,
    }


def _merge_reviews(google: dict, trustpilot: dict) -> dict[str, Any]:
    sources: list[float] = []
    platforms: list[str] = []
    total_count = 0

    g_rating = google.get("google_rating")
    if g_rating:
        sources.append(float(g_rating))
        platforms.append("Google")
        total_count += google.get("google_review_count", 0)

    t_score = trustpilot.get("trustpilot_score")
    if t_score:
        sources.append(float(t_score))
        platforms.append("Trustpilot")
        total_count += trustpilot.get("trustpilot_review_count", 0)

    avg = round(sum(sources) / len(sources), 2) if sources else None

    return {
        "review_count": total_count,
        "avg_rating": avg,
        "response_rate": None,
        "sentiment_keywords": [],
        "negative_keywords": [],
        "has_reviews_page": len(platforms) > 0,
        "review_platforms": platforms,
        "trustpilot_score": trustpilot.get("trustpilot_score"),
        "trustpilot_business_id": trustpilot.get("trustpilot_business_id"),
        "google_rating": google.get("google_rating"),
        "homeviews_score": None,
    }


def _domain_to_name(domain: str) -> str:
    clean = re.sub(r"^www\.", "", domain)
    clean = re.sub(r"\.[a-z]{2,6}(\.[a-z]{2})?$", "", clean)
    return re.sub(r"[-_]", " ", clean).strip()
