"""
BaseAnalyzer ABC — maps collector results to PainSignal objects.

Analyzers are deliberately synchronous: pure data transformation, no I/O.
They load rules once at init and apply them against the collector map.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .schemas import CollectorResult, PainSignal


class BaseAnalyzer(ABC):
    def __init__(self, rules_path: str) -> None:
        self.rules_path = rules_path
        self._rules = self._load_rules()

    @abstractmethod
    def _load_rules(self) -> dict:
        """Load and parse the rules file(s) from rules_path. Called once at init."""
        ...

    @abstractmethod
    def analyze(self, collector_results: dict[str, CollectorResult]) -> list[PainSignal]:
        """
        Map collector results to pain signals.

        Args:
            collector_results: keyed by collector_id

        Returns:
            List of PainSignal objects. Empty list is valid (no pain detected).
        """
        ...
