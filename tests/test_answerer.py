"""End-to-end tests for solution/answerer.py on a small site shaped like the real one.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "solution"))

from answerer import REFUSAL, Answerer  # noqa: E402
from page_classifier import classify  # noqa: E402
from passages import PassageIndex  # noqa: E402
from resolver import KnowledgeBase  # noqa: E402

SITE = "https://www.dupaco.com"

RATES = """
Auto & Motorcycle Loans

| Loan Program | Term | Annual Percentage Rate (As Low As) |
| --- | --- | --- |
| 2024-27 Model Year | 60 Months | {auto}% APR1 |

Rates are effective as of {eff}. All loans are subject to approval.

| Account Type | Balance Tier | Dividend Rate | Annual Percentage Yield (APY)1 | Min. to Open | Min. to Earn |
| --- | --- | --- | --- | --- | --- |
| Share Savings | $25,000+ | 0.20% | 0.20%1 | $25 | $25,000 |
| Share Savings | $10,000 - $24,999.99 | 0.15% | 0.15%1 | $25 | $10,000 |

Rates are effective as of 06/15/2026 and are subject to change without notice.

Certificates

| Term | Dividend Rate | Annual Percentage Yield (APY) | Min. to Earn |
| --- | --- | --- | --- |
| 12 Months | 3.37% | 3.40%1 | $1,000.00 |

Special Certificates

| Certificate | Dividend Rate | Annual Percentage Yield (APY) | Min. to Earn |
| --- | --- | --- | --- |
| 17 Month Fixed Certificate Special | 4.06% | 4.10%1 | $1,000.00 |
| 17 Month Fixed Jumbo Certificate Special | 4.15% | 4.20%1 | $100,000.00 |

Rates are effective as of 06/15/2026 and are subject to change without notice.
"""
CHECKING = "# Free Checking\n\nThere is no monthly fee. A $30 overdraft fee applies to each item paid.\n"
CALCULATOR = """
Auto Loan Rates

| Loan Program | Term | APR |
| --- | --- | --- |
| 2023-26 Model Year | 60 Months | 5.34% APR |

