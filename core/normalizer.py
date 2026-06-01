"""
NormalizationLayer — transforms raw collector data into canonical formats.

Runs automatically after every collector via BaseCollector.run().
Handles: currency, dates, CTA classification, booleans, unit counts, ratings.

UK and Swedish locale variants are both handled — the intelligence layer
never sees locale-specific strings.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Lookup tables (extended as new markets are added)
# ---------------------------------------------------------------------------

_CURRENCY_PATTERNS: list[tuple[str, str]] = [
    ("GBP", r"£\s*([\d,]+(?:\.\d+)?)"),
    ("GBP", r"([\d,]+(?:\.\d+)?)\s*(?:gbp|GBP)"),
    ("SEK", r"([\d\s]+(?:[.,]\d+)?)\s*(?:kr|SEK|sek)"),
    ("EUR", r"€\s*([\d,]+(?:\.\d+)?)"),
    ("EUR", r"([\d,]+(?:\.\d+)?)\s*(?:eur|EUR)"),
]

_CTA_MAPPINGS: dict[str, list[str]] = {
    "reserve": [
        "reserve", "book", "reservation", "boka", "reservera",
        "buy now", "secure your", "secure plot", "purchase",
    ],
    "enquire": [
        "enquire", "enquiry", "ask", "contact us", "get in touch",
        "förfrågan", "kontakta", "register interest", "express interest",
        "request info", "request information", "find out more",
        "send enquiry", "make enquiry",
    ],
    "call": [
        "call us", "ring us", "phone", "call now", "speak to",
        "ring", "ring oss",
    ],
}

_BOOL_TRUE: set[str] = {"yes", "ja", "true", "1", "on", "y", "sant"}
_BOOL_FALSE: set[str] = {"no", "nej", "false", "0", "off", "n", "falskt"}

_DATE_FORMATS: list[str] = [
    "%d %B %Y",       # 3 March 2025
    "%d %b %Y",       # 3 Mar 2025
    "%B %d, %Y",      # March 3, 2025
    "%d/%m/%Y",       # 03/03/2025
    "%d-%m-%Y",       # 03-03-2025
    "%Y-%m-%d",       # 2025-03-03
    "%d.%m.%Y",       # 03.03.2025
    "%Y/%m/%d",       # 2025/03/03
]

_UNIT_SUFFIXES: list[str] = [
    "homes", "home", "units", "unit", "apartments", "apartment",
    "flats", "flat", "plots", "plot",
    "bostäder", "bostad", "lägenheter", "lägenhet", "enheter",
]


# ---------------------------------------------------------------------------
# Keys that trigger specific normalizers (substring match on field name)
# ---------------------------------------------------------------------------

_CURRENCY_KEYS = ("price", "amount", "value", "cost", "fee")
_DATE_KEYS = ("date", "time", "listed", "granted", "created", "updated")
_CTA_KEYS = ("cta", "call_to_action", "button_text", "cta_type")
_BOOL_KEYS = ("has_", "is_", "enabled", "active", "listed")
_UNIT_KEYS = ("unit_count", "num_units", "units", "homes", "apartments", "flats")
_RATING_KEYS = ("rating", "score", "stars")


class NormalizationLayer:
    def normalize(self, data: dict[str, Any], geography: str) -> dict[str, Any]:
        return {key: self._normalize_field(key, value, geography) for key, value in data.items()}

    def _normalize_field(self, key: str, value: Any, geography: str) -> Any:
        key_lower = key.lower()

        if isinstance(value, str):
            if any(k in key_lower for k in _CURRENCY_KEYS):
                parsed = _parse_currency(value)
                if parsed is not None:
                    return parsed

            if any(k in key_lower for k in _DATE_KEYS):
                parsed = _parse_date(value)
                if parsed is not None:
                    return parsed

            if any(k in key_lower for k in _CTA_KEYS):
                return _classify_cta(value)

            if any(key_lower.startswith(k) for k in ("has_", "is_")):
                parsed = _parse_bool(value)
                if parsed is not None:
                    return parsed

        if isinstance(value, (int, float)):
            if any(k in key_lower for k in _RATING_KEYS):
                return _normalize_rating(value, key_lower)
            if any(k in key_lower for k in _UNIT_KEYS):
                return int(value)

        if isinstance(value, str) and any(k in key_lower for k in _UNIT_KEYS):
            parsed = _parse_unit_count(value)
            if parsed is not None:
                return parsed

        return value


# ---------------------------------------------------------------------------
# Individual parsers (module-level for testability)
# ---------------------------------------------------------------------------

def _parse_currency(value: str) -> dict[str, Any] | None:
    for currency, pattern in _CURRENCY_PATTERNS:
        match = re.search(pattern, value.replace("\xa0", " "))
        if match:
            raw = match.group(1).replace(",", "").replace(" ", "")
            try:
                return {"amount": float(raw), "currency": currency}
            except ValueError:
                continue
    return None


def _parse_date(value: str) -> str | None:
    clean = value.strip()
    # Already ISO8601
    if re.match(r"^\d{4}-\d{2}-\d{2}$", clean):
        return clean
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(clean, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _classify_cta(value: str) -> str:
    lowered = value.lower().strip()
    for cta_type, variants in _CTA_MAPPINGS.items():
        if any(variant in lowered for variant in variants):
            return cta_type
    return "other"


def _parse_bool(value: str) -> bool | None:
    lowered = value.lower().strip()
    if lowered in _BOOL_TRUE:
        return True
    if lowered in _BOOL_FALSE:
        return False
    return None


def _parse_unit_count(value: str) -> int | None:
    match = re.search(r"(\d+)", value.replace(",", "").replace(".", ""))
    if match:
        return int(match.group(1))
    return None


def _normalize_rating(value: float, key: str) -> float:
    # Normalise to 0.0–1.0 range
    if "10" in key or value > 5:
        return round(value / 10, 3)
    if value > 1:
        return round(value / 5, 3)
    return round(value, 3)


# ---------------------------------------------------------------------------
# NormalizationMixin — mixed into BaseCollector subclasses
# ---------------------------------------------------------------------------

class NormalizationMixin:
    """
    Mix into BaseCollector subclasses to activate automatic normalization.

    Usage:
        class MyScraper(NormalizationMixin, BaseCollector):
            ...
    """

    _normalizer: NormalizationLayer = NormalizationLayer()

    def _normalize(self, data: dict[str, Any], geography: str) -> dict[str, Any]:
        return self._normalizer.normalize(data, geography)
