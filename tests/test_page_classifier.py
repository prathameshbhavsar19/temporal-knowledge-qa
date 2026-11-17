"""Tests for solution/page_classifier.py.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "solution"))

from page_classifier import canonical_path, classify, is_variant, page_type, publish_date  # noqa: E402

SITE = "https://www.dupaco.com"


class TestPaths(unittest.TestCase):
    def test_canonical_path(self):
        self.assertEqual(canonical_path(SITE + "/personal/loans/auto-loans/"), "/personal/loans/auto-loans")
        self.assertEqual(canonical_path(SITE + "/privacy-policy?campaignId=123#top"), "/privacy-policy")
        self.assertEqual(canonical_path(SITE), "/")

    def test_variants(self):
        self.assertTrue(is_variant(SITE + "/privacy-policy?campaignId=123"))
        self.assertTrue(is_variant(SITE + "/personal/savings-account#rates"))
        self.assertFalse(is_variant(SITE + "/rates"))


class TestTiers(unittest.TestCase):
    def test_page_types(self):
        cases = {
            "/rates": (1, "rate_sheet"),
            "/business-rates": (1, "rate_sheet"),
            "/personal/loans/auto-motorcycle-loans": (2, "product"),
            "/business/savings-accounts": (2, "product"),
            "/contact": (3, "site"),
            "/switch-and-earn": (3, "site"),
            "/newsroom/forbes-ranks-dupaco-as-a-top-credit-union-in-america-3": (4, "news"),
            "/learn/master-your-money/homeownership": (5, "blog"),
            "/calculators/vehicle-affordability": (6, "calculator"),
            "/hubfs/rate-sheet": (6, "pdf"),
            "/membermoneymarket-lp": (6, "promo"),
        }
        for path, want in cases.items():
            self.assertEqual(page_type(path), want, path)


class TestDates(unittest.TestCase):
    def test_publish_date_is_the_first_date_near_the_top(self):
        md = ("# Dupaco evolves with purpose and member needs, plans Hy-Vee branch closure\n\n"
              "June 20, 2025\n\nThe branch will close March 31, 2026.")
        self.assertEqual(publish_date(md), "2025-06-20")
        self.assertIsNone(publish_date("# No date here\n\nJust text."))

    def test_frozen_when_not_refetched_since_previous_snapshot(self):
        frozen = classify(SITE + "/switch-and-earn", "", "2026-06-09T10:00:00Z", previous_taken_on="2026-08-27")
        fresh = classify(SITE + "/rates", "", "2026-09-28T10:00:00Z", previous_taken_on="2026-08-27")
        self.assertTrue(frozen.frozen)
        self.assertFalse(fresh.frozen)
        self.assertFalse(classify(SITE + "/rates", "", "2026-05-12", None).frozen)   # first snapshot

    def test_publish_date_only_for_news(self):
        md = "June 18, 2024\n\nForbes ranks Dupaco..."
        self.assertEqual(classify(SITE + "/newsroom/forbes-3", md, "2026-09-28").publish_date, "2024-06-18")
        self.assertIsNone(classify(SITE + "/rates", md, "2026-09-28").publish_date)


if __name__ == "__main__":
    unittest.main()
