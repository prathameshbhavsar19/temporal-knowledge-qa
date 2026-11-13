"""Fact keys: the shared vocabulary of the system.

The table extractor writes facts under a FactKey, the question parser turns a
question into a FactKey, and the resolver groups facts by FactKey. If all three
spell a key the same way, a correct fact can always be found.

A FactKey says WHAT a number is about:
    product     canonical product name, built from the table text
                e.g. "fixed certificate special", "share savings"
    segment     "personal" or "business"
    kind        rate | apr | apy | dividend_rate | intro_apr | intro_dividend_rate | points
    qualifiers  what splits one product into tiers:
                term_months, model_years, amount, purpose, ira_type, size

Facts from prose (branch counts, addresses, closing dates, links) use the
simpler EntityKey(entity, attribute).

Only the standard library is used.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional

_DASHES = re.compile(r"[\u2010-\u2015\u2212]")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, decode HTML entities, normalize dashes and whitespace."""
    text = text or ""
    for _ in range(2):
        text = html.unescape(text)
    text = text.replace("\u00a0", " ").replace("*", "")
    text = _DASHES.sub("-", text)
    return _SPACES.sub(" ", text).strip().lower()


@dataclass(frozen=True)
class Range:
    """A closed range; None means open on that side."""

    lo: Optional[float] = None
    hi: Optional[float] = None

    def contains(self, x: float) -> bool:
        return (self.lo is None or x >= self.lo) and (self.hi is None or x <= self.hi)

    def __str__(self) -> str:
        def fmt(value: Optional[float]) -> str:
            if value is None:
                return ""
            return f"{value:g}" if value != int(value) else str(int(value))

        return f"{fmt(self.lo)}-{fmt(self.hi)}"


_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_MONEY_SPAN = re.compile(rf"\$\s*{_NUM}\s*-\s*\$?\s*{_NUM}")
_MONEY_UP = re.compile(rf"\$\s*{_NUM}\s*(?:\+|and (?:up|above|over)|or more)")
_MONEY_DOWN = re.compile(rf"(?:\$\s*{_NUM}\s*or less|up to \$\s*{_NUM})")


def _num(value: str) -> float:
    return float(value.replace(",", ""))


def parse_amount(text: str) -> Optional[Range]:
    """Parse dollar tiers such as '$10,000 - $24,999.99' or '$25,000+'."""
    text = normalize(text)
    match = _MONEY_SPAN.search(text)
    if match:
        return Range(_num(match.group(1)), _num(match.group(2)))
    match = _MONEY_UP.search(text)
    if match:
        return Range(_num(match.group(1)), None)
    match = _MONEY_DOWN.search(text)
    if match:
        return Range(None, _num(match.group(1) or match.group(2)))
    return None


_YEARS_SPAN = re.compile(r"\b(20\d\d|19\d\d)\s*-\s*(\d{2}|\d{4})\b")
_YEAR_OLDER = re.compile(r"\b(20\d\d|19\d\d)\b[^|]*\(?(?:and older|or older)")


def _full_year(first: int, part: str) -> int:
    """Expand a two-digit year using the first year's century."""
    if len(part) == 4:
        return int(part)
    return first // 100 * 100 + int(part)


def parse_model_years(text: str) -> Optional[Range]:
    """Parse ranges like '2024-27 Model Year' and '2016 Model Year (and older)'."""
    text = normalize(text)
    if "model year" not in text and "year" not in text:
        return None
    match = _YEAR_OLDER.search(text)
    if match:
        return Range(None, int(match.group(1)))
    match = _YEARS_SPAN.search(text)
    if match:
        first = int(match.group(1))
        last = _full_year(first, match.group(2))
        return Range(min(first, last), max(first, last))
    return None


_TERM_MONTHS = re.compile(r"\b(\d{1,3})\s*-?\s*(?:months?|mos?\.?)(?![a-z])")
_TERM_YEARS = re.compile(r"\b(\d{1,2})\s*-?\s*years?(?![a-z])")


def parse_term_months(text: str) -> Optional[int]:
    """Parse month terms and convert year terms to months."""
    text = normalize(text)
    match = _TERM_MONTHS.search(text)
    if match:
        return int(match.group(1))
    match = _TERM_YEARS.search(text)
    if match and "model year" not in text:
        return int(match.group(1)) * 12
    return None


KINDS = ("rate", "apr", "apy", "dividend_rate", "intro_apr", "intro_dividend_rate", "points")


def kind_from_header(header: str) -> Optional[str]:
    """Map a table column heading to its rate kind."""
    header = normalize(re.sub(r"\d+$", "", (header or "").strip()))
    intro = any(word in header for word in ("intro", "1st", "first"))
    if "annual percentage yield" in header or re.search(r"\bapy\b", header):
        return "apy"
    if "dividend" in header:
        return "intro_dividend_rate" if intro else "dividend_rate"
    if "annual percentage rate" in header or re.search(r"\bapr\b", header):
        return "intro_apr" if intro else "apr"
    if header.startswith("points"):
        return "points"
    if header == "rate" or header.startswith("rate "):
        return "rate"
    return None


