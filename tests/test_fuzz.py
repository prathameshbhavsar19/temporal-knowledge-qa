"""Robustness: odd questions and odd dates must never crash, hang or leak numbers.

Uses the small site from test_answerer.py. Run from the repo root:
    python -m unittest discover -s tests -v
"""
import random
import re
import string
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "solution"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_answerer import build  # noqa: E402

ODD_QUESTIONS = [
    "", " ", "?", "???", "0", "12345678901234567890", "%%%", "$$$", "rate rate rate rate",
    "APR", "apy?", "What is the", "DROP TABLE rates;", "<script>alert(1)</script>",
    "\u00bfCu\u00e1l es la tasa de inter\u00e9s?", "\u4f60\u597d", "\U0001f697 loan rate \U0001f4b8",
    "What's the APR for a 60-month auto loan on a 2025 vehicle?" * 40,
    "what is the rate for a 99999-month loan on a 1800 model year car for $-5",
    "jumbo jumbo roth traditional 10-month 17-month special",
    "How many how many how many",
    "What was the rate in Smarch 2026?",
]
ODD_DATES = [None, "", "2026-13", "2026-02-31", "yesterday", "1999-01-01", "2099-12-31", "2026-05",
             "5/19/2026", "2026-05-31T10:00:00Z", 12345]


class TestFuzz(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = build()

    def check(self, question, as_of=None):
        started = time.perf_counter()
        d = self.a.answer(question, as_of)
        took = time.perf_counter() - started
        self.assertIsInstance(d.text, str)
        self.assertTrue(d.text)
        self.assertLess(took, 2.0, question[:60])
        if not d.confident:
            self.assertFalse(re.search(r"\d", d.text), question[:60])   # a refusal never states a number

    def test_odd_questions(self):
        for question in ODD_QUESTIONS:
            self.check(question)

    def test_odd_dates(self):
        for as_of in ODD_DATES:
            self.check("What's the APR for a 60-month auto loan on a 2025 vehicle?", as_of)
            self.check("How many branches does Dupaco have?", as_of)

    def test_random_text(self):
        rng = random.Random(7)
        alphabet = string.ascii_letters + string.digits + string.punctuation + " " * 10
        words = ["rate", "apr", "apy", "loan", "auto", "savings", "jumbo", "month", "year", "$", "k",
                 "hours", "branch", "when", "how many", "link", "as of", "May", "2026", "special"]
        for _ in range(200):
            junk = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
            mixed = " ".join(rng.choice(words) for _ in range(rng.randint(1, 12)))
            self.check(junk)
            self.check(mixed)

    def test_same_question_same_answer(self):
        q = "What is the highest certificate APY?"
        self.assertEqual(self.a.answer(q).text, self.a.answer(q).text)


if __name__ == "__main__":
    unittest.main()
