"""
Prose facts: the one site-wide number that articles keep restating, the branch count.

Every news release ends with an "ABOUT DUPACO" paragraph ("It has over 650 employees
and 22 branch office locations."). Older releases keep their old count, so the site
states several counts at once. Each statement is kept with its article's publish
date; the newest dated statement is the current count, and the older counts are a
contradiction the site never cleans up.

Member, asset and employee figures in the same paragraph are left alone: they are
growth figures that are expected to differ by date, not contradictions.

Only the standard library is used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from page_classifier import PageInfo

_COUNT = re.compile(r"(?<![\d,.])\b(\d{1,3})\s+(?:full-service\s+)?branch(?:es|\s+office\s+locations|\s+offices|\s+locations)\b",
                    re.I)


@dataclass(frozen=True)
class CountStatement:
    value: str          # '22'
    date: str           # the article's publish date, or '' when the page is undated
    url: str
    path: str
    tier: int
    quote: str          # the sentence that states it


def branch_counts(markdown: str, url: str, page: PageInfo) -> List[CountStatement]:
    """Branch-count statements on one page, one per distinct count."""
    out, seen = [], set()
    for line in (markdown or "").splitlines():
        for m in _COUNT.finditer(line):
            if m.group(1) in seen:
                continue
            seen.add(m.group(1))
            start = max(0, line.rfind(".", 0, m.start()) + 1)
            end = line.find(".", m.end())
            quote = line[start:end + 1 if end >= 0 else len(line)].strip()[:200]
            out.append(CountStatement(m.group(1), page.publish_date or "", url, page.path, page.tier, quote))
    return out


def current_count(statements: List[CountStatement]) -> Optional[CountStatement]:
    """The newest dated statement; undated pages only when nothing is dated."""
    dated = [s for s in statements if s.date]
    pool = dated or statements
    if not pool:
        return None
    return max(pool, key=lambda s: (s.date, -s.tier, s.path))