_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_BARE_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def clean_percent(cell: str) -> Optional[str]:
    """Normalize percent cells and remove attached footnote markers."""
    match = _PERCENT.search(cell or "")
    if match:
        return match.group(1) + "%"
    match = _BARE_NUMBER.match(cell or "")
    if match:
        return match.group(1) + "%"
    return None


def percent_value(text: str) -> Optional[float]:
    """Return a cleaned percentage's numeric value."""
    match = _PERCENT.search(text or "")
    return float(match.group(1)) if match else None


def segment_of(*texts: str) -> str:
    """Infer business vs personal from row/table text, not the page URL."""
    return "business" if any("business" in normalize(text) for text in texts if text) else "personal"


_PURPOSE = re.compile(r"^(purchase|refinance)\s*:")
_IRA = re.compile(r"\b(roth|traditional)\b")
_STRIP = [
    _MONEY_SPAN,
    _MONEY_UP,
    _MONEY_DOWN,
    re.compile(r"\bmodel years?\b"),
    _YEARS_SPAN,
    re.compile(r"\b(?:19|20)\d\d\b"),
    re.compile(r"\((?:and|or) older\)"),
    _TERM_MONTHS,
    _TERM_YEARS,
    re.compile(r"^(purchase|refinance)\s*:"),
    re.compile(r"\b(roth|traditional|jumbo|business)\b"),
    re.compile(r"\b(rates?|apr|apy)\s*$"),
]


@dataclass(frozen=True)
class LabelParts:
    product: str
    qualifiers: tuple


def parse_label(label: str) -> LabelParts:
    """Split a table label into canonical product words and qualifiers."""
    text = normalize(label)
    qualifiers: dict = {}
    if match := _PURPOSE.search(text):
        qualifiers["purpose"] = match.group(1)
    if match := _IRA.search(text):
        qualifiers["ira_type"] = match.group(1)
    if re.search(r"\bjumbo\b", text):
        qualifiers["size"] = "jumbo"
    years = parse_model_years(text)
    if years is not None:
        qualifiers["model_years"] = years
    elif (term := parse_term_months(text)) is not None:
        qualifiers["term_months"] = term
    if (amount := parse_amount(text)) is not None:
        qualifiers["amount"] = amount

    words = text
    for pattern in _STRIP:
        words = pattern.sub(" ", words)
    words = re.sub(r"['\u2019]", "", words)
    words = re.sub(r"[^a-z0-9&/ ]", " ", words)
    words = re.sub(r"\b\d+\b", " ", words)
    words = _SPACES.sub(" ", words).strip()
    return LabelParts(words, tuple(sorted(qualifiers.items())))


@dataclass(frozen=True)
class FactKey:
    product: str
    segment: str
    kind: str
    qualifiers: tuple = ()

    @staticmethod
    def make(product: str, segment: str, kind: str, **qualifiers) -> "FactKey":
        if kind not in KINDS:
            raise ValueError(f"unknown kind: {kind}")
        if segment not in ("personal", "business"):
            raise ValueError(f"unknown segment: {segment}")
        normalized_qualifiers = tuple(
            sorted((name, value) for name, value in qualifiers.items() if value is not None)
        )
        return FactKey(normalize(product), segment, kind, normalized_qualifiers)

    def get(self, name: str):
        return dict(self.qualifiers).get(name)

    def __str__(self) -> str:
        parts = [self.product, self.segment, self.kind]
        parts.extend(f"{name}={value}" for name, value in self.qualifiers)
        return " | ".join(parts)


@dataclass(frozen=True)
class EntityKey:
    """A prose fact key, such as a branch address."""

    entity: str
    attribute: str

    def __str__(self) -> str:
        return f"{self.entity} | {self.attribute}"


PRODUCT_SYNONYMS = {
    "cd": "certificate",
    "cds": "certificate",
    "car": "auto",
    "vehicle": "auto",
    "truck": "auto",
    "home loan": "mortgage",
    "boat": "recreational",
    "rv": "recreational",
    "camper": "recreational",
    "credit card": "visa",
}

KIND_WORDS = {
    "apy": "apy",
    "annual percentage yield": "apy",
    "yield": "apy",
    "apr": "apr",
    "annual percentage rate": "apr",
    "dividend": "dividend_rate",
    "points": "points",
}

_DEPOSIT_WORDS = ("savings", "certificate", "checking", "money market", "ira", "account", "club")
_MORTGAGE_WORDS = ("conventional", "mortgage")


def default_kinds(product: str) -> tuple:
    """Return the default rate kinds to answer for a product."""
    product = normalize(product)
    if any(word in product for word in _DEPOSIT_WORDS):
        return ("apy", "dividend_rate")
    if any(word in product for word in _MORTGAGE_WORDS):
        return ("rate", "apr")
    return ("apr",)
