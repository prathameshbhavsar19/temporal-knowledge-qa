"""
Table extractor: turns every rate table on a page into fact rows.

For each markdown table that has at least one rate column (APR, APY, dividend
rate, rate), every row becomes one TableFact per rate column:

    key        FactKey: product + segment + kind + qualifiers (see fact_key.py)
    value      the number as written on the page, footnote digits removed
    effective  the date in the "Rates are effective as of ..." note under the table
    quote      the raw row, used later as the cited source quote

Where the product comes from, in order:
    1. the row label, when it names a product ("Share Savings")
    2. the table's family, found from (in order) the short title lines just above
       the table, its first header cell, the nearest heading, and the page URL
       ("Auto & Motorcycle Loans" -> "auto loan")
The family is put in front of the label words unless the label already contains
it: "Roth: 17 Month Fixed Jumbo Special" in an IRA table -> "ira fixed special".

Only the standard library is used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from fact_key import MONEY_KINDS, FactKey, clean_money, clean_percent, kind_from_header, normalize, parse_label, segment_of


@dataclass(frozen=True)
class TableFact:
    key: FactKey
    value: str                 # '5.39%', or a range '12.50% - 19.50%'
    url: str
    snapshot_id: str
    fetched_at: str
    effective: Optional[str]   # ISO date 'YYYY-MM-DD' from the note under the table, or None
    context: str               # the table's title lines, e.g. 'Home Loans > Home Loans - Purchase'
    quote: str                 # the raw table row
    ended: bool = False        # stated as an ended / expired offer: kept as evidence, never current


# --------------------------------------------------------------------------
# Product families: a small, generic list of credit-union product families.
# Most specific first; the first family whose word appears wins.
# --------------------------------------------------------------------------

FAMILIES = [
    ("powersports loan", (r"\batv\b", r"\bsnowmobile", r"\bjet ski")),
    ("boat & rv loan", (r"\bboat", r"\brv\b", r"\brecreational\b")),
    ("auto loan", (r"\bauto\b", r"\bmotorcycle")),
    ("heloc", (r"\bheloc\b", r"\bhome equity line")),
    ("home equity loan", (r"\bhome equity\b",)),
    ("home improvement loan", (r"\bhome improvement\b",)),
    ("mortgage", (r"\bhome loans?\b", r"\bmortgages?\b")),
    ("personal loan", (r"\bpersonal loans?\b",)),
    ("ira", (r"\biras?\b",)),
    ("certificate", (r"\bcertificates?\b",)),
]

_CARD = re.compile(r"\bvisa\b")
_DROP_WORDS = {"standard", "introductory", "intro"}       # describe the row, not the product


def detect_family(texts: List[str]) -> Optional[str]:
    """First family found in the texts, which are given in priority order."""
    for text in texts:
        t = normalize(text)
        if not t:
            continue
        if _CARD.search(t):                                 # card names are the family: 'platinum visa'
            return parse_label(t).product or None
        for family, patterns in FAMILIES:
            if any(re.search(p, t) for p in patterns):
                return family
    return None


def compose_product(family: Optional[str], label_words: str) -> str:
    """Family + label words, without repeating the family. Both already normalised."""
    words = [w for w in label_words.split() if w not in _DROP_WORDS]
    if not family:
        return " ".join(words)
    fam = family.split()
    if set(fam) <= set(words):
        return " ".join(words)
    return " ".join(fam + [w for w in words if w not in fam])


# --------------------------------------------------------------------------
# Small parsers
# --------------------------------------------------------------------------

_SEPARATOR = re.compile(r"^\|\s*:?-{2,}")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
_EFFECTIVE = re.compile(r"effective\s+(?:as\s+of\s+)?(\d{1,2})/(\d{1,2})/(\d{4})", re.I)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*%\d*\s*-\s*(\d+(?:\.\d+)?)\s*%")
_SKIP_COLUMN = re.compile(r"\bmin\b|\bmin\.|payment|example", re.I)
_INTRO = re.compile(r"\bintro(?:ductory)?\b")
_CELL_KIND = (("apr", re.compile(r"\bapr\b")), ("apy", re.compile(r"\bapy\b")))


def kind_from_cell(cell: str, header_kind: str) -> str:
    """A unit written in the cell beats the column header: the vehicle-affordability
    calculator puts '5.34% APR' under an 'Annual Percentage Yield' header."""
    t = normalize(cell)
    for kind, pattern in _CELL_KIND:
        if pattern.search(t):
            return kind
    return header_kind


def split_row(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def clean_rate_value(cell: str) -> Optional[str]:
    """'5.39% APR1' -> '5.39%'; '15.50% \u2013 21.00%1' -> '15.50% - 21.00%'; '\u2014' -> None."""
    t = normalize(cell)
    first = _PERCENT.search(t)
    m = _RANGE.search(t)
    if m and first and m.start() == first.start():   # '2.90% APR for 6 mos. (then 12.50% - 19.50%)' -> 2.90%
        return f"{m.group(1)}% - {m.group(2)}%"
    return clean_percent(t)


def parse_effective(line: str) -> Optional[str]:
    """'Rates are effective as of 08/31/2026.' -> '2026-08-31'."""
    m = _EFFECTIVE.search(line or "")
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{year:04d}-{month:02d}-{day:02d}"


def _is_title_line(line: str) -> bool:
    """A short label line just above a table: 'Auto & Motorcycle Loans', 'Platinum Visa'."""
    s = line.strip()
    if not s or len(s) > 70 or s.startswith(("[", "!", "|", "<")):
        return False
    if s.endswith((".", ":", "?", "!")):
        return False
    return True


def _url_words(url: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", url or "")
    return re.sub(r"[/_\-]+", " ", path)


# --------------------------------------------------------------------------
# The extractor
# --------------------------------------------------------------------------

def extract_tables(markdown: str, url: str, snapshot_id: str, fetched_at: str) -> List[TableFact]:
    lines = (markdown or "").splitlines()
    facts: List[TableFact] = []
    heading = ""                 # nearest markdown heading above the current line
    titles: List[str] = []       # title-like lines since the previous table
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        is_table = line.startswith("|") and i + 1 < len(lines) and _SEPARATOR.match(lines[i + 1].strip())
        if not is_table:
            m = _HEADING.match(line)
            if m:
                heading = m.group(1).strip()
                titles.append(heading)
            elif _is_title_line(line):
                titles.append(line)
            i += 1
            continue

        header = split_row(line)
        j = i + 2
        rows = []
        while j < len(lines) and lines[j].strip().startswith("|"):
            rows.append(lines[j].strip())
            j += 1

        # The note under the table: first non-empty lines after it, before anything else.
        effective = None
        k, seen = j, 0
        while k < len(lines) and seen < 3 and not lines[k].strip().startswith("|"):
            if lines[k].strip():
                seen += 1
                effective = parse_effective(lines[k])
                if effective:
                    break
            k += 1

        facts.extend(_table_facts(header, rows, titles[-2:], heading, url, snapshot_id, fetched_at, effective))
        titles = []
        i = j
    return facts


# A rate written as a labelled line instead of a table row:
#   '6-month certificate APY: 3.50%'
#   'Summer special: 6-month certificate APY 2.95% (offer ended 2025-09-30)'
_RATE_LINE = re.compile(r"^(?:[-*]\s*)?(?:(?P<prefix>[^:|]{1,40}):\s*)?(?P<label>[a-z0-9][a-z0-9 '&/-]{2,60}?)\s+"
                        r"(?P<kind>apy|apr|dividend rate|rate)\s*:?\s*(?P<value>\d+(?:\.\d+)?)\s?%(?P<rest>.*)$")
_ENDED = re.compile(r"\b(ended|expired|no longer|discontinued)\b")
_LINE_KIND = {"apy": "apy", "apr": "apr", "dividend rate": "dividend_rate", "rate": "rate"}


def extract_rate_lines(markdown: str, url: str, snapshot_id: str, fetched_at: str) -> List[TableFact]:
    """Rates stated one per line outside tables. A line describing an ended or expired offer
    is kept (it is evidence of a stale page) but marked so it can never be the current value."""
    facts: List[TableFact] = []
    for line in (markdown or "").splitlines():
        text = normalize(line)
        if not text or text.startswith("|") or len(text) > 140:
            continue
        m = _RATE_LINE.match(text)
        if not m:
            continue
        parts = parse_label(m.group("label"))
        family = detect_family([m.group("label")])
        product = compose_product(family, parts.product) if family else parts.product
        if not product:
            continue
        key = FactKey.make(product, segment_of(m.group("label"), m.group("prefix") or ""),
                           _LINE_KIND[m.group("kind")], **dict(parts.qualifiers))
        ended = bool(_ENDED.search(m.group("rest") + " " + (m.group("prefix") or "")))
        facts.append(TableFact(key, m.group("value") + "%", url, snapshot_id, fetched_at, None,
                               m.group("label"), line.strip(), ended))
    return facts


def _table_facts(header, rows, titles, heading, url, snapshot_id, fetched_at, effective) -> List[TableFact]:
    kinds = {c: kind_from_header(h) for c, h in enumerate(header)}
    rate_cols = [c for c, k in kinds.items() if k and k != "points"]
    if not any(kinds[c] not in MONEY_KINDS for c in rate_cols):    # needs a real rate column
        return []
    label_col = next((c for c in range(len(header)) if not kinds.get(c)), None)
    if label_col is None:
        return []
    qual_cols = [c for c in range(len(header))
                 if c != label_col and not kinds.get(c) and not _SKIP_COLUMN.search(header[c])]

    first_header = header[label_col]
    # Business rate sheets do not always say "business" above each table.
    path = re.sub(r"^https?://[^/]+", "", url or "").lower()
    business_page = "business" if path.startswith(("/business-rates", "/business/")) else ""
    family = detect_family(list(reversed(titles)) + [first_header, heading, _url_words(url)])
    context = " > ".join(titles)

    out: List[TableFact] = []
    last_label = ""
    for raw in rows:
        cells = split_row(raw)
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        label = cells[label_col] or last_label            # blank label = same product as the row above
        last_label = label
        parts = parse_label(label)
        quals = dict(parts.qualifiers)
        for c in qual_cols:                               # 'Term', 'Balance Tier', 'Loan Amount' columns
            for name, value in parse_label(cells[c]).qualifiers:
                quals.setdefault(name, value)
        # 'Purchase:' / 'Refinance:' rows are mortgage rows wherever they appear
        # ('Cash-Out Refinance' on the home-equity page repeats the mortgage table).
        product = compose_product("mortgage" if "purpose" in quals else family, parts.product)
        if not product:
            continue
        segment = segment_of(label, *titles, first_header, business_page)
        intro = bool(_INTRO.search(normalize(label)))
        for c in rate_cols:
            if kinds[c] in MONEY_KINDS:                    # 'Min. to Open' / 'Min. to Earn' columns
                value = clean_money(cells[c])
                if value is not None:
                    key = FactKey.make(product, segment, kinds[c], **quals)
                    out.append(TableFact(key, value, url, snapshot_id, fetched_at, effective, context, raw))
                continue
            value = clean_rate_value(cells[c])
            if value is None:
                continue
            kind = kind_from_cell(cells[c], kinds[c])
            if intro and kind in ("apr", "dividend_rate"):
                kind = "intro_" + kind
            key = FactKey.make(product, segment, kind, **quals)
            out.append(TableFact(key, value, url, snapshot_id, fetched_at, effective, context, raw))
    return out
