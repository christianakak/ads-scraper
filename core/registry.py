"""
VerticalRegistry — maps vertical slugs to their collectors, analyzers, and rules.

Core has zero knowledge of any vertical. Verticals register themselves at
startup. Adding a new vertical (SaaS, FinTech) requires zero changes here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base.analyzer import BaseAnalyzer
    from .base.collector import BaseCollector


class VerticalRegistry:
    _registry: dict[str, dict] = {}

    @classmethod
    def register(
        cls,
        vertical: str,
        collectors: list[type[BaseCollector]],
        analyzers: list[type[BaseAnalyzer]],
        rules_path: str,
        rules_version: str = "1.0.0",
    ) -> None:
        """
        Register a vertical. Can be called multiple times to update a registration
        (e.g. during testing or hot-reload).
        """
        cls._registry[vertical] = {
            "collectors": collectors,
            "analyzers": analyzers,
            "rules_path": rules_path,
            "rules_version": rules_version,
        }

    @classmethod
    def get(cls, vertical: str) -> dict:
        if vertical not in cls._registry:
            raise ValueError(
                f"Vertical '{vertical}' is not registered. "
                f"Registered verticals: {list(cls._registry.keys())}"
            )
        return cls._registry[vertical]

    @classmethod
    def list_verticals(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def clear(cls) -> None:
        """Clear all registrations. Used in tests only."""
        cls._registry.clear()
