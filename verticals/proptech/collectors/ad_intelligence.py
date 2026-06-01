"""
Ad Intelligence Collector — Adyntel API

Primary source: Adyntel (adyntel.com) — POST /facebook
Sends a company domain, receives all Meta/Facebook/Instagram ads as JSON.
Adyntel handles domain-to-page resolution internally.

Fallback / POC mode: when ADYNTEL_API_KEY is not set and ADYNTEL_DUMMY_MODE=True,
returns deterministic dummy data from fixtures so the engine runs end-to-end
without any API credits. Swap in the real key when ready.

Signals produced:
  has_active_ads, recently_stopped_ads, ad_count, active_ad_count,
  creative_age_days (oldest active ad), ad_fatigue_score,
  primary_cta_type, cta_types[], spend_tier,
  facebook_page_id, facebook_page_name, landing_page_domains[]
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.base.collector import BaseCollector
from core.base.schemas import CollectorResult
from core.normalizer import NormalizationMixin
from core.stealth import StealthClient

_ADYNTEL_BASE = "https://api.adyntel.com/facebook"

_FIXTURES_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures"
_DUMMY_SCENARIOS = [
    "adyntel_scale_up.json",
    "adyntel_premium.json",
    "adyntel_no_ads.json",
    "adyntel_recently_stopped.json",
]

# Maps Adyntel CTA enum → our internal classification
_CTA_TYPE_MAP: dict[str, str] = {
    "CONTACT_US":      "enquire",
    "LEARN_MORE":      "enquire",
    "SEND_MESSAGE":    "enquire",
    "GET_QUOTE":       "enquire",
    "MESSAGE_NOW":     "enquire",
    "BOOK_NOW":        "reserve",
    "SIGN_UP":         "reserve",
    "SUBSCRIBE":       "reserve",
    "REQUEST_TIME":    "reserve",
    "CALL_NOW":        "call",
    "SHOP_NOW":        "buy",
    "BUY_NOW":         "buy",
    "WATCH_MORE":      "view",
    "SEE_MENU":        "view",
    "APPLY_NOW":       "reserve",
    "GET_OFFER":       "enquire",
    "DOWNLOAD":        "view",
}

# Spend tier — estimated proxy from (active_ad_count × creative_age_days)
_SPEND_TIERS: list[tuple[int, str]] = [
    (500, "HIGH"),
    (150, "MEDIUM"),
    (30,  "LOW"),
    (0,   "NONE"),
]


class AdIntelligenceCollector(NormalizationMixin, BaseCollector):
    collector_id = "ad_intelligence"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._api_key: str = getattr(settings, "adyntel_api_key", "")
        self._email: str = getattr(settings, "adyntel_email", "")
        self._dummy_mode: bool = getattr(settings, "adyntel_dummy_mode", True)

    async def collect(self, domain: str, geography: str) -> CollectorResult:
        if self._api_key:
            raw = await self._fetch_adyntel(domain, geography)
        elif self._dummy_mode:
            raw = self._load_dummy(domain)
        else:
            return CollectorResult(
                collector_id=self.collector_id,
                domain=domain,
                success=True,
                data={"has_active_ads": None, "_skipped": "no_credentials"},
            )

        signals = _compute_signals(raw)
        return CollectorResult(
            collector_id=self.collector_id,
            domain=domain,
            success=True,
            data=signals,
        )

    # ------------------------------------------------------------------
    # Live API
    # ------------------------------------------------------------------

    async def _fetch_adyntel(self, domain: str, geography: str) -> dict[str, Any]:
        client = StealthClient(geography)
        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "email": self._email,
            "company_domain": domain,
            "active_status": "all",   # active + inactive — full history
            "country_code": _geo_to_country(geography),
        }
        try:
            response = await client.post(
                _ADYNTEL_BASE,
                json=payload,
                skip_jitter=True,
            )
            if response.status_code == 204:
                # Domain resolved to no Facebook page — not an error
                return {"results": [], "number_of_ads": 0, "page_id": None}
            return response.json()
        except Exception:
            return {"results": [], "number_of_ads": 0, "page_id": None}

    # ------------------------------------------------------------------
    # Dummy / POC mode
    # ------------------------------------------------------------------

    def _load_dummy(self, domain: str) -> dict[str, Any]:
        """Deterministically pick a scenario fixture based on domain hash."""
        idx = int(hashlib.md5(domain.encode()).hexdigest(), 16) % len(_DUMMY_SCENARIOS)
        fixture_path = _FIXTURES_DIR / _DUMMY_SCENARIOS[idx]
        return json.loads(fixture_path.read_text())


# ---------------------------------------------------------------------------
# Signal computation — Adyntel response format
# ---------------------------------------------------------------------------

def _compute_signals(raw: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = raw.get("results", [])
    now = datetime.now(tz=timezone.utc)

    active_ads = [r for r in results if r.get("is_active")]
    inactive_ads = [r for r in results if not r.get("is_active")]

    # Creative age — oldest ACTIVE ad (the one costing money right now)
    creative_age_days: int | None = None
    oldest_ts: int | None = None
    for ad in active_ads:
        ts = ad.get("start_date")
        if ts and (oldest_ts is None or ts < oldest_ts):
            oldest_ts = ts
    if oldest_ts:
        start_dt = datetime.fromtimestamp(oldest_ts, tz=timezone.utc)
        creative_age_days = (now - start_dt).days

    # Recently stopped: had ads but all are now inactive
    recently_stopped = len(results) > 0 and len(active_ads) == 0
    days_since_stopped: int | None = None
    if recently_stopped and inactive_ads:
        latest_end = max(
            (ad.get("end_date") or 0) for ad in inactive_ads if ad.get("end_date")
        )
        if latest_end:
            end_dt = datetime.fromtimestamp(latest_end, tz=timezone.utc)
            days_since_stopped = (now - end_dt).days

    # CTA classification — use the explicit enum, fall back to copy parsing
    cta_types: list[str] = []
    for ad in results:
        snap = ad.get("snapshot", {})
        cta_enum = snap.get("cta_type") or ""
        classified = _CTA_TYPE_MAP.get(cta_enum.upper())
        if not classified:
            classified = _classify_from_copy(snap.get("body", {}).get("text", ""))
        if classified and classified not in cta_types:
            cta_types.append(classified)

    primary_cta = cta_types[0] if cta_types else None

    # Fatigue score based on oldest active ad
    ad_fatigue_score = _fatigue_score(creative_age_days)

    # Spend tier proxy
    spend_proxy = len(active_ads) * (creative_age_days or 0)
    spend_tier = "NONE"
    for threshold, tier in _SPEND_TIERS:
        if spend_proxy >= threshold:
            spend_tier = tier
            break

    # Landing pages
    landing_page_domains = _extract_landing_domains(results)

    return {
        "has_active_ads": len(active_ads) > 0,
        "recently_stopped_ads": recently_stopped,
        "days_since_stopped": days_since_stopped,
        "ad_count": len(results),
        "active_ad_count": len(active_ads),
        "creative_age_days": creative_age_days,
        "ad_fatigue_score": ad_fatigue_score,
        "primary_cta_type": primary_cta,
        "cta_types": cta_types,
        "spend_tier": spend_tier,
        "facebook_page_id": raw.get("page_id"),
        "facebook_page_name": raw.get("page_name"),
        "landing_page_domains": landing_page_domains[:5],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fatigue_score(age_days: int | None) -> float:
    if age_days is None:
        return 0.0
    if age_days >= 45:
        return 0.95
    if age_days >= 30:
        return 0.75
    if age_days >= 15:
        return 0.40
    return 0.10


_COPY_CTA_MAP: dict[str, list[str]] = {
    "reserve": ["reserve", "book", "boka", "reservera", "secure your"],
    "enquire": ["enquire", "enquiry", "contact", "get in touch", "förfrågan",
                "register interest", "find out more", "request"],
    "call":    ["call us", "ring us", "phone"],
    "view":    ["explore", "discover", "learn more", "see more"],
}


def _classify_from_copy(text: str) -> str | None:
    lowered = text.lower()
    for cta_type, patterns in _COPY_CTA_MAP.items():
        if any(p in lowered for p in patterns):
            return cta_type
    return None


def _extract_landing_domains(ads: list[dict[str, Any]]) -> list[str]:
    import re
    seen: list[str] = []
    for ad in ads:
        url = ad.get("snapshot", {}).get("link_url", "")
        match = re.search(r"https?://(?:www\.)?([^/\s?#]+)", url)
        if match:
            d = match.group(1)
            if d not in seen:
                seen.append(d)
    return seen


def _geo_to_country(geography: str) -> str:
    return {"uk": "GB", "se": "SE"}.get(geography.lower(), "GB")
