"""
PropTech vertical — registration stub.

Collectors and analyzers are empty for Phase 1.
They will be added in Phases 2–4 without any changes to this file's interface.
"""

from core.registry import VerticalRegistry

RULES_PATH = "verticals/proptech/rules"
RULES_VERSION = "1.0.0"


def register() -> None:
    VerticalRegistry.register(
        vertical="proptech",
        collectors=[],   # Phase 2: dns_headers, ad_intelligence, site_scanner...
        analyzers=[],    # Phase 4: PainMapper, ICPClassifier
        rules_path=RULES_PATH,
        rules_version=RULES_VERSION,
    )
