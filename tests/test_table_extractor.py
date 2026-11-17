"""Tests for solution/table_extractor.py.

Each page below is a few lines shaped like the real snapshot pages (same headers,
title lines and notes), so the tests run without the snapshot data.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "solution"))

from fact_key import Range  # noqa: E402
from table_extractor import (  # noqa: E402
    clean_rate_value,
    compose_product,
    detect_family,
    extract_tables,
    parse_effective,
)

RATES_URL = "https://www.dupaco.com/rates"


def extract(markdown, url=RATES_URL):
    return extract_tables(markdown, url, "05_test", "2026-09-28T00:00:00Z")


def find(facts, **want):
    """Facts whose key has every given field / qualifier."""
    out = []
    for f in facts:
        fields = {"product": f.key.product, "segment": f.key.segment, "kind": f.key.kind, **dict(f.key.qualifiers)}
        if all(fields.get(k) == v for k, v in want.items()):
            out.append(f)
    return out


AUTO_ON_RATES = """
Auto & Motorcycle Loans

Auto & Motorcycle Loans

| Loan Program | Term | Annual Percentage Rate (As Low As) |
| --- | --- | --- |
| 2024-27 Model Year | 48 Months | 5.19% APR1 |
| 2024-27 Model Year | 60 Months | 5.39% APR1 |
| 2021-23 Model Year | 60 Months | 5.64% APR1 |

Rates are effective as of 08/31/2026. All loans are subject to approval.
"""

CARDS = """
Credit Cards

Platinum Visa

| Card Type | APR |
| --- | --- |
| Introductory Balance Transfer Rate | 2.90%1 |
| Standard | 12.50% \u2013 19.50%1 |

Rates are effective as of 01/02/2026. All loans are subject to approval.
"""

SAVINGS = """
| Account Type | Balance Tier | Dividend Rate | Annual Percentage Yield (APY)1 | Min. to Open | Min. to Earn |
| --- | --- | --- | --- | --- | --- |
| Share Savings | $25,000+ | 0.20% | 0.20%1 | $25.00 | $25,000.00 |
| Share Savings | $10,000 \u2013 $24,999.99 | 0.15% | 0.15%1 | $25.00 | $10,000.00 |
|  | $2,500 \u2013 $9,999.99 | 0.10% | 0.10%1 | $25.00 | $2,500.00 |

Rates are effective as of 06/15/2026 and are subject to change without notice.
"""

CERTIFICATES = """
| Certificate | Dividend Rate | Annual Percentage Yield (APY) | Min. to Earn |
| --- | --- | --- | --- |
| 17 Month Fixed Jumbo Certificate Special | 4.15% | 4.20%1 | $100,000.00 |
| 60 Month | 1.79% | 1.80%1 | $1,000.00 |

Rates are effective as of 09/01/2026 and are subject to change without notice.

| IRA Type | Dividend Rate | Annual Percentage Yield (APY) | Min. to Earn |
| --- | --- | --- | --- |
| Roth: 17 Month Fixed Jumbo Special | 4.15% | 4.20%1 | $100,000.00 |
| Roth: 60 Months | 1.79% | 1.80%1 | $1,000.00 |
"""

MORTGAGE = """
Home Loans

Home Loans - Purchase

| Program | Rate | Points | APR | \\*Payment Example |
| --- | --- | --- | --- | --- |
| Purchase: 30-Year Fixed Conventional $100,000 or less | 7.13% | 0 | 7.27% | $674 |

