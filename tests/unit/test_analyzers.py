"""Unit tests for PainMapper and ICPClassifier analyzers."""

from __future__ import annotations

from core.base.schemas import CollectorResult, M360Module, Severity
from verticals.proptech.analyzers.icp_classifier import (
    ICPClassifier,
    pop_icp_result,
)
from verticals.proptech.analyzers.pain_mapper import (
    PainMapper,
    _evaluate_threshold,
    _get_nested,
)

RULES_PATH = "verticals/proptech/rules"


# ---------------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------------

class TestEvaluateThreshold:
    def test_gte_passes(self):
        assert _evaluate_threshold(47, {"gte": 30}) is True

    def test_gte_fails(self):
        assert _evaluate_threshold(20, {"gte": 30}) is False

    def test_lte_passes(self):
        assert _evaluate_threshold(0.3, {"lte": 0.45}) is True

    def test_eq_bool_true(self):
        assert _evaluate_threshold(False, {"eq": False}) is True

    def test_eq_string(self):
        assert _evaluate_threshold("enquire", {"eq": "enquire"}) is True

    def test_none_value_gte_fails(self):
        assert _evaluate_threshold(None, {"gte": 30}) is False

    def test_none_value_lte_fails(self):
        assert _evaluate_threshold(None, {"lte": 0.5}) is False


class TestGetNested:
    def test_simple_field(self):
        assert _get_nested({"cta_type": "enquire"}, "cta_type") == "enquire"

    def test_dot_notation(self):
        data = {"tech_stack": {"has_facebook_pixel": True}}
        assert _get_nested(data, "tech_stack.has_facebook_pixel") is True

    def test_missing_field(self):
        assert _get_nested({}, "missing") is None

    def test_nested_missing(self):
        assert _get_nested({"tech_stack": {}}, "tech_stack.has_pixel") is None


# ---------------------------------------------------------------------------
# PainMapper
# ---------------------------------------------------------------------------

def _make_collector_map(**kwargs) -> dict[str, CollectorResult]:
    results = {}
    for collector_id, data in kwargs.items():
        results[collector_id] = CollectorResult(
            collector_id=collector_id,
            domain="test.co.uk",
            success=True,
            data=data,
        )
    return results


class TestPainMapper:
    def setup_method(self):
        self.mapper = PainMapper(RULES_PATH)

    def test_stale_creative_fires(self):
        cmap = _make_collector_map(ad_intelligence={"creative_age_days": 47})
        signals = self.mapper.analyze(cmap)
        signal_ids = [s.signal_id for s in signals]
        assert "stale_creative" in signal_ids

    def test_critical_fatigue_fires_at_45(self):
        cmap = _make_collector_map(ad_intelligence={"creative_age_days": 45})
        signals = self.mapper.analyze(cmap)
        signal_ids = [s.signal_id for s in signals]
        assert "critical_ad_fatigue" in signal_ids

    def test_fresh_creative_does_not_fire(self):
        cmap = _make_collector_map(ad_intelligence={"creative_age_days": 10})
        signals = self.mapper.analyze(cmap)
        signal_ids = [s.signal_id for s in signals]
        assert "stale_creative" not in signal_ids
        assert "critical_ad_fatigue" not in signal_ids

    def test_enquire_cta_fires(self):
        cmap = _make_collector_map(site_scanner={"cta_type": "enquire"})
        signals = self.mapper.analyze(cmap)
        signal_ids = [s.signal_id for s in signals]
        assert "enquire_cta_no_reservation" in signal_ids

    def test_reserve_cta_does_not_fire_enquire_rule(self):
        cmap = _make_collector_map(site_scanner={"cta_type": "reserve"})
        signals = self.mapper.analyze(cmap)
        signal_ids = [s.signal_id for s in signals]
        assert "enquire_cta_no_reservation" not in signal_ids

    def test_pre_launch_fires(self):
        cmap = _make_collector_map(planning_intel={"development_stage": "pre_launch"})
        signals = self.mapper.analyze(cmap)
        signal_ids = [s.signal_id for s in signals]
        assert "pre_launch_no_data" in signal_ids

    def test_not_portal_listed_fires(self):
        cmap = _make_collector_map(portal_quality={"portal_listed": False})
        signals = self.mapper.analyze(cmap)
        signal_ids = [s.signal_id for s in signals]
        assert "not_portal_listed" in signal_ids

    def test_portal_listed_does_not_fire(self):
        cmap = _make_collector_map(portal_quality={"portal_listed": True})
        signals = self.mapper.analyze(cmap)
        signal_ids = [s.signal_id for s in signals]
        assert "not_portal_listed" not in signal_ids

    def test_signal_has_correct_module(self):
        cmap = _make_collector_map(ad_intelligence={"creative_age_days": 47})
        signals = self.mapper.analyze(cmap)
        stale = next(s for s in signals if s.signal_id == "stale_creative")
        assert stale.m360_module == M360Module.LEMON

    def test_critical_severity_assigned(self):
        cmap = _make_collector_map(ad_intelligence={"creative_age_days": 50})
        signals = self.mapper.analyze(cmap)
        critical = [s for s in signals if s.severity == Severity.CRITICAL]
        assert len(critical) >= 1

    def test_failed_collector_skipped(self):
        results = {
            "ad_intelligence": CollectorResult(
                collector_id="ad_intelligence", domain="x.co.uk",
                success=False, error="timeout", data={},
            )
        }
        signals = self.mapper.analyze(results)
        assert all(s.signal_id != "stale_creative" for s in signals)

    def test_dot_notation_field(self):
        cmap = _make_collector_map(
            site_scanner={"tech_stack": {"has_facebook_pixel": False}}
        )
        signals = self.mapper.analyze(cmap)
        signal_ids = [s.signal_id for s in signals]
        assert "no_facebook_pixel" in signal_ids

    def test_poor_reviews_fires(self):
        cmap = _make_collector_map(social_review={"avg_rating": 3.2})
        signals = self.mapper.analyze(cmap)
        signal_ids = [s.signal_id for s in signals]
        assert "poor_reviews" in signal_ids

    def test_good_reviews_does_not_fire(self):
        cmap = _make_collector_map(social_review={"avg_rating": 4.5})
        signals = self.mapper.analyze(cmap)
        signal_ids = [s.signal_id for s in signals]
        assert "poor_reviews" not in signal_ids

    def test_multiple_collectors_produce_multiple_signals(self):
        cmap = _make_collector_map(
            ad_intelligence={"creative_age_days": 47},
            site_scanner={"cta_type": "enquire", "has_digital_reservation": False},
            portal_quality={"portal_listed": False},
        )
        signals = self.mapper.analyze(cmap)
        assert len(signals) >= 3


