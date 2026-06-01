"""Unit tests for NormalizationLayer — no network calls."""

import pytest

from core.normalizer import (
    NormalizationLayer,
    _classify_cta,
    _normalize_rating,
    _parse_bool,
    _parse_currency,
    _parse_date,
    _parse_unit_count,
)


class TestCurrencyParser:
    def test_gbp_with_symbol(self):
        result = _parse_currency("£450,000")
        assert result == {"amount": 450000.0, "currency": "GBP"}

    def test_gbp_with_commas(self):
        result = _parse_currency("£1,250,000")
        assert result == {"amount": 1250000.0, "currency": "GBP"}

    def test_sek_with_space(self):
        result = _parse_currency("4 500 000 kr")
        assert result == {"amount": 4500000.0, "currency": "SEK"}

    def test_sek_uppercase(self):
        result = _parse_currency("2500000 SEK")
        assert result == {"amount": 2500000.0, "currency": "SEK"}

    def test_eur_with_symbol(self):
        result = _parse_currency("€350,000")
        assert result == {"amount": 350000.0, "currency": "EUR"}

    def test_no_currency_returns_none(self):
        assert _parse_currency("no price here") is None

    def test_plain_number_returns_none(self):
        assert _parse_currency("12345") is None


class TestDateParser:
    def test_iso8601_passthrough(self):
        assert _parse_date("2025-03-03") == "2025-03-03"

    def test_uk_long_format(self):
        assert _parse_date("3 March 2025") == "2025-03-03"

    def test_uk_short_format(self):
        assert _parse_date("03/03/2025") == "2025-03-03"

    def test_abbreviated_month(self):
        assert _parse_date("3 Mar 2025") == "2025-03-03"

    def test_us_format(self):
        assert _parse_date("March 3, 2025") == "2025-03-03"

    def test_dot_separator(self):
        assert _parse_date("03.03.2025") == "2025-03-03"

    def test_unrecognised_returns_none(self):
        assert _parse_date("sometime in spring") is None


class TestCtaClassifier:
    def test_enquire_english(self):
        assert _classify_cta("Enquire Now") == "enquire"

    def test_enquire_swedish(self):
        assert _classify_cta("Förfrågan") == "enquire"

    def test_reserve_english(self):
        assert _classify_cta("Reserve Your Plot") == "reserve"

    def test_reserve_swedish(self):
        assert _classify_cta("Boka nu") == "reserve"

    def test_call_english(self):
        assert _classify_cta("Call Us Today") == "call"

    def test_register_interest(self):
        assert _classify_cta("Register Interest") == "enquire"

    def test_unknown_returns_other(self):
        assert _classify_cta("Download Brochure") == "other"

    def test_case_insensitive(self):
        assert _classify_cta("ENQUIRE") == "enquire"


class TestBoolParser:
    def test_yes(self):
        assert _parse_bool("Yes") is True

    def test_ja(self):
        assert _parse_bool("Ja") is True

    def test_no(self):
        assert _parse_bool("No") is False

    def test_nej(self):
        assert _parse_bool("Nej") is False

    def test_true_string(self):
        assert _parse_bool("true") is True

    def test_one(self):
        assert _parse_bool("1") is True

    def test_unknown_returns_none(self):
        assert _parse_bool("maybe") is None


class TestUnitCountParser:
    def test_plain_number(self):
        assert _parse_unit_count("32") == 32

    def test_with_suffix_english(self):
        assert _parse_unit_count("32 homes") == 32

    def test_with_suffix_swedish(self):
        assert _parse_unit_count("32 bostäder") == 32

    def test_with_comma_separator(self):
        assert _parse_unit_count("1,200 units") == 1200

    def test_no_number_returns_none(self):
        assert _parse_unit_count("no units listed") is None


class TestRatingNormalizer:
    def test_five_star_scale(self):
        assert _normalize_rating(4.2, "avg_rating") == pytest.approx(0.84, rel=0.01)

    def test_ten_point_scale(self):
        assert _normalize_rating(8.4, "avg_rating_10") == pytest.approx(0.84, rel=0.01)

    def test_already_normalized(self):
        assert _normalize_rating(0.84, "score") == pytest.approx(0.84, rel=0.01)


class TestNormalizationLayerIntegration:
    def setup_method(self):
        self.layer = NormalizationLayer()

    def test_full_dict_normalization(self):
        raw = {
            "price": "£450,000",
            "listing_date": "3 March 2025",
            "cta_type": "Enquire Now",
            "has_virtual_tour": "Yes",
            "unit_count": "24 apartments",
        }
        result = self.layer.normalize(raw, "uk")

        assert result["price"] == {"amount": 450000.0, "currency": "GBP"}
        assert result["listing_date"] == "2025-03-03"
        assert result["cta_type"] == "enquire"
        assert result["has_virtual_tour"] is True
        assert result["unit_count"] == 24

    def test_swedish_locale(self):
        raw = {
            "price": "4 500 000 kr",
            "cta_type": "Boka nu",
            "has_interactive_floor_plans": "Ja",
        }
        result = self.layer.normalize(raw, "se")

        assert result["price"] == {"amount": 4500000.0, "currency": "SEK"}
        assert result["cta_type"] == "reserve"
        assert result["has_interactive_floor_plans"] is True

    def test_passthrough_on_unknown_fields(self):
        raw = {"some_custom_field": "unchanged value", "count": 5}
        result = self.layer.normalize(raw, "uk")
        assert result == raw
