"""
HookGenerator — generates personalised cold outreach copy using Claude API.

Takes the full AuditReport and writes 3 highly specific outputs:
  - hook_text: 3-sentence cold email opener (max 80 words)
  - subject_line: max 8 words, no punctuation at end
  - follow_up_angle: 1-sentence Day-3 follow-up framing

The prompt is persona-aware and references specific numbers from the audit
(creative_age_days, load_time_ms, days_on_market, etc.) so the hook reads
like it came from someone who actually looked at their site — not a template.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from .base.schemas import AuditReport, M360Module, OutboundCopy, PainSignal

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """\
You are an expert B2B cold email copywriter for PropTech outbound in the UK and \
Swedish markets. You write for Marketer M360 — an AI-powered platform for property \
developers that includes EVE3D (3D visualisation), Journey (digital reservations), \
Lemon (ad creative automation), Plot.ai (market feasibility), and Newbuilds.com \
(new-homes portal).

RULES — follow every single one:
1. Reference the SPECIFIC technical finding with a real number or detail where possible
   (e.g. "running the same creative for 47 days", "4200ms load time", "134 days on market")
2. State the business consequence in concrete terms (CPL, leads, velocity, margin)
3. End with a soft, peer-to-peer CTA — never a hard sell, never "book a demo"
4. Tone: one expert to another. Never vendor-to-prospect.
5. Vary the hook angle based on the persona:
   - scale_up_developer → velocity / operational cost angle
   - premium_visionary  → price-per-sqm / brand perception angle
   - data_driven_planner → risk reduction / certainty angle
6. FORBIDDEN words: synergy, leverage, game-changing, world-class, seamless, robust,
   scalable, bespoke, tailored solution, innovative, cutting-edge, holistic, exciting
7. hook_text: MAX 80 words, exactly 3 sentences
8. subject_line: MAX 8 words, no punctuation at end, not a question, not a clickbait title
9. follow_up_angle: exactly 1 sentence, different angle from hook_text, references a
   different pain signal if possible
"""

_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

# Human-readable module descriptions for the prompt
_MODULE_CONTEXT = {
    M360Module.LEMON:    "Lemon (ad creative automation — refreshes and A/B tests Meta/Google creatives automatically)",
    M360Module.EVE3D:    "EVE3D (immersive 3D visualisation and digital twin — lets buyers walk through the project off-plan)",
    M360Module.JOURNEY:  "Journey (digital reservation and after-sales automation — replaces manual enquiry handling)",
    M360Module.PLOT_AI:  "Plot.ai (AI feasibility analysis — shows what buyers in a postcode are searching for right now)",
    M360Module.NEWBUILDS:"Newbuilds.com (dedicated new-homes portal — puts the project in front of high-intent buyers)",
}


class HookGenerator:
    def __init__(self, api_key: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)

    async def generate(self, report: AuditReport) -> OutboundCopy:
        top_signals = _top_signals(report.pain_signals, n=3)
        prompt = _build_prompt(report, top_signals)

        logger.debug("Generating hook for %s (persona=%s)", report.domain, report.icp_persona)

        message = self._client.messages.create(
            model=_MODEL,
            max_tokens=500,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:-1])

        data = json.loads(raw)
        return OutboundCopy(**data)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(report: AuditReport, top_signals: list[PainSignal]) -> str:
    persona = report.icp_persona.value.replace("_", " ") if report.icp_persona else "property developer"
    primary = _MODULE_CONTEXT.get(report.primary_module, str(report.primary_module)) if report.primary_module else "M360"

    # Build signal context with specific numbers from raw collector output
    signal_lines = []
    for sig in top_signals:
        value_str = _format_detected_value(sig.detected_value, report.raw_collector_output)
        signal_lines.append(
            f"  [{sig.severity.value}] {sig.signal_id}: {sig.emotional_trigger}\n"
            f"    Detected value: {value_str}\n"
            f"    Fix: {primary}"
        )

    high_intent_note = ""
    if report.high_intent and report.high_intent_reason:
        high_intent_note = f"\nHigh-intent trigger: {report.high_intent_reason} — use this as the hook angle."

    secondary_modules = [
        _MODULE_CONTEXT.get(m, m.value)
        for m in report.recommended_modules[1:3]
        if m != report.primary_module
    ]
    secondary_str = "; ".join(secondary_modules) if secondary_modules else "none"

    return f"""Company domain: {report.domain}
Geography: {report.geography.value.upper()}
ICP Persona: {persona}
Primary recommended module: {primary}
Secondary modules: {secondary_str}{high_intent_note}

Top pain signals detected (most severe first):
{chr(10).join(signal_lines)}

Write exactly:
1. hook_text — 3-sentence opener, MAX 80 words, references the MOST CRITICAL specific finding above with real numbers
2. subject_line — MAX 8 words, no punctuation at end
3. follow_up_angle — 1 sentence for the Day-3 follow-up email, different angle

Respond with valid JSON only:
{{"hook_text": "...", "subject_line": "...", "follow_up_angle": "..."}}"""


def _format_detected_value(
    detected_value: dict[str, Any],
    raw_output: dict[str, Any],
) -> str:
    """Format the raw detected value into something readable for the prompt."""
    if not detected_value:
        return "detected"

    key, val = next(iter(detected_value.items()))

    if key == "creative_age_days" and val:
        return f"creative running for {val} days"
    if key == "cta_type":
        return f"CTA is '{val}' (not 'reserve')"
    if key == "has_digital_reservation" and val is False:
        return "no /reserve or /book flow found on site"
    if key == "has_interactive_floor_plans" and val is False:
        return "only static floor plan images, no interactive embed"
    if key == "has_virtual_tour" and val is False:
        return "no virtual tour embed detected"
    if key == "pricing_transparency" and val == "poa":
        return "price on application — no figure shown to buyers"
    if key == "load_time_ms" and val:
        return f"{val}ms first contentful paint (industry avg: ~2500ms)"
    if key == "portal_listed" and val is False:
        return "not found on Rightmove or Hemnet"
    if key == "days_on_market" and val:
        return f"{val} days on market"
    if key == "listing_quality_score" and val is not None:
        return f"listing quality score {val:.0%} (threshold: 45%)"
    if key == "development_stage" and val == "pre_launch":
        days = raw_output.get("planning_intel", {}).get("days_since_planning")
        units = raw_output.get("planning_intel", {}).get("estimated_unit_count")
        parts = ["pre-launch (planning approved"]
        if days:
            parts.append(f"{days} days ago")
        if units:
            parts.append(f"{units} units")
        return " — ".join(parts) + ")"
    if key == "avg_rating" and val:
        return f"average review rating {val}/5.0"
    if key == "recently_stopped_ads" and val:
        days = raw_output.get("ad_intelligence", {}).get("days_since_stopped")
        return f"ads paused {days} days ago" if days else "ads paused"

    return f"{key}={val}"


def _top_signals(signals: list[PainSignal], n: int = 3) -> list[PainSignal]:
    return sorted(
        signals,
        key=lambda s: (_SEVERITY_RANK.get(s.severity.value, 9), -s.confidence),
    )[:n]
