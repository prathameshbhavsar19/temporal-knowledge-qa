"""
Page classifier: how much to trust a page, from its URL and its dates.

Trust tier (1 = most trusted). Page type only breaks ties: the resolver looks
at each table's own effective date first (see resolver.py).

    1  rate sheet       /rates, /business-rates
    2  product page     /personal/..., /business/...
    3  site page        everything else on the site (contact, about, landing pages)
    4  news             /newsroom/...
    5  blog             /learn/...
    6  calculator/PDF   /calculators/..., /hubfs/..., promo landing pages ending in -lp

A page is "frozen" when its own fetch date is older than the previous
snapshot: the crawler carried an old copy forward instead of re-reading it.

Only the standard library is used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

TIER_DESCRIPTIONS = {
    1: "the official rate sheet",
    2: "a product page",
    3: "a site page",
    4: "a news article",
    5: "a blog article",
    6: "a calculator, promo or PDF page",
}

_RULES = [   # (tier, page_type, path pattern); first match wins
    (1, "rate_sheet", re.compile(r"^/(business-)?rates$")),
    (6, "calculator", re.compile(r"^/calculators?(/|$)")),
    (6, "pdf", re.compile(r"^/hubfs(/|$)")),
    (6, "promo", re.compile(r"-lp$")),
    (4, "news", re.compile(r"^/newsroom(/|$)")),
    (5, "blog", re.compile(r"^/learn(/|$)")),
    (5, "blog", re.compile(r"^/business/resources/.")),          # articles, not product pages
    (5, "blog", re.compile(r"^/20\d\d/\d\d/")),                  # dated blog URLs
    (2, "product", re.compile(r"^/(personal|business)(/|$)")),
]

_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december")
_PUBLISHED = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}}),\s+(20\d\d)\b", re.I)


@dataclass(frozen=True)
class PageInfo:
    url: str
    path: str                     # canonical path: no host, query, fragment or trailing slash
    tier: int
    page_type: str
    frozen: bool
    publish_date: Optional[str]   # news and blog articles: ISO date the article was published

    @property
    def description(self) -> str:
        return TIER_DESCRIPTIONS[self.tier]


def canonical_path(url: str) -> str:
    """'https://www.dupaco.com/personal/loans/auto-loans/?utm=x#rates' -> '/personal/loans/auto-loans'."""
    path = re.sub(r"^https?://[^/]+", "", url or "")
    path = re.split(r"[?#]", path, maxsplit=1)[0]
    path = path.rstrip("/")
    return path or "/"


def is_variant(url: str) -> bool:
    """URLs with ?query or #fragment are copies of another page (pop-ups, tracking links)."""
    return "?" in (url or "") or "#" in (url or "")


def page_type(path: str):
    for tier, kind, pattern in _RULES:
        if pattern.search(path):
            return tier, kind
    return 3, "site"


def publish_date(markdown: str) -> Optional[str]:
    """The first 'Month D, YYYY' near the top of a news article: its publish date.
    Later dates in the text ('will close March 31, 2026') are not used."""
    top = "\n".join((markdown or "").splitlines()[:15])
    m = _PUBLISHED.search(top)
    if not m:
        return None
    month = _MONTHS.split("|").index(m.group(1).lower()) + 1
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"


def classify(url: str, markdown: str, fetched_at: Optional[str], previous_taken_on: Optional[str] = None) -> PageInfo:
    path = canonical_path(url)
    tier, kind = page_type(path)
    fetched = (fetched_at or "")[:10]
    frozen = bool(previous_taken_on and fetched and fetched <= previous_taken_on[:10])
    published = publish_date(markdown) if kind in ("news", "blog") else None
    return PageInfo(url, path, tier, kind, frozen, published)
