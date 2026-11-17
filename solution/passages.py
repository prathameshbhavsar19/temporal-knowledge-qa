"""
Passage index: keyword search over page text, ranked by relevance, trust and date.

Used for questions the rate tables cannot answer: hours, addresses, branch
counts, closures, awards, programs, links.

    passage    up to three consecutive text blocks of one page (a heading, a
               paragraph, a list), with markdown links and images reduced to text
    relevance  BM25 over word roots ('recognized' and 'recognition' share 'recogn'),
               boosted when the page title matches the question
    trust      page tier weight (rate sheet and product pages 1.0 ... calculators 0.6)
    date       a news or blog article's publish date; other pages count as current

Among passages close to the best score, the newest one wins: the newest news
article's "22 branch office locations" beats older articles' "23".

Only the standard library is used.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

from page_classifier import PageInfo
from question_parser import STOPWORDS, stem, tokenize

TRUST = {1: 1.0, 2: 1.0, 3: 0.95, 4: 0.85, 5: 0.7, 6: 0.6}

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
_BARE_URL = re.compile(r"https?://\S+")
_MARKUP = re.compile(r"[*_`#>|]+")
_SENTENCE = re.compile(r"(?<=[.!?\u201d])\s+(?=[A-Z0-9\u201c\"])")
_SUFFIXES = ("izations", "ization", "ations", "ation", "itions", "ition", "izing", "ized", "izes", "ize",
             "ings", "ing", "ions", "ion", "ments", "ment", "ibility", "ability", "ities", "ity", "ible", "able", "ed")
# False friends that suffix folding would merge: 'founded' (started) is not 'found' (past of
# find), and the Dupaco Foundation is a name.
_NO_FOLD = {"foundation", "foundations", "founded"}

LINK_RE = _LINK
_SKIP_LINE = re.compile(r"^\s*(?:\d+\s+min(?:ute)?s?\s+read\s*|handpicked for you\s*)$", re.I)


def _without_rate_tables(lines: List[str]) -> List[str]:
    """Drop page furniture lines and every markdown table that holds a percentage (a rate
    table, header included). Tables without rates (fee schedules) stay searchable."""
    out: List[str] = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            if not any("%" in l for l in lines[i:j]):
                out += lines[i:j]
            i = j
            continue
        if not _SKIP_LINE.match(lines[i]):
            out.append(lines[i])
        i += 1
    return out


def root(word: str) -> str:
    """Crude suffix folding so related forms meet: 'recognized'/'recognition' -> 'recogn',
    'flexible'/'flexibility' -> 'flex'."""
    if word in _NO_FOLD:
        return word
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[: -len(suf)]
    return word


def clean(text: str) -> str:
    """Markdown block -> plain text. Links keep their words, lose their URLs."""
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(lambda m: m.group(1), text)
    text = _BARE_URL.sub(" ", text)
    text = _MARKUP.sub(" ", text)
    text = text.replace("\\", "")
    return re.sub(r"[ \t]+", " ", text).strip()


def index_words(text: str) -> List[str]:
    return [root(stem(w)) for w in tokenize(text) if w not in STOPWORDS]


@dataclass
class Passage:
    text: str               # cleaned text of this window, lines kept
    page: PageInfo
    url: str
    date: str               # publish date; snapshot date for site pages; '' for undated articles
    words: Counter
    blocks: List[str]       # every cleaned block of the page
    start: int              # index of this window's first block


class PassageIndex:
    """One index per snapshot."""

    K1, B = 1.2, 0.75

    def __init__(self, snapshot_id: str, taken_on: str) -> None:
        self.snapshot_id = snapshot_id
        self.taken_on = taken_on[:10]
        self.passages: List[Passage] = []
        self.postings: Dict[str, List[int]] = defaultdict(list)
        self.links: List[Tuple[str, str, PageInfo]] = []      # (anchor text, url, page it appears on)
        self.pages: Dict[str, PageInfo] = {}                  # canonical path -> page
        self.titles: Dict[str, set] = {}                      # canonical path -> title words
        self.headings: Dict[str, str] = {}                    # canonical path -> the page's H1
        self._idf: Dict[str, float] = {}
        self._avg = 1.0

    def add_page(self, url: str, title: str, markdown: str, page: PageInfo) -> None:
        for m in _LINK.finditer(markdown or ""):
            anchor, target = clean(m.group(1)), m.group(2)
            if anchor and target.startswith("http"):
                self.links.append((anchor, target, page))
        heading = next((l.lstrip("# ").strip() for l in (markdown or "").splitlines() if l.startswith("# ")), "")
        self.pages[page.path] = page
        self.titles[page.path] = set(index_words(f"{title} {heading}"))
        self.headings[page.path] = clean(heading)

        # Rate-table rows are answered from the tables (resolver.py), never as raw text;
        # reading-time and "Handpicked for You" lines are page furniture.
        text = "\n".join(_without_rate_tables((markdown or "").splitlines()))
        blocks = [clean(b) for b in re.split(r"\n\s*\n", text)]
        blocks = [b for b in blocks if len(b) > 2]
        # Undated articles sort as oldest; undated site and product pages count as current.
        date = page.publish_date or ("" if page.page_type in ("news", "blog") else self.taken_on)
        for i in range(len(blocks)):
            text = "\n".join(blocks[i:i + 3])
            if len(text) < 25:
                continue
            words = Counter(index_words(text))
            if words:
                self.passages.append(Passage(text, page, url, date, words, blocks, i))

    def finish(self) -> None:
        n = len(self.passages)
        self._avg = sum(sum(p.words.values()) for p in self.passages) / max(n, 1)
        for i, p in enumerate(self.passages):
            for w in p.words:
                self.postings[w].append(i)
        self._idf = {w: math.log(1 + (n - len(ids) + 0.5) / (len(ids) + 0.5)) for w, ids in self.postings.items()}

    def idf(self, word: str) -> float:
        return self._idf.get(word, 0.0)

    def search(self, words: List[str], limit: int = 60) -> List[Tuple[float, Passage]]:
        """BM25 x trust x title match, best first. `words` are index words (index_words)."""
        scores: Dict[int, float] = defaultdict(float)
        for w in set(words):
            idf = self.idf(w)
            for i in self.postings.get(w, ()):
                p = self.passages[i]
                tf = p.words[w]
                length = sum(p.words.values())
                scores[i] += idf * tf * (self.K1 + 1) / (tf + self.K1 * (1 - self.B + self.B * length / self._avg))
        total = sum(self.idf(w) for w in set(words)) or 1.0
        ranked = []
        for i, s in scores.items():
            p = self.passages[i]
            title = self.titles.get(p.page.path, set())
            in_title = sum(self.idf(w) for w in set(words) if w in title) / total
            ranked.append((s * TRUST[p.page.tier] * (1 + 0.5 * in_title), i))
        ranked.sort(reverse=True)
        return [(s, self.passages[i]) for s, i in ranked[:limit]]


def coverage(words: List[str], passage: Passage, index: PassageIndex) -> float:
    """Share of the question's word weight (IDF) found in the passage."""
    total = sum(index.idf(w) for w in words) or 1.0
    return sum(index.idf(w) for w in words if w in passage.words) / total


def newest_close_to_best(ranked: List[Tuple[float, Passage]], ratio: float = 0.8) -> List[Tuple[float, Passage]]:
    """Passages scoring within `ratio` of the best, newest date first, then score."""
    if not ranked:
        return []
    best = ranked[0][0]
    close = [(s, p) for s, p in ranked if s >= ratio * best]
    rest = [(s, p) for s, p in ranked if s < ratio * best]
    return sorted(close, key=lambda sp: (sp[1].date, sp[0]), reverse=True) + rest


def sentences(text: str) -> List[str]:
    out = []
    for line in text.split("\n"):
        out += [s.strip(" -\u2022") for s in _SENTENCE.split(line) if s.strip(" -\u2022")]
    return out
