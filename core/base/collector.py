"""
BaseCollector ABC — the interface every data collector must implement.

The public API is run(). The implementation detail is collect().
Normalization and error containment are handled here so collectors
never need to think about either.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from .schemas import CollectorResult


class BaseCollector(ABC):
    # Subclasses must define these as class variables
    collector_id: str
    is_screening: bool = False  # True only on the DNS/Headers screening collector

    def __init__(self, settings: object) -> None:
        self.settings = settings

    @abstractmethod
    async def collect(self, domain: str, geography: str) -> CollectorResult:
        """Fetch raw signals for the domain. May raise — run() catches everything."""
        ...

    async def run(self, domain: str, geography: str) -> CollectorResult:
        """
        Public entry point called by DomainAuditor.

        Wraps collect() with:
        - Exception containment: any error returns a failed CollectorResult
        - Normalization: applied to result.data via the vertical's NormalizationLayer
        """
        try:
            result = await self.collect(domain, geography)
            result.data = self._normalize(result.data, geography)
            return result
        except Exception as exc:  # noqa: BLE001
            return CollectorResult(
                collector_id=self.collector_id,
                domain=domain,
                collected_at=datetime.utcnow(),
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _normalize(self, data: dict, geography: str) -> dict:
        """
        Apply normalization. Overridden by NormalizationMixin when mixed in.
        Default: pass-through (used in tests and simple collectors).
        """
        return data
