"""Tests for solution/resolver.py, using small pages shaped like the real snapshots.

The story in these tests mirrors the real data:
  - /rates moves the 60-month auto APR from 5.19% (May) to 5.39% (September)
  - a calculator keeps 5.34% under an older model-year label the whole time
  - /personal/savings-account lags /rates on a certificate special in one snapshot

Run from the repo root:  python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "solution"))

from resolver import KnowledgeBase, tier_label, value_key  # noqa: E402
from fact_key import FactKey, Range  # noqa: E402

SITE = "https://www.dupaco.com"


def auto_table(label, rate, effective):
    return (f"Auto & Motorcycle Loans\n\n"
            f"| Loan Program | Term | Annual Percentage Rate (As Low As) |\n| --- | --- | --- |\n"
            f"| {label} | 60 Months | {rate}% APR1 |\n\n"
            f"Rates are effective as of {effective}. All loans are subject to approval.\n")


def cert_table(apy, effective):
    return ("| Certificate | Dividend Rate | Annual Percentage Yield (APY) | Min. to Earn |\n| --- | --- | --- | --- |\n"
            f"| 17 Month Fixed Certificate Special | {apy}% | {apy}%1 | $1,000.00 |\n\n"
            f"Rates are effective as of {effective} and are subject to change without notice.\n")


MAY = [
    (SITE + "/rates", auto_table("2023-26 Model Year", "5.19", "05/01/2026") + cert_table("3.95", "05/01/2026"), "2026-05-12"),
    (SITE + "/personal/savings-account", cert_table("3.95", "05/01/2026"), "2026-05-12"),
    (SITE + "/calculators/vehicle-affordability", auto_table("2023-26 Model Year", "5.34", "1/1/2026"), "2026-05-12"),
]
AUGUST = [
    (SITE + "/rates", auto_table("2023-26 Model Year", "5.19", "05/01/2026") + cert_table("3.95", "07/14/2026"), "2026-08-27"),
    (SITE + "/personal/savings-account", cert_table("3.85", "06/02/2026"), "2026-08-27"),
    (SITE + "/calculators/vehicle-affordability", auto_table("2023-26 Model Year", "5.34", "1/1/2026"), "2026-08-27"),
]
SEPTEMBER = [
    (SITE + "/rates", auto_table("2024-27 Model Year", "5.39", "08/31/2026") + cert_table("3.95", "07/14/2026"), "2026-09-28"),
    (SITE + "/personal/savings-account", cert_table("3.95", "07/14/2026"), "2026-09-28"),
    (SITE + "/calculators/vehicle-affordability", auto_table("2023-26 Model Year", "5.34", "1/1/2026"), "2026-09-28"),
    (SITE + "/calculators/vehicle-affordability?utm=email", auto_table("2023-26 Model Year", "9.99", "1/1/2026"), "2026-09-28"),
]


def build():
    kb = KnowledgeBase()
    kb.add_snapshot("01_may", "2026-05-12", MAY)
    kb.add_snapshot("04_aug", "2026-08-27", AUGUST)
    kb.add_snapshot("05_sep", "2026-09-28", SEPTEMBER)
    return kb


class TestSnapshots(unittest.TestCase):
    def test_snapshot_for(self):
        kb = build()
        self.assertEqual(kb.snapshot_for(None), "05_sep")
        self.assertEqual(kb.snapshot_for("2026-05-31"), "01_may")
        self.assertEqual(kb.snapshot_for("2026-08-27"), "04_aug")      # taken that day counts
        self.assertIsNone(kb.snapshot_for("2026-01-01"))                # before any snapshot


class TestWinners(unittest.TestCase):
    def setUp(self):
        self.kb = build()

    def auto(self, as_of=None):
        tiers = self.kb.lookup("auto loan", "personal", "apr", as_of=as_of, term_months=60, model_years=2025)
        self.assertEqual(len(tiers), 1)
        return tiers[0].winner

    def test_newest_effective_date_beats_stale_calculator(self):
        w = self.auto()
        self.assertEqual((w.value, w.page.path), ("5.39%", "/rates"))

    def test_as_of_answers_from_that_snapshot(self):
        self.assertEqual(self.auto("2026-05-31").value, "5.19%")

    def test_overlapping_model_years_are_one_tier(self):
        tiers = self.kb.lookup("auto loan", "personal", "apr", term_months=60)
        self.assertEqual(len(tiers), 1)                      # 2023-26 and 2024-27 joined
        self.assertEqual(sorted({c.value for c in tiers[0].candidates}), ["5.34%", "5.39%"])

    def test_query_variant_urls_are_ignored(self):
        values = {c.value for t in self.kb.lookup("auto loan", "personal", "apr") for c in t.candidates}
        self.assertNotIn("9.99%", values)

    def test_table_date_not_majority(self):
        w = self.kb.lookup("fixed certificate special", "personal", "apy", as_of="2026-08-30", term_months=17)[0].winner
        self.assertEqual((w.value, w.page.path), ("3.95%", "/rates"))

    def test_query_names_a_qualifier_the_fact_lacks(self):
        self.assertEqual(self.kb.lookup("auto loan", "personal", "apr", size="jumbo"), [])


class TestConflicts(unittest.TestCase):
    def setUp(self):
        self.conflicts = build().conflicts()

    def within(self):
        return [c for c in self.conflicts if "changed between snapshots" not in c.topic]

    def test_one_record_per_stale_page_and_product(self):
        self.assertEqual(len(self.within()), 2)

    def test_change_between_snapshots(self):
        changes = [c for c in self.conflicts if "changed between snapshots" in c.topic]
        self.assertEqual(len(changes), 1)                               # the auto APR moved; one record
        c = changes[0]
        self.assertTrue(c.topic.startswith("auto loan"))
        self.assertEqual(c.values, ["5.19%", "5.39%"])                  # old first, newest last
        self.assertIn("5.39%", c.resolution)
        self.assertEqual(c.snapshot_id, "05_sep")

    def test_auto_record(self):
        c = next(x for x in self.conflicts if x.topic.startswith("auto loan"))
        self.assertEqual(c.snapshot_id, "05_sep")                       # latest occurrence kept
        self.assertEqual(c.values, ["5.34%", "5.39%"])
        self.assertIn("5.39%", c.resolution)
        self.assertIn("/calculators/vehicle-affordability", c.topic)
        self.assertIn("60-month", c.topic)
        self.assertIn("effective 2026-08-31", c.reason)
        self.assertEqual({s[0] for s in c.sources},
                         {SITE + "/rates", SITE + "/calculators/vehicle-affordability"})

    def test_certificate_record_from_august_only(self):
        c = next(x for x in self.conflicts if x.topic.startswith("fixed certificate special"))
        self.assertEqual(c.snapshot_id, "04_aug")
        self.assertIn("3.85%", c.values)
        self.assertIn("3.95%", c.resolution)


class TestValuesAndDates(unittest.TestCase):
    def test_values_compare_by_number(self):
        self.assertEqual(value_key("$25"), value_key("$25.00"))
        self.assertEqual(value_key("3.5%"), value_key("3.50%"))
        self.assertNotEqual(value_key("3.95%"), value_key("3.85%"))

    def test_minimum_columns_never_conflict(self):
        kb = KnowledgeBase()
        table = ("| Account | Dividend Rate | APY | Min. to Open |\n| --- | --- | --- | --- |\n"
                 "| Share Savings | 0.10% | 0.10% | {m} |\n\nRates are effective as of 06/15/2026.\n")
        kb.add_snapshot("01", "2026-06-20", [(SITE + "/rates", table.format(m="$25"), "2026-06-20"),
                                             (SITE + "/personal/savings-account", table.format(m="$25.00"), "2026-06-20"),
                                             (SITE + "/savings", table.format(m="$5"), "2026-06-20")])
        self.assertEqual(kb.conflicts(), [])
        self.assertTrue(kb.lookup("share savings", "personal", "min_open"))

    def test_before_the_first_snapshot_only_tables_already_effective(self):
        kb = build()
        self.assertEqual(kb.view("2026-03-01"), ("01_may", "2026-03-01"))
        tiers = kb.lookup("auto loan", "personal", "apr", as_of="2026-03-01", term_months=60)
        self.assertEqual([t.winner.value for t in tiers], ["5.34%"])      # /rates' table starts 05/01
        self.assertEqual(kb.lookup("fixed certificate special", "personal", as_of="2026-03-01"), [])

    def test_product_facts(self):
        kb = build()
        self.assertIn("auto & motorcycle loans", kb.aliases[("auto loan", "personal")])
        self.assertEqual(kb.product_tier("auto loan", "personal"), 1)
        self.assertEqual(kb.qualifier_names("auto loan", "personal"), {"term_months", "model_years"})


class TestLabels(unittest.TestCase):
    def test_tier_label(self):
        k = FactKey.make("mortgage fixed conventional", "personal", "rate",
                         term_months=360, purpose="purchase", amount=Range(None, 100000))
        self.assertEqual(tier_label(k), "30-year, purchase, up to $100,000")
        k = FactKey.make("auto loan", "personal", "apr", term_months=60, model_years=Range(2024, 2027))
        self.assertEqual(tier_label(k), "60-month, 2024-2027 model years")


if __name__ == "__main__":
    unittest.main()
