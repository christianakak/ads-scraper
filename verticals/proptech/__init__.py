"""
PropTech vertical — collector and analyzer registration.

Collectors are ordered: screening collector first (is_screening=True),
remaining collectors registered in preferred execution order.
The engine always runs the screening collector first regardless of list order,
but ordering here documents intent.
"""

from core.registry import VerticalRegistry
from verticals.proptech.collectors.dns_headers import DnsHeadersCollector
from verticals.proptech.collectors.ad_intelligence import AdIntelligenceCollector

RULES_PATH = "verticals/proptech/rules"
RULES_VERSION = "1.0.0"


def register() -> None:
    VerticalRegistry.register(
        vertical="proptech",
        collectors=[
            DnsHeadersCollector,       # Phase 0: screening gate (is_screening=True)
            AdIntelligenceCollector,   # Phase 1: Meta Ad Library
            # Phase 3 additions:
            # SiteScannerCollector,
            # PortalQualityCollector,
            # PlanningIntelCollector,
            # SocialReviewCollector,
        ],
        analyzers=[],    # Phase 4: PainMapper, ICPClassifier
        rules_path=RULES_PATH,
        rules_version=RULES_VERSION,
    )
