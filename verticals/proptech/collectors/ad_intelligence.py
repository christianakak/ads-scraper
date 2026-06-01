"""
Ad Intelligence Collector

Sources:
  - Meta Ad Library API (free, requires access token)
  - Google Ads Transparency Center (scraped, Phase 3 addition)

Signals produced:
  has_active_ads, ad_count, creative_age_days (oldest active ad),
  ad_fatigue_score, spend_tier, cta_types_in_ads[], facebook_page_id,
  facebook_page_name, landing_page_domains[]

Meta Ad Library API docs:
  https://developers.facebook.com/docs/marketing-api/reference/ads-archive
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from core.base.collector import BaseCollector
from core.base.schemas import CollectorResult
from core.normalizer import NormalizationMixin
from core.stealth import StealthClient

_AD_LIBRARY_BASE = "https://graph.facebook.com/v19.0/ads_archive"

_COUNTRY_CODES: dict[str, str] = {
    "uk": "GB",
    "se": "SE",
}

_AD_LIBRARY_FIELDS = ",".join([
    "ad_delivery_start_time",
    "ad_delivery_stop_time",
    "ad_creative_bodies",
    "ad_snapshot_url",
    "page_name",
    "page_id",
    "spend",
    "impressions",
])

_CTA_PATTERNS: dict[str, list[str]] = {
    "reserve": ["reserve", "book", "boka", "reservera", "secure your"],
    "enquire": ["enquire", "enquiry", "contact", "ask", "find out", "förfrågan", "register interest"],
    "view": ["view", "explore", "discover", "see", "learn more", "find out more"],
    "buy": ["buy now", "purchase", "get yours"],
}

# Spend tier thresholds — estimated from (ad_count × creative_age_days)
_SPEND_TIER_THRESHOLDS = [
    (500, "HIGH"),
    (150, "MEDIUM"),
    (30,  "LOW"),
    (0,   "NONE"),
]


class AdIntelligenceCollector(NormalizationMixin, BaseCollector):
    collector_id = "ad_intelligence"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._token: str = getattr(settings, "meta_ad_library_token", "")

    async def collect(self, domain: str, geography: str) -> CollectorResult:
        if not self._token:
            return CollectorResult(
                collector_id=self.collector_id,
                domain=domain,
                success=True,
                data={"has_active_ads": None, "_skipped": "no_token"},
            )

        company_name = _domain_to_company_name(domain)
        country_code = _COUNTRY_CODES.get(geography, "GB")

        ads = await self._fetch_ads(company_name, country_code)

        if not ads:
            return CollectorResult(
                collector_id=self.collector_id,
                domain=domain,
                success=True,
                data={
                    "has_active_ads": False,
                    "ad_count": 0,
                    "creative_age_days": None,
                    "ad_fatigue_score": 0.0,
                    "spend_tier": "NONE",
                    "cta_types_in_ads": [],
                    "facebook_page_id": None,
                    "facebook_page_name": None,
                    "landing_page_domains": [],
                },
            )

        signals = _compute_signals(ads)
        return CollectorResult(
            collector_id=self.collector_id,
            domain=domain,
            success=True,
            data=signals,
        )

    async def _fetch_ads(self, search_term: str, country_code: str) -> list[dict[str, Any]]:
        client = StealthClient()
        params = {
            "fields": _AD_LIBRARY_FIELDS,
            "search_terms": search_term,
            "ad_type": "ALL",
            "ad_reached_countries": country_code,
            "ad_active_status": "ACTIVE",
            "limit": 50,
            "access_token": self._token,
        }

        try:
            response = await client.get(
                _AD_LIBRARY_BASE,
                params=params,
                skip_jitter=True,  # API call — no jitter needed
            )
            data = response.json()
            return data.get("data", [])
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def _compute_signals(ads: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)

    # Creative age — oldest active ad (longest running = most fatigued)
    start_dates: list[datetime] = []
    for ad in ads:
        raw = ad.get("ad_delivery_start_time")
        if raw:
            try:
                # Normalise timezone suffixes: +0000 → +00:00, Z → +00:00
                clean = raw.replace("Z", "+00:00")
                if re.search(r"[+-]\d{4}$", clean):
                    clean = clean[:-5] + clean[-5:-2] + ":" + clean[-2:]
                dt = datetime.fromisoformat(clean)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                start_dates.append(dt)
            except ValueError:
                pass

    oldest_start = min(start_dates) if start_dates else None
    creative_age_days = (now - oldest_start).days if oldest_start else None

    # CTAs
    cta_types: list[str] = []
    landing_page_domains: list[str] = []
    for ad in ads:
        bodies = ad.get("ad_creative_bodies") or []
        for body in bodies:
            if isinstance(body, str):
                cta = _classify_cta_from_copy(body)
                if cta and cta not in cta_types:
                    cta_types.append(cta)

        snapshot_url = ad.get("ad_snapshot_url", "")
        if snapshot_url:
            domain_match = re.search(r"https?://(?:www\.)?([^/\s?]+)", snapshot_url)
            if domain_match:
                lp_domain = domain_match.group(1)
                if lp_domain not in landing_page_domains:
                    landing_page_domains.append(lp_domain)

    # Page info (first ad wins)
    page_id = ads[0].get("page_id") if ads else None
    page_name = ads[0].get("page_name") if ads else None

    # Fatigue score
    ad_fatigue_score = _compute_fatigue_score(creative_age_days)

    # Spend tier — estimated proxy from ad_count × creative_age
    spend_proxy = len(ads) * (creative_age_days or 0)
    spend_tier = "NONE"
    for threshold, tier in _SPEND_TIER_THRESHOLDS:
        if spend_proxy >= threshold:
            spend_tier = tier
            break

    return {
        "has_active_ads": len(ads) > 0,
        "ad_count": len(ads),
        "creative_age_days": creative_age_days,
        "ad_fatigue_score": ad_fatigue_score,
        "spend_tier": spend_tier,
        "cta_types_in_ads": cta_types,
        "facebook_page_id": page_id,
        "facebook_page_name": page_name,
        "landing_page_domains": landing_page_domains[:5],
    }


def _compute_fatigue_score(age_days: int | None) -> float:
    if age_days is None:
        return 0.0
    if age_days >= 45:
        return 0.95
    if age_days >= 30:
        return 0.75
    if age_days >= 15:
        return 0.40
    return 0.10


def _classify_cta_from_copy(copy: str) -> str | None:
    lowered = copy.lower()
    for cta_type, patterns in _CTA_PATTERNS.items():
        if any(p in lowered for p in patterns):
            return cta_type
    return None


def _domain_to_company_name(domain: str) -> str:
    """'berkeley-homes.co.uk' → 'berkeley homes'"""
    # Strip www. prefix
    clean = re.sub(r"^www\.", "", domain)
    # Remove TLD(s): .co.uk, .com, .se, etc.
    clean = re.sub(r"\.[a-z]{2,6}(\.[a-z]{2})?$", "", clean)
    # Replace hyphens and underscores with spaces
    clean = re.sub(r"[-_]", " ", clean)
    return clean.strip()