Rates are effective as of 1/1/2026.
"""
NEWS_OLD = "January 10, 2026\n\n# Dupaco news\n\nIt has nearly 700 employees and 23 branch office locations.\n"
NEWS_NEW = "May 01, 2026\n\n# Dupaco news\n\nIt has nearly 700 employees and 22 branch office locations.\n"
BRANCH = ("June 20, 2025\n\n# New branch opens\n\nLobby\n\nMonday-Friday: 8:30 a.m.-5 p.m.\n\n"
          "Saturday: 8:30 a.m.-12:30 p.m.\n\nSunday: Closed\n\nABOUT DUPACO\n")
BLOG = "March 3, 2024\n\n# A day at work\n\nI help members at our Mullan Avenue branch every week.\n"
CONTACT = "# Contact\n\n[Find a branch](https://www.dupaco.com/contact#locator)\n\nCall us any time.\n"


def site(auto, eff, date):
    return [
        (SITE + "/rates", RATES.format(auto=auto, eff=eff), date),
        (SITE + "/calculators/vehicle-affordability", CALCULATOR, date),
        (SITE + "/newsroom/old-news", NEWS_OLD, date),
        (SITE + "/newsroom/new-news", NEWS_NEW, date),
        (SITE + "/newsroom/branch-opening", BRANCH, date),
        (SITE + "/learn/blog/a-day-at-work", BLOG, date),
        (SITE + "/contact", CONTACT, date),
        (SITE + "/personal/checking-accounts", CHECKING, date),
    ]


def build():
    kb, indexes = KnowledgeBase(), {}
    for sid, taken, auto, eff in [("01", "2026-05-12", "5.19", "05/01/2026"), ("05", "2026-09-28", "5.39", "08/31/2026")]:
        pages = site(auto, eff, taken)
        previous = kb.taken_on[-1] if kb.taken_on else None
        kb.add_snapshot(sid, taken, pages)
        index = PassageIndex(sid, taken)
        for url, md, fetched in pages:
            index.add_page(url, "", md, classify(url, md, fetched, previous))
        index.finish()
        indexes[sid] = index
    return Answerer(kb, indexes)


class TestAnswers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = build()

    def ask(self, question, as_of=None):
        return self.a.answer(question, as_of)

    def test_current_rate_beats_stale_calculator(self):
        d = self.ask("What's the APR for a 60-month auto loan on a 2025 vehicle?")
        self.assertIn("5.39%", d.text)
        self.assertNotIn("5.34", d.text)
        self.assertIn("calculators/vehicle-affordability shows 5.34%", d.conflict_note)
        self.assertTrue(d.sources[0][0].endswith("/rates"))

    def test_as_of(self):
        d = self.ask("What was the 60-month auto APR for a 2025 car?", as_of="2026-05-31")
        self.assertIn("5.19%", d.text)
        self.assertNotIn("5.39", d.text)

    def test_tier_and_highest(self):
        self.assertIn("0.15%", self.ask("How much does Share Savings pay on $15,000?").text)
        highest = self.ask("What is the highest Share Savings APY?").text
        self.assertIn("0.20%", highest)
        self.assertIn("$25,000", highest)

    def test_newest_article_wins_for_counts(self):
        d = self.ask("How many branches does Dupaco have?")
        self.assertIn("22 branch", d.text)

    def test_hours(self):
        d = self.ask("What are your lobby hours?")
        self.assertIn("8:30 a.m.", d.text)
        self.assertIn("12:30 p.m.", d.text)
        self.assertNotIn("ABOUT", d.text)

    def test_link(self):
        self.assertIn("dupaco.com/contact", self.ask("What's the URL for the branch locator?").text)

    def test_tier_named_without_rate_words(self):
        d = self.ask("What's the 17-month jumbo special?")
        self.assertIn("4.20%", d.text)
        self.assertNotIn("4.10%", d.text)

    def test_highest_looks_across_the_family(self):
        d = self.ask("What is the highest certificate APY?")
        self.assertIn("4.20%", d.text)                # the jumbo special, not the 12-month regular

    def test_a_tier_the_product_does_not_have_is_refused(self):
        self.assertEqual(self.ask("What rate does a jumbo Share Savings account earn?").text, REFUSAL)

    def test_minimum_to_open(self):
        d = self.ask("What's the minimum to open a Share Savings account?")
        self.assertIn("$25", d.text)
        self.assertNotIn("%", d.text)
        self.assertNotIn("$25,000", d.text)        # one value for every tier, said once

    def test_dates_in_the_question_and_partial_dates(self):
        self.assertIn("5.19%", self.ask("What was the 60-month auto APR for a 2025 car in May 2026?").text)
        self.assertIn("5.19%", self.ask("60-month auto APR for a 2025 car", as_of="2026-05").text)

    def test_before_the_first_snapshot(self):
        self.assertEqual(self.ask("Share Savings APY on $15,000", as_of="2026-02-01").text, REFUSAL)
        self.assertIn("23 branch", self.ask("How many branches does Dupaco have?", as_of="2026-02-01").text)

    def test_fee(self):
        self.assertIn("$30", self.ask("What is the overdraft fee?").text)

    def test_trace(self):
        self.ask("What's the APR for a 60-month auto loan on a 2025 vehicle?")
        self.assertEqual(self.a.trace["route"], "rate table")
        self.assertEqual(self.a.trace["products"], ["auto loan (personal)"])

    def test_refusals_have_no_numbers(self):
        for question in ["What is your rate on a 7/1 adjustable-rate mortgage?",
                         "What interest rate do you charge on a construction loan?",
                         "Can you give me the lobby hours for the Mullan Avenue branch?"]:
            d = self.ask(question)
            self.assertEqual(d.text, REFUSAL, question)
            self.assertFalse(d.confident)
            self.assertFalse(re.search(r"\d", d.text))


if __name__ == "__main__":
    unittest.main()
