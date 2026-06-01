"""
Planning Intelligence Collector

Searches UK Planning Portal and Swedish planning databases for recent
applications by the developer. A recently-approved planning permission
that has no active portal listing = pre-launch window = Plot.ai pitch.

Signals produced:
  recent_planning_apps[], planning_granted_date, days_since_planning,
  estimated_unit_count, development_stage, new_geography_flag,
  planning_authority
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
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

_PLANNING_API_UK = "https://www.planning.data.gov.uk/entity.json"
_PLANNING_ALERTS_UK = "https://www.planningalerts.org.au/api/v2/applications"


class PlanningIntelCollector(NormalizationMixin, BaseCollector):
    collector_id = "planning_intel"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._dummy_mode: bool = getattr(settings, "site_scanner_dummy_mode", True)

    async def collect(self, domain: str, geography: str) -> CollectorResult:
        if not self._dummy_mode:
            data = await self._live_fetch(domain, geography)
            return CollectorResult(
                collector_id=self.collector_id, domain=domain,
                success=True, data=data,
            )
        return self._load_dummy(domain)

    async def _live_fetch(self, domain: str, geography: str) -> dict[str, Any]:
        company = _domain_to_name(domain)
        client = StealthClient(geography)

        apps: list[dict] = []
        try:
            if geography == "uk":
                apps = await _search_uk_planning(client, company)
            # Sweden: Lantmäteriet API (requires auth in production)
        except Exception:
            pass

        return _build_signals(apps)

    def _load_dummy(self, domain: str) -> CollectorResult:
        idx = int(hashlib.md5(domain.encode()).hexdigest(), 16) % len(_DUMMY_SCENARIOS)
        path = _FIXTURES_DIR / _DUMMY_SCENARIOS[idx]
        data = {k: v for k, v in json.loads(path.read_text()).items() if not k.startswith("_")}
        return CollectorResult(
            collector_id=self.collector_id, domain=domain, success=True, data=data,
        )


# ---------------------------------------------------------------------------
# UK planning search
# ---------------------------------------------------------------------------

async def _search_uk_planning(client: StealthClient, company: str) -> list[dict]:
    """Query planning.data.gov.uk for recent applications by company name."""
    try:
        response = await client.get(
            _PLANNING_API_UK,
            params={
                "dataset": "development-policy-document",
                "organisation__name__icontains": company,
                "limit": 10,
            },
            skip_jitter=True,
        )
        data = response.json()
        return _parse_planning_data_response(data)
    except Exception:
        return []


def _parse_planning_data_response(data: dict) -> list[dict]:
    apps = []
    for entity in data.get("entities", []):
        apps.append({
            "reference": entity.get("reference", ""),
            "address": entity.get("name", ""),
            "description": entity.get("description", ""),
            "decision": "APPROVED",
            "decision_date": entity.get("entry-date", ""),
            "unit_count": None,
        })
    return apps


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def _build_signals(apps: list[dict]) -> dict[str, Any]:
    if not apps:
        return {
            "recent_planning_apps": [],
            "planning_granted_date": None,
            "days_since_planning": None,
            "estimated_unit_count": None,
            "development_stage": "unknown",
            "new_geography_flag": False,
            "planning_authority": None,
        }

    # Sort by decision date descending
    dated = [(a, _parse_date(a.get("decision_date", ""))) for a in apps]
    dated = [(a, d) for a, d in dated if d is not None]
    dated.sort(key=lambda x: x[1], reverse=True)

    most_recent_app, most_recent_date = dated[0] if dated else (apps[0], None)
    now = datetime.now(tz=timezone.utc)

    days_since = (now - most_recent_date).days if most_recent_date else None
    unit_count = _extract_unit_count(most_recent_app.get("description", ""))

    # Stage: if recently approved and no active listing, it's pre-launch
    if days_since is not None and days_since < 180:
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
    }


def _parse_date(value: str) -> datetime | None:
    formats = ["%Y-%m-%d", "%d/%m/%Y", "%d %B %Y"]
    for fmt in formats:
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_unit_count(description: str) -> int | None:
    match = re.search(r"(\d+)\s+(?:dwelling|unit|apartment|home|flat|house)", description, re.I)
    if match:
        return int(match.group(1))
    return None


def _domain_to_name(domain: str) -> str:
    clean = re.sub(r"^www\.", "", domain)
    clean = re.sub(r"\.[a-z]{2,6}(\.[a-z]{2})?$", "", clean)
    return re.sub(r"[-_]", " ", clean).strip()
