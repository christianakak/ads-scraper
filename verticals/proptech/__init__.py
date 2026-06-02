"""
PropTech vertical — collector and analyzer registration.

Collectors run in two phases:
  Phase 0: DnsHeadersCollector (is_screening=True) — gates the rest
  Phase 1: All remaining collectors in parallel via asyncio.gather

Analyzers run synchronously after collection:
  1. PainMapper   — maps signals to M360 module recommendations
  2. ICPClassifier — scores and assigns ICP persona + high_intent flag
"""

from core.registry import VerticalRegistry
from verticals.proptech.analyzers.icp_classifier import ICPClassifier
from verticals.proptech.analyzers.pain_mapper import PainMapper
from verticals.proptech.collectors.ad_intelligence import AdIntelligenceCollector
from verticals.proptech.collectors.dns_headers import DnsHeadersCollector
from verticals.proptech.collectors.planning_intel import PlanningIntelCollector
from verticals.proptech.collectors.portal_quality import PortalQualityCollector
from verticals.proptech.collectors.site_scanner import SiteScannerCollector
from verticals.proptech.collectors.social_review import SocialReviewCollector

RULES_PATH = "verticals/proptech/rules"
RULES_VERSION = "1.0.0"


def register() -> None:
    VerticalRegistry.register(
        vertical="proptech",
        collectors=[
            DnsHeadersCollector,       # Phase 0: screening gate
            AdIntelligenceCollector,   # Meta/Facebook ads via Adyntel
            SiteScannerCollector,      # Browserless + Wappalyzer + PageSpeed
            PortalQualityCollector,    # Rightmove (UK) + Hemnet (SE)
            PlanningIntelCollector,    # UK Planning Portal + Swedish planning data
            SocialReviewCollector,     # Google Places + Trustpilot + HomeViews
        ],
        analyzers=[
            PainMapper,                # Signal → M360 module pain mapping
            ICPClassifier,             # Score → persona + high_intent flag
        ],
        rules_path=RULES_PATH,
        rules_version=RULES_VERSION,
    )
