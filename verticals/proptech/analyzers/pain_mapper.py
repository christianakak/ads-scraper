"""
PainMapper — evaluates pain rules against collector results.

Loads pain_rules.v{version}.json, evaluates each rule against
the corresponding collector's output, and returns PainSignal objects
for every rule that fires.

Rules are versioned — each audit stores which rule version was used
so A/B testing and historical re-analysis work correctly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.base.analyzer import BaseAnalyzer
from core.base.schemas import (
    CollectorResult,
    ICPPersona,
    M360Module,
    PainSignal,
    Severity,
)

_RULES_FILE = "pain_rules.v1.0.json"

_SEVERITY_MAP = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH":     Severity.HIGH,
    "MEDIUM":   Severity.MEDIUM,
    "LOW":      Severity.LOW,
}

_MODULE_MAP = {
    "Lemon":          M360Module.LEMON,
    "EVE3D":          M360Module.EVE3D,
    "Journey":        M360Module.JOURNEY,
    "Plot.ai":        M360Module.PLOT_AI,
    "Newbuilds.com":  M360Module.NEWBUILDS,
}

_PERSONA_MAP = {
    "scale_up_developer":   ICPPersona.SCALE_UP_DEVELOPER,
    "premium_visionary":    ICPPersona.PREMIUM_VISIONARY,
    "data_driven_planner":  ICPPersona.DATA_DRIVEN_PLANNER,
}


class PainMapper(BaseAnalyzer):

    def _load_rules(self) -> dict:
        path = Path(self.rules_path) / _RULES_FILE
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return {"rules": {}}

    def analyze(self, collector_results: dict[str, CollectorResult]) -> list[PainSignal]:
        rules: dict = self._rules.get("rules", {})
        signals: list[PainSignal] = []

        for rule_id, rule in rules.items():
            collector_id = rule["collector"]
            result = collector_results.get(collector_id)
            if result is None or not result.success:
                continue

            value = _get_nested(result.data, rule["field"])
            if value is None:
                continue

            if not _evaluate_threshold(value, rule["threshold"]):
                continue

            # Boost confidence if corroborating signals exist
            confidence = _compute_confidence(rule, rule_id, collector_results)

            signals.append(PainSignal(
                signal_id=rule_id,
                severity=_SEVERITY_MAP.get(rule["severity"], Severity.MEDIUM),
                confidence=confidence,
                detected_value={rule["field"]: value},
                business_pain=rule["business_pain"],
                emotional_trigger=rule["emotional_trigger"],
                m360_module=_MODULE_MAP[rule["m360_module"]],
                hook_angle=rule["hook_angle"],
                icp_fit=[_PERSONA_MAP[p] for p in rule["icp_fit"] if p in _PERSONA_MAP],
                corroborating_signals=_find_corroborating(rule_id, signals),
            ))

        return signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_nested(data: dict[str, Any], field: str) -> Any:
    """Support dot-notation fields like 'tech_stack.has_facebook_pixel'."""
    parts = field.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _evaluate_threshold(value: Any, threshold: dict | Any) -> bool:
    if not isinstance(threshold, dict):
        return value == threshold
    if "gte" in threshold:
        return value is not None and float(value) >= float(threshold["gte"])
    if "lte" in threshold:
        return value is not None and float(value) <= float(threshold["lte"])
    if "eq" in threshold:
        return value == threshold["eq"]
    return False


def _compute_confidence(rule: dict, rule_id: str, collector_results: dict) -> float:
    base = float(rule.get("base_confidence", 0.80))

    # Boost: if a CRITICAL rule, check data freshness
    result = collector_results.get(rule["collector"])
    if result and result.success and result.data:
        base = min(base + 0.03, 0.99)

    # Slight reduction if collector had partial data
    if result and not result.data:
        base = max(base - 0.10, 0.50)

    return round(base, 3)


def _find_corroborating(rule_id: str, existing_signals: list[PainSignal]) -> list[str]:
    """Mark related signals already found as corroborating."""
    related: dict[str, list[str]] = {
        "critical_ad_fatigue":      ["stale_creative"],
        "stale_creative":           ["recently_stopped_ads", "no_facebook_pixel"],
        "no_digital_reservation":   ["enquire_cta_no_reservation", "no_chat_automation"],
        "not_portal_listed":        ["pre_launch_no_data", "high_days_on_market"],
        "static_floor_plans":       ["no_virtual_tour", "poa_pricing"],
        "pre_launch_no_data":       ["not_portal_listed", "new_geography_expansion"],
    }
    peers = related.get(rule_id, [])
    existing_ids = {s.signal_id for s in existing_signals}
    return [p for p in peers if p in existing_ids]
