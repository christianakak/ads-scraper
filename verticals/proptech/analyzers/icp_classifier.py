"""
ICPClassifier — scores collector signals against ICP persona definitions.

Loads icp_rules.v1.0.json, evaluates weighted signals for each persona,
and assigns the highest-scoring persona to the audit.

Also computes high_intent flag based on specific trigger combinations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.base.analyzer import BaseAnalyzer
from core.base.schemas import CollectorResult, ICPPersona, PainSignal

_RULES_FILE = "icp_rules.v1.0.json"

_PERSONA_MAP = {
    "scale_up_developer":   ICPPersona.SCALE_UP_DEVELOPER,
    "premium_visionary":    ICPPersona.PREMIUM_VISIONARY,
    "data_driven_planner":  ICPPersona.DATA_DRIVEN_PLANNER,
}

# Passed as context on the AuditReport — not PainSignals
HIGH_INTENT_CONTEXT_KEY = "__icp_classification__"


class ICPClassifier(BaseAnalyzer):

    def _load_rules(self) -> dict:
        path = Path(self.rules_path) / _RULES_FILE
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return {"personas": {}}

    def analyze(self, collector_results: dict[str, CollectorResult]) -> list[PainSignal]:
        """
        Returns an empty list — ICP classification doesn't produce PainSignals.
        Results are attached to the AuditReport via the special context key.
        The DomainAuditor engine reads this after analysis completes.
        """
        persona_scores = self._score_personas(collector_results)
        high_intent, high_intent_reason = self._detect_high_intent(collector_results, persona_scores)

        # Encode result as a synthetic "signal" the engine can extract
        # This avoids needing a separate return channel from analyzers
        result = _IcpResult(
            persona_scores=persona_scores,
            high_intent=high_intent,
            high_intent_reason=high_intent_reason,
        )
        # Store on a module-level singleton for the engine to pick up
        _icp_result_cache[id(collector_results)] = result
        return []

    def _score_personas(self, collector_results: dict[str, CollectorResult]) -> dict[str, float]:
        personas: dict = self._rules.get("personas", {})
        scores: dict[str, float] = {}

        for persona_id, persona_def in personas.items():
            score = 0.0
            for signal in persona_def.get("signals", []):
                result = collector_results.get(signal["collector"])
                if result is None or not result.success:
                    continue
                value = _get_nested(result.data, signal["field"])
                if value is None:
                    continue
                if _evaluate_threshold(value, signal["threshold"]):
                    score += float(signal["weight"])
            scores[persona_id] = round(score, 3)

        return scores

    def _detect_high_intent(
        self,
        collector_results: dict[str, CollectorResult],
        persona_scores: dict[str, float],
    ) -> tuple[bool, str | None]:
        planning = collector_results.get("planning_intel")
        portal = collector_results.get("portal_quality")
        ads = collector_results.get("ad_intelligence")

        triggers: list[str] = []

        # Pre-launch window: planning approved + not on portal yet
        if planning and planning.success:
            stage = planning.data.get("development_stage")
            days = planning.data.get("days_since_planning")
            if stage == "pre_launch" and days is not None and days < 120:
                triggers.append("recent_planning_permission")

        if portal and portal.success:
            if not portal.data.get("portal_listed"):
                triggers.append("no_portal_listing")
            dom = portal.data.get("days_on_market")
            if dom is not None and dom >= 90:
                triggers.append("stalled_velocity_90d")

        if ads and ads.success:
            age = ads.data.get("creative_age_days")
            if age is not None and age >= 45:
                triggers.append("critical_ad_fatigue")

        if len(triggers) >= 2:
            return True, " + ".join(triggers)
        if len(triggers) == 1 and triggers[0] in ("recent_planning_permission", "stalled_velocity_90d"):
            return True, triggers[0]

        return False, None


# ---------------------------------------------------------------------------
# Shared result cache — engine reads this after analyze() returns
# ---------------------------------------------------------------------------

_icp_result_cache: dict[int, _IcpResult] = {}


class _IcpResult:
    def __init__(
        self,
        persona_scores: dict[str, float],
        high_intent: bool,
        high_intent_reason: str | None,
    ) -> None:
        self.persona_scores = persona_scores
        self.high_intent = high_intent
        self.high_intent_reason = high_intent_reason

    @property
    def top_persona(self) -> ICPPersona | None:
        if not self.persona_scores:
            return None
        best = max(self.persona_scores, key=lambda k: self.persona_scores[k])
        if self.persona_scores[best] < 0.10:
            return None
        return _PERSONA_MAP.get(best)

    @property
    def top_confidence(self) -> float | None:
        if not self.persona_scores:
            return None
        best_score = max(self.persona_scores.values())
        total = sum(self.persona_scores.values())
        if total == 0:
            return None
        return round(best_score / total, 3)


def pop_icp_result(collector_results: dict) -> _IcpResult | None:
    """Called by engine after analyzers run to retrieve ICP classification."""
    return _icp_result_cache.pop(id(collector_results), None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_nested(data: dict[str, Any], field: str) -> Any:
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
