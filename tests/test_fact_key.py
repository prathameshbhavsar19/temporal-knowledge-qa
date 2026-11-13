"""Tests for solution/fact_key.py. Every example is copied from the real snapshots.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "solution"))

from fact_key import (  # noqa: E402
    EntityKey,
    FactKey,
    Range,
    clean_percent,
    default_kinds,
    kind_from_header,
    parse_amount,
    parse_label,
    parse_model_years,
    parse_term_months,
    percent_value,
    segment_of,
)


class TestRanges(unittest.TestCase):
    def test_model_years(self):
        self.assertEqual(parse_model_years("2024-27 Model Year"), Range(2024, 2027))
        self.assertEqual(parse_model_years("2023\u201326 Model Year"), Range(2023, 2026))
        self.assertEqual(parse_model_years("Model Year 2026-23"), Range(2023, 2026))
        self.assertEqual(parse_model_years("2016 Model Year (and older)"), Range(None, 2016))
        self.assertTrue(parse_model_years("2024-27 Model Year").contains(2025))
        self.assertFalse(parse_model_years("2021-23 Model Year").contains(2025))

    def test_amounts(self):
        self.assertEqual(parse_amount("$10,000 - $24,999.99"), Range(10000, 24999.99))
        self.assertEqual(parse_amount("$10,000-24,999"), Range(10000, 24999))
        self.assertEqual(parse_amount("$10,000 \u2013 $24,999.99"), Range(10000, 24999.99))
        self.assertEqual(parse_amount("$25,000+"), Range(25000, None))
        self.assertEqual(parse_amount("$100,000 or less"), Range(None, 100000))
        self.assertEqual(parse_amount("$832,750 or less"), Range(None, 832750))
        self.assertIsNone(parse_amount("Bright Savings"))

    def test_terms(self):
        self.assertEqual(parse_term_months("60 Months"), 60)
        self.assertEqual(parse_term_months("17 Month Fixed Certificate Special"), 17)
        self.assertEqual(parse_term_months("Loan \u2013 Standard (120 mo)"), 120)
        self.assertEqual(parse_term_months("Purchase: 30-Year Fixed Conventional"), 360)
        self.assertIsNone(parse_term_months("2024-27 Model Year"))


class TestKindsAndValues(unittest.TestCase):
    def test_kind_from_header(self):
        self.assertEqual(kind_from_header("Annual Percentage Yield (APY)1"), "apy")
        self.assertEqual(kind_from_header("Dividend Rate"), "dividend_rate")
        self.assertEqual(kind_from_header("Dividend Rate for 1st 6 Months"), "intro_dividend_rate")
        self.assertEqual(kind_from_header("Annual Percentage Rate (As Low As)"), "apr")
        self.assertEqual(kind_from_header("APR (As Low As)"), "apr")
        self.assertEqual(kind_from_header("Rate"), "rate")
        self.assertEqual(kind_from_header("Points"), "points")
        self.assertIsNone(kind_from_header("Min. to Earn"))
        self.assertIsNone(kind_from_header("*Payment Example"))

    def test_clean_percent(self):
        self.assertEqual(clean_percent("3.95%2"), "3.95%")
        self.assertEqual(clean_percent("5.39% APR1"), "5.39%")
        self.assertEqual(clean_percent("6.125%"), "6.125%")
        self.assertEqual(clean_percent("7.89"), "7.89%")
        self.assertIsNone(clean_percent("$1,000"))
        self.assertEqual(percent_value("3.50%"), percent_value("3.5%"))


class TestLabels(unittest.TestCase):
    def test_mortgage_row(self):
        parts = parse_label("Purchase: 30-Year Fixed Conventional $100,000 or less")
        self.assertEqual(parts.product, "fixed conventional")
        qualifiers = dict(parts.qualifiers)
        self.assertEqual(qualifiers["purpose"], "purchase")
        self.assertEqual(qualifiers["term_months"], 360)
        self.assertEqual(qualifiers["amount"], Range(None, 100000))

    def test_certificate_rows(self):
        parts = parse_label("17 Month Fixed Jumbo Certificate Special")
        self.assertEqual(parts.product, "fixed certificate special")
        self.assertEqual(dict(parts.qualifiers), {"term_months": 17, "size": "jumbo"})
        self.assertEqual(parse_label("17 Month Fixed Certificate Special").product, "fixed certificate special")

    def test_ira_row(self):
        parts = parse_label("Roth: 10 Month Fixed Jumbo Special")
        self.assertEqual(
            dict(parts.qualifiers),
            {"ira_type": "roth", "term_months": 10, "size": "jumbo"},
        )

    def test_rows_with_no_product_words(self):
        self.assertEqual(parse_label("2024-27 Model Year").product, "")
        self.assertEqual(parse_label("$10,000-24,999").product, "")
        self.assertEqual(parse_label("60 Months").product, "")

    def test_product_names(self):
        self.assertEqual(parse_label("Bright Savings").product, "bright savings")
        self.assertEqual(parse_label("Business Share Savings").product, "share savings")
        self.assertEqual(parse_label("HELOC \u2013 Standard").product, "heloc standard")
        self.assertEqual(parse_label("1st Rate Checking").product, "1st rate checking")
        self.assertEqual(parse_label("Platinum Visa Rate").product, "platinum visa")
        self.assertEqual(
            parse_label("Investor's Choice Money Market").product,
            "investors choice money market",
        )
        self.assertEqual(parse_label("High-Yield Savings (Business)").product, "high yield savings")

    def test_html_entities(self):
        self.assertEqual(
            parse_label("Abandoned account fee &amp;#45; unclaimed share fee").product,
            "abandoned account fee unclaimed share fee",
        )

    def test_purpose_only_on_mortgage_rows(self):
        self.assertNotIn("purpose", dict(parse_label("Extended Warranty and Purchase Security").qualifiers))
        self.assertNotIn("purpose", dict(parse_label("Purchase Interest Grace Period").qualifiers))
        self.assertEqual(
            dict(parse_label("Refinance: 15-Year Fixed Conventional").qualifiers)["purpose"],
            "refinance",
        )

    def test_amount_without_commas(self):
        qualifiers = dict(parse_label("Refinance: 30-Year Fixed Conventional $100000 or less").qualifiers)
        self.assertEqual(qualifiers["amount"], Range(None, 100000))


class TestKeys(unittest.TestCase):
    def test_segment(self):
        self.assertEqual(segment_of("Business Share Savings"), "business")
        self.assertEqual(segment_of("Share Savings", "Savings Account Rates"), "personal")

    def test_factkey_is_order_independent_and_hashable(self):
        first = FactKey.make(
            "Auto Loan",
            "personal",
            "apr",
            term_months=60,
            model_years=Range(2024, 2027),
        )
        second = FactKey.make(
            "auto loan",
            "personal",
            "apr",
            model_years=Range(2024, 2027),
            term_months=60,
        )
        self.assertEqual(first, second)
        self.assertEqual(len({first, second}), 1)
        self.assertEqual(first.get("term_months"), 60)
        self.assertEqual(
            str(first),
            "auto loan | personal | apr | model_years=2024-2027 | term_months=60",
        )

    def test_factkey_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            FactKey.make("auto loan", "personal", "interest")

    def test_entity_key(self):
        self.assertEqual(str(EntityKey("branch:galena", "address")), "branch:galena | address")

    def test_default_kinds(self):
        self.assertEqual(default_kinds("share savings"), ("apy", "dividend_rate"))
        self.assertEqual(default_kinds("fixed certificate special"), ("apy", "dividend_rate"))
        self.assertEqual(default_kinds("fixed conventional"), ("rate", "apr"))
        self.assertEqual(default_kinds("auto loan"), ("apr",))


if __name__ == "__main__":
    unittest.main()
