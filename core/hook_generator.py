"""
HookGenerator — generates personalised cold outreach copy using Claude API.

Takes the top 3 pain signals from an AuditReport and the ICP persona,
and returns a 3-sentence hook, subject line, and follow-up angle.

Rules baked into the system prompt:
- Reference the specific technical finding (never generic pain)
- State the concrete business consequence
- Soft CTA only — peer-to-peer tone, not vendor pitch
- Zero buzzwords
"""

from __future__ import annotations

import json
import logging

import anthropic

from .base.schemas import AuditReport, M360Module, OutboundCopy, PainSignal, Severity

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an expert B2B cold email copywriter specialising in PropTech outbound for the UK \
and Swedish markets.

You write 3-sentence cold email openers that are hyper-specific, credible, and pain-focused.

Strict rules:
1. Reference the specific technical finding — never generic pain ("I noticed your CTA has \
been 'Enquire' for X months" not "your marketing could be better")
2. State the concrete business consequence in monetary or velocity terms where possible
3. End with a soft, peer-to-peer CTA — never a hard sell
4. Tone: one expert to another, not vendor to prospect
5. Forbidden words: synergy, leverage, game-changing, world-class, exciting, innovative, \
cutting-edge, holistic, seamless, robust, scalable, bespoke, tailored solution
6. Maximum 80 words for hook_text
7. Subject line: max 8 words, no punctuation at end, not a question
"""

_SEVERITY_ORDER: dict[str, int] = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


class HookGenerator:
    def __init__(self, api_key: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)

    async def generate(self, report: AuditReport) -> OutboundCopy:
        top_signals = _top_signals(report.pain_signals, n=3)

        signal_lines = "\n".join(
            f"- [{s.severity.value}] {s.signal_id}: {s.emotional_trigger} "
            f"→ fix: {s.m360_module.value} (angle: {s.hook_angle})"
            for s in top_signals
        )

        primary = report.primary_module.value if report.primary_module else "M360"
        persona = report.icp_persona.value.replace("_", " ") if report.icp_persona else "property developer"

        prompt = f"""Domain: {report.domain}
Geography: {report.geography.value.upper()}
ICP Persona: {persona}
Primary recommended module: {primary}

Top pain signals detected:
{signal_lines}

Write three things:
1. hook_text — 3-sentence cold email opener (max 80 words) referencing the \
MOST CRITICAL specific finding above
2. subject_line — max 8 words, no punctuation at end
3. follow_up_angle — one sentence for the Day 3 follow-up email (different angle)

Respond with valid JSON only:
{{"hook_text": "...", "subject_line": "...", "follow_up_angle": "..."}}"""

        logger.debug("Generating hook for %s (persona=%s)", report.domain, persona)

        message = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:-1])

        data = json.loads(raw)
        return OutboundCopy(**data)


def _top_signals(signals: list[PainSignal], n: int = 3) -> list[PainSignal]:
    return sorted(
        signals,
        key=lambda s: (_SEVERITY_ORDER.get(s.severity.value, 9), -s.confidence),
    )[:n]
