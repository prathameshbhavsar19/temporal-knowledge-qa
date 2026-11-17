"""Tests for solution/question_parser.py.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "solution"))

from question_parser import normalize_date, parse, stem, words_to_number  # noqa: E402


class TestWords(unittest.TestCase):
    def test_member_words_become_site_words(self):
        self.assertIn("auto", parse("What's the rate on a car loan?").words)
        self.assertIn("certificate", parse("What is your best CD rate?").words)
        self.assertIn("visa", parse("What credit cards do you offer?").words)

    def test_stem(self):
        self.assertEqual(stem("branches"), "branch")
        self.assertEqual(stem("loans"), "loan")
        self.assertEqual(stem("savings"), "savings")
        self.assertEqual(stem("forbes"), "forbes")


class TestIntent(unittest.TestCase):
    def test_rate_and_kind(self):
        q = parse("What's the APR for a 60-month auto loan on a 2025 model-year vehicle?")
        self.assertTrue(q.rate)
        self.assertEqual(q.kind, "apr")
        self.assertEqual(q.qualifiers, {"term_months": 60, "model_years": 2025})

    def test_how_much_does_it_pay_is_a_rate_question(self):
        self.assertTrue(parse("How much does Share Savings pay on a $15,000 balance?").rate)

    def test_terms(self):
        self.assertEqual(parse("five-year car loan rate").qualifiers["term_months"], 60)
        self.assertEqual(parse("30-year mortgage rate").qualifiers["term_months"], 360)
        self.assertNotIn("term_months", parse("rate for a 2025 model-year car").qualifiers)

    def test_as_of_year_is_not_a_model_year(self):
        q = parse("What was the auto loan rate as of May 2026?")
        self.assertNotIn("model_years", q.qualifiers)

    def test_other_qualifiers(self):
        q = parse("What's the interest rate to refinance a $90,000 30-year mortgage?")
        self.assertEqual(q.qualifiers["purpose"], "refinance")
        self.assertEqual(q.qualifiers["amount"], 90000.0)
        self.assertEqual(q.kind, "rate")
        self.assertEqual(parse("10-month jumbo special").qualifiers["size"], "jumbo")
        self.assertEqual(parse("Roth IRA certificate rate").qualifiers["ira_type"], "roth")

    def test_link_listing_highest(self):
        self.assertTrue(parse("What link should I use to see certificate rates?").url)
        self.assertTrue(parse("How do I join Dupaco online?").url)
        self.assertTrue(parse("What credit cards do you offer and what are their APRs?").listing)
        self.assertTrue(parse("What is the highest Share Savings APY?").highest)

    def test_attributes(self):
        self.assertEqual(parse("What are your lobby hours?").attribute, "hours")
        self.assertEqual(parse("What is the address of the Galena Branch?").attribute, "address")
        self.assertEqual(parse("How many branches does Dupaco have?").attribute, "count")
        self.assertEqual(parse("When does the Hy-Vee branch close?").attribute, "date")
        self.assertEqual(parse("Is the Hy-Vee branch still open today?").attribute, "date")

    def test_amounts_in_the_forms_members_write_them(self):
        amount = lambda text: parse(text).qualifiers.get("amount")
        self.assertEqual(amount("Share Savings rate for $15k"), 15000)
        self.assertEqual(amount("rate on 15K in savings"), 15000)
        self.assertEqual(amount("savings rate on ten thousand dollars"), 10000)
        self.assertEqual(amount("savings with between $10k and $25k"), 10000)
        self.assertEqual(amount("savings balance under $2,500"), 2499.99)
        self.assertEqual(amount("mortgage of 250,000 dollars"), 250000)
        self.assertIsNone(amount("Can I roll my 401k into an IRA?"))
        self.assertEqual(words_to_number("twenty five thousand"), 25000)

    def test_shorthand_terms_and_model_years(self):
        q = parse("5yr car loan for a '25 model")
        self.assertEqual(q.qualifiers["term_months"], 60)
        self.assertEqual(q.qualifiers["model_years"], 2025)
        self.assertEqual(parse("Auto loan APR for a MY2024 car, 60 months").qualifiers["model_years"], 2024)
        self.assertEqual(parse("72 mo truck loan").qualifiers["term_months"], 72)
        self.assertNotIn("term_months", parse("loan for a car that is 3 years old").qualifiers)

    def test_dates_written_in_the_question(self):
        self.assertEqual(parse("What was the jumbo special rate in May 2026?").as_of, "2026-05-31")
        self.assertEqual(parse("What was the 30-year rate as of June 9, 2026?").as_of, "2026-06-09")
        self.assertEqual(parse("auto APR as of 2026-05").as_of, "2026-05-31")
        self.assertEqual(parse("What was the special rate back in May?").as_of_month, 5)
        self.assertIsNone(parse("Is the special still offered in May 2026?").as_of)   # not past tense
        self.assertNotIn("model_years", parse("What was the 60-month auto APR in May 2026?").qualifiers)

    def test_normalize_date(self):
        self.assertEqual(normalize_date("2026-05"), "2026-05-31")
        self.assertEqual(normalize_date("2026-05-31T10:00:00"), "2026-05-31")
        self.assertEqual(normalize_date("May 2026"), "2026-05-31")
        self.assertEqual(normalize_date("5/19/2026"), "2026-05-19")
        self.assertIsNone(normalize_date(None))

    def test_minimums_fees_and_years(self):
        self.assertEqual(parse("What's the minimum to open a Share Savings account?").kind, "min_open")
        self.assertTrue(parse("What's the minimum deposit to open a certificate?").rate)
        self.assertEqual(parse("What is the overdraft fee?").attribute, "fee")
        self.assertIsNone(parse("What rate do you charge on a personal loan?").attribute)
        self.assertEqual(parse("What year was Dupaco founded?").attribute, "year")

    def test_entities(self):
        self.assertEqual(parse("Can you give me the lobby hours for the Mullan Avenue branch?").entities,
                         ["Mullan Avenue"])
        self.assertEqual(parse("What is the address of the Galena Branch?").entities, ["Galena"])
        self.assertEqual(parse("What are your lobby hours?").entities, [])


if __name__ == "__main__":
    unittest.main()
