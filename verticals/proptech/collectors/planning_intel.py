"""
Planning Intelligence Collector

Two complementary sources:
  1. Developer's own site (HTTP, no browser) — checks for register-interest /
     coming-soon signals that indicate a pre-launch development.
  2. UK brownfield land register (planning.data.gov.uk, free, no auth) — checks
     for recently-registered brownfield sites that match the developer's name.

Sweden: Lantmäteriet API (requires auth — future work).

Signals produced:
  recent_planning_apps[], planning_granted_date, days_since_planning,
  estimated_unit_count, development_stage, new_geography_flag,
  planning_authority, has_register_interest_page
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.base.collector import BaseCollector
from core.base.schemas import CollectorResult
from core.normalizer import NormalizationMixin
from core.stealth import StealthClient

_FIXTURES_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures"
_DUMMY_SCENARIOS = [
    "planning_pre_launch.json",
    "planning_active.json",
    "planning_none.json",
    "planning_expanding.json",
]

_PLANNING_DATA_UK = "https://www.planning.data.gov.uk/entity.json"

_PRELAUNCH_PATTERNS = [
    r"register.{0,10}interest",
    r"coming\s+soon",
    r"launching\s+soon",
    r"pre.?launch",
    r"notify\s+me",
    r"be\s+the\s+first",
    r"register\s+now\s+for",
    r"sign\s+up\s+for\s+updates",
]


class PlanningIntelCollector(NormalizationMixin, BaseCollector):
    collector_id = "planning_intel"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._dummy_mode: bool = getattr(settings, "planning_dummy_mode", False)

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

        async def _no_apps() -> list:
            return []

        site_task = _check_site_prelaunch(domain, client)
        brownfield_task = _search_brownfield_uk(client, company) if geography == "uk" else _no_apps()

        results = await asyncio.gather(site_task, brownfield_task, return_exceptions=True)
        site_signals = results[0] if isinstance(results[0], dict) else {}
        apps = results[1] if isinstance(results[1], list) else []

        return _build_signals(apps, site_signals)

    def _load_dummy(self, domain: str) -> CollectorResult:
        idx = int(hashlib.md5(domain.encode()).hexdigest(), 16) % len(_DUMMY_SCENARIOS)
        path = _FIXTURES_DIR / _DUMMY_SCENARIOS[idx]
        data = {k: v for k, v in json.loads(path.read_text()).items() if not k.startswith("_")}
        return CollectorResult(
            collector_id=self.collector_id, domain=domain, success=True, data=data,
        )


# ---------------------------------------------------------------------------
# Pre-launch detection via developer's own site (HTTP only, no browser)
# ---------------------------------------------------------------------------

async def _check_site_prelaunch(domain: str, client: StealthClient) -> dict[str, Any]:
    """Fast HTTP check for register-interest / coming-soon text on the homepage."""
    try:
        resp = await client.get(f"https://{domain}", skip_jitter=True)
        html_lower = resp.text.lower()
        for pat in _PRELAUNCH_PATTERNS:
            if re.search(pat, html_lower):
                return {"has_register_interest_page": True}
    except Exception:
        pass
    return {"has_register_interest_page": False}


# ---------------------------------------------------------------------------
# UK brownfield land register (planning.data.gov.uk — free, no auth required)
# ---------------------------------------------------------------------------

_COMMON_WORDS = {"homes", "house", "property", "properties", "living", "place", "group", "land"}


async def _search_brownfield_uk(client: StealthClient, company: str) -> list[dict]:
    """
    Search the UK brownfield land register for sites with the developer's name
    in the site address. Only runs when the company name is specific enough to
    avoid false positives from single common words like "miller" or "homes".
    """
    words = [w for w in company.lower().split() if w not in _COMMON_WORDS and len(w) > 3]
    if not words:
        return []
    # Use the most distinctive word (longest)
    search_term = max(words, key=len)
    if len(search_term) < 5:
        return []

    try:
        resp = await client.get(
            _PLANNING_DATA_UK,
            params={
                "dataset": "brownfield-land",
                "site-address__icontains": search_term,
                "deliverable": "yes",
                "limit": 5,
            },
            skip_jitter=True,
        )
        entities = resp.json().get("entities", [])
        # Validate the API actually filtered — it silently ignores unknown fields
        # and returns generic results. Discard any that don't mention the term.
        matched = [
            e for e in entities
            if search_term.lower() in (
                e.get("site-address", "") + e.get("notes", "")
            ).lower()
        ]
        return _parse_brownfield_entities(matched)
    except Exception:
        return []


def _parse_brownfield_entities(entities: list[dict]) -> list[dict]:
    apps = []
    for e in entities:
        apps.append({
            "reference": e.get("reference", ""),
            "address": e.get("site-address", e.get("name", "")),
            "description": e.get("notes", ""),
            "decision": "APPROVED",
            "decision_date": e.get("entry-date", ""),
            "unit_count": _extract_unit_count(e.get("notes", "")),
        })
    return apps


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def _build_signals(apps: list[dict], site: dict[str, Any]) -> dict[str, Any]:
    has_register_interest = site.get("has_register_interest_page", False)

    if not apps:
        stage = "pre_launch" if has_register_interest else "unknown"
        return {
            "recent_planning_apps": [],
            "planning_granted_date": None,
            "days_since_planning": None,
            "estimated_unit_count": None,
            "development_stage": stage,
            "new_geography_flag": False,
            "planning_authority": None,
            "has_register_interest_page": has_register_interest,
        }

    dated = [(a, _parse_date(a.get("decision_date", ""))) for a in apps]
    dated = [(a, d) for a, d in dated if d is not None]
    dated.sort(key=lambda x: x[1], reverse=True)

    most_recent_app, most_recent_date = dated[0] if dated else (apps[0], None)
    now = datetime.now(tz=UTC)

    days_since = (now - most_recent_date).days if most_recent_date else None
    unit_count = _extract_unit_count(most_recent_app.get("description", ""))

    if has_register_interest or (days_since is not None and days_since < 180):
        stage = "pre_launch"
    else:
        stage = "active"

    return {
        "recent_planning_apps": [a for a, _ in dated[:3]],
        "planning_granted_date": most_recent_date.date().isoformat() if most_recent_date else None,
        "days_since_planning": days_since,
        "estimated_unit_count": unit_count,
        "development_stage": stage,
        "new_geography_flag": False,
        "planning_authority": None,
        "has_register_interest_page": has_register_interest,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _extract_unit_count(description: str) -> int | None:
    match = re.search(r"(\d+)\s+(?:dwelling|unit|apartment|home|flat|house)", description, re.I)
    return int(match.group(1)) if match else None


def _domain_to_name(domain: str) -> str:
    clean = re.sub(r"^www\.", "", domain)
    clean = re.sub(r"\.[a-z]{2,6}(\.[a-z]{2})?$", "", clean)
    return re.sub(r"[-_]", " ", clean).strip()