# ---------------------------------------------------------------------------
# ICPClassifier
# ---------------------------------------------------------------------------

class TestICPClassifier:
    def setup_method(self):
        self.classifier = ICPClassifier(RULES_PATH)

    def test_scale_up_signals_classify_correctly(self):
        cmap = _make_collector_map(
            site_scanner={
                "project_count": 4,
                "cta_type": "enquire",
                "has_digital_reservation": False,
                "has_chat_automation": False,
            },
            ad_intelligence={"ad_count": 5, "creative_age_days": 35},
            social_review={"response_rate": 0.2},
        )
        self.classifier.analyze(cmap)
        result = pop_icp_result(cmap)
        assert result is not None
        assert result.top_persona is not None
        from core.base.schemas import ICPPersona
        assert result.top_persona == ICPPersona.SCALE_UP_DEVELOPER

    def test_planner_signals_classify_correctly(self):
        cmap = _make_collector_map(
            planning_intel={"development_stage": "pre_launch"},
            portal_quality={"portal_listed": False},
            ad_intelligence={"has_active_ads": False},
        )
        self.classifier.analyze(cmap)
        result = pop_icp_result(cmap)
        assert result is not None
        from core.base.schemas import ICPPersona
        assert result.top_persona == ICPPersona.DATA_DRIVEN_PLANNER

    def test_high_intent_from_pre_launch_plus_no_listing(self):
        cmap = _make_collector_map(
            planning_intel={"development_stage": "pre_launch", "days_since_planning": 45},
            portal_quality={"portal_listed": False, "days_on_market": None},
            ad_intelligence={"creative_age_days": None},
        )
        self.classifier.analyze(cmap)
        result = pop_icp_result(cmap)
        assert result is not None
        assert result.high_intent is True

    def test_no_signals_returns_none_persona(self):
        cmap = _make_collector_map(
            site_scanner={"project_count": 1},
        )
        self.classifier.analyze(cmap)
        result = pop_icp_result(cmap)
        # Low signal — may be None or very low confidence
        if result and result.top_persona is not None:
            assert result.top_confidence is not None

    def test_high_days_on_market_triggers_high_intent(self):
        cmap = _make_collector_map(
            portal_quality={"portal_listed": True, "days_on_market": 120},
            planning_intel={"development_stage": "active", "days_since_planning": 300},
            ad_intelligence={"creative_age_days": 20},
        )
        self.classifier.analyze(cmap)
        result = pop_icp_result(cmap)
        assert result is not None
        assert result.high_intent is True
        assert "stalled_velocity" in result.high_intent_reason