Rates are effective as of 09/25/2026. Rates may be as low as quoted rates.
"""


class TestHelpers(unittest.TestCase):
    def test_clean_rate_value(self):
        self.assertEqual(clean_rate_value("5.39% APR1"), "5.39%")
        self.assertEqual(clean_rate_value("12.50% \u2013 19.50%1"), "12.50% - 19.50%")
        self.assertEqual(clean_rate_value("4.20%1"), "4.20%")
        self.assertIsNone(clean_rate_value("\u2014"))

    def test_parse_effective(self):
        self.assertEqual(parse_effective("Rates are effective as of 08/31/2026. All loans..."), "2026-08-31")
        self.assertEqual(parse_effective("**Rates are effective as of 1/1/2026."), "2026-01-01")
        self.assertIsNone(parse_effective("All loans are subject to approval."))

    def test_detect_family(self):
        self.assertEqual(detect_family(["Auto & Motorcycle Loans"]), "auto loan")
        self.assertEqual(detect_family(["ATV, Snowmobile & Jet Ski Loans", "Recreational Loans"]), "powersports loan")
        self.assertEqual(detect_family(["Boat & RV Loans"]), "boat & rv loan")
        self.assertEqual(detect_family(["Home Equity Line of Credit (HELOC)"]), "heloc")
        self.assertEqual(detect_family(["Business Platinum Visa"]), "platinum visa")
        self.assertEqual(detect_family(["", "IRA Type"]), "ira")
        self.assertIsNone(detect_family(["Account Type"]))

    def test_compose_product(self):
        self.assertEqual(compose_product("ira", "fixed special"), "ira fixed special")
        self.assertEqual(compose_product("certificate", "fixed certificate special"), "fixed certificate special")
        self.assertEqual(compose_product("auto loan", ""), "auto loan")
        self.assertEqual(compose_product("heloc", "heloc standard"), "heloc")
        self.assertEqual(compose_product(None, "share savings"), "share savings")


class TestExtract(unittest.TestCase):
    def test_auto_table(self):
        facts = extract(AUTO_ON_RATES)
        self.assertEqual(len(facts), 3)
        f = find(facts, product="auto loan", kind="apr", term_months=60, model_years=Range(2024, 2027))
        self.assertEqual([x.value for x in f], ["5.39%"])
        self.assertEqual(f[0].effective, "2026-08-31")
        self.assertEqual(f[0].context, "Auto & Motorcycle Loans > Auto & Motorcycle Loans")
        self.assertEqual(f[0].quote, "| 2024-27 Model Year | 60 Months | 5.39% APR1 |")

    def test_cards_intro_and_range(self):
        facts = extract(CARDS)
        self.assertEqual(find(facts, product="platinum visa", kind="apr")[0].value, "12.50% - 19.50%")
        intro = find(facts, kind="intro_apr")
        self.assertEqual((intro[0].key.product, intro[0].value), ("platinum visa balance transfer", "2.90%"))

    def test_savings_tiers_use_balance_column_not_minimums(self):
        facts = extract(SAVINGS)
        f = find(facts, product="share savings", kind="apy", amount=Range(10000, 24999.99))
        self.assertEqual([x.value for x in f], ["0.15%"])
        self.assertEqual(str(f[0].key.get("amount")), "10000-24999.99")
        mins = find(facts, product="share savings", kind="min_open", amount=Range(10000, 24999.99))
        self.assertEqual([x.value for x in mins], ["$25.00"])                  # minimum columns are facts too
        # blank label carries the product down from the row above
        self.assertEqual(find(facts, product="share savings", kind="apy", amount=Range(2500, 9999.99))[0].value, "0.10%")

    def test_certificates_and_ira(self):
        facts = extract(CERTIFICATES)
        self.assertEqual(find(facts, product="fixed certificate special", kind="apy", size="jumbo", term_months=17)[0].value, "4.20%")
        self.assertEqual(find(facts, product="certificate", kind="apy", term_months=60)[0].value, "1.80%")
        self.assertEqual(find(facts, product="ira fixed special", ira_type="roth", kind="apy", term_months=17)[0].value, "4.20%")
        self.assertEqual(find(facts, product="ira", ira_type="roth", kind="dividend_rate", term_months=60)[0].value, "1.79%")
        self.assertIsNone(facts[-1].effective)          # IRA table has no note under it

    def test_mortgage_rate_and_apr_points_skipped(self):
        facts = extract(MORTGAGE)
        self.assertEqual(sorted(f.key.kind for f in facts), ["apr", "rate"])
        f = find(facts, product="mortgage fixed conventional", purpose="purchase", term_months=360, kind="rate")
        self.assertEqual(f[0].value, "7.13%")
        self.assertEqual(f[0].key.get("amount"), Range(None, 100000))

    def test_business_segment_from_title(self):
        md = "Business Savings\n\n" + SAVINGS
        self.assertTrue(all(f.key.segment == "business" for f in extract(md)))

    def test_family_from_url_when_titles_say_nothing(self):
        md = ("## Get on the road with low interest rates\n\n"
              "| Loan Program | Term | Annual Percentage Rate (As Low As) |\n| --- | --- | --- |\n"
              "| 2024-27 Model Year | 60 Months | 5.39% APR1 |\n")
        facts = extract(md, url="https://www.dupaco.com/personal/loans/auto-motorcycle-loans")
        self.assertEqual(facts[0].key.product, "auto loan")

    def test_unit_in_cell_beats_wrong_header(self):
        md = ("Auto Loan Rates\n\n| Account Type | Balance Tier | Annual Percentage Yield |\n| --- | --- | --- |\n"
              "| 2023-26 Model Year | 60 Months | 5.34% APR |\n")
        facts = extract(md, url="https://www.dupaco.com/calculators/vehicle-affordability")
        self.assertEqual((facts[0].key.kind, facts[0].value), ("apr", "5.34%"))

    def test_tables_without_rate_columns_are_skipped(self):
        md = ("| Year | Make/ Model | Minimum Bid |\n| --- | --- | --- |\n| 2018 | Alfa Romeo Stelvio | $8,550.00 |\n\n"
              "| Category | Before taxes | After taxes |\n| --- | --- | --- |\n| With 2.9% inflation | $1,119 | $984 |\n")
        self.assertEqual(extract(md), [])


class TestRateLines(unittest.TestCase):
    def test_labelled_rate_lines(self):
        from table_extractor import extract_rate_lines
        facts = extract_rate_lines("# Certificates\n\n6-month certificate APY: 3.50%\n\n"
                                   "Summer special: 6-month certificate APY 2.95% (offer ended 2025-09-30)\n",
                                   "https://example-cu.test/rates/certificates", "02", "2026-03-01")
        self.assertEqual([(f.key.product, f.key.kind, f.key.get("term_months"), f.value, f.ended) for f in facts],
                         [("certificate", "apy", 6, "3.50%", False), ("certificate", "apy", 6, "2.95%", True)])

    def test_prose_sentences_are_not_rate_lines(self):
        from table_extractor import extract_rate_lines
        self.assertEqual(extract_rate_lines("Dupaco members who opened a certificate last year earned more.\n"
                                            "| 12 Months | 3.37% | 3.40%1 |\n", "u", "01", "2026-05-12"), [])


if __name__ == "__main__":
    unittest.main()
