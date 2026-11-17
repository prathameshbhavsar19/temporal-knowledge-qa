"""
Resolver: decides which value is current, per snapshot, and lists the contradictions.

How a winner is picked among pages that state the same fact (rules 2 and 4 in NOTES):
    1. a table with an "effective as of" date beats one without
    2. the newest effective date wins
    3. a page that was re-fetched beats a frozen copy
    4. the more trusted page type wins (page_classifier.py)
    5. the most recently fetched page wins
A majority vote is never used: copied pages are not independent votes.

"Same fact" means same product, segment, kind and qualifiers, except that
model-year ranges only need to mostly overlap: the calculator's "2023-26 Model
Year" and /rates' "2024-27 Model Year" describe the same tier of car.

Two kinds of contradiction are reported (conflicts()):
    within a snapshot  pages that state a different value for the same tier than the
                       winner; one record per product and set of stale values (pages
                       repeating the same stale table share one record), keeping the
                       latest snapshot's version
    across snapshots   the winning value of a tier changed from one crawl to the next;
                       one record per product, resolved to the newest value
Values are compared by their numbers, so '$25' and '$25.00' agree. Minimum-balance
columns are kept for answers but never reported as contradictions.

Dates before the first snapshot: the earliest snapshot is used, but only tables whose
"effective as of" date is on or before the asked date count (lookup(cutoff=...)).

Only the standard library is used.
"""
from __future__ import annotations

import bisect
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from fact_key import MONEY_KINDS, FactKey, Range, normalize
from page_classifier import PageInfo, classify, is_variant, canonical_path
from prose_facts import CountStatement, branch_counts, current_count
from table_extractor import TableFact, extract_rate_lines, extract_tables

FUZZY = "model_years"          # the one qualifier matched by overlap instead of equality
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def value_key(value: str) -> tuple:
    """The numbers in a value: '3.5%' == '3.50%', '$25' == '$25.00'."""
    return tuple(float(x) for x in _NUMBER.findall((value or "").replace(",", "")))


@dataclass(frozen=True)
class Candidate:
    fact: TableFact
    page: PageInfo

    @property
    def value(self) -> str:
        return self.fact.value

    @property
    def key(self) -> FactKey:
        return self.fact.key


def rank(c: Candidate) -> tuple:
    """Higher is better. Used with max()."""
    return (
        not c.fact.ended,                   # an ended offer is never the current value
        c.fact.effective is not None,
        c.fact.effective or "",
        not c.page.frozen,
        -c.page.tier,
        c.fact.fetched_at or "",
    )


@dataclass
class Tier:
    """One tier of one product (e.g. 60 months, 2024-27 model year) in one snapshot."""
    winner: Candidate
    candidates: List[Candidate]

    @property
    def key(self) -> FactKey:
        return self.winner.key

    @property
    def losers(self) -> List[Candidate]:
        win = value_key(self.winner.value)
        return [c for c in self.candidates if value_key(c.value) != win]

    def agreeing(self) -> List[Candidate]:
        win = value_key(self.winner.value)
        return [c for c in self.candidates if value_key(c.value) == win]


@dataclass
class ConflictRecord:
    topic: str
    values: List[str]
    sources: List[Tuple[str, str, str]]   # (url, snapshot_id, quote)
    resolution: str
    reason: str
    snapshot_id: str
    product: str = ""


# --------------------------------------------------------------------------
# Matching tiers
# --------------------------------------------------------------------------

def _years_match(a: Optional[Range], b: Optional[Range]) -> bool:
    """Same tier if the model-year ranges overlap by at least half of the shorter one."""
    if a is None or b is None:
        return a is b
    if a == b:
        return True
    if a.lo is None or b.lo is None:          # open-ended '2016 and older'
        return a.lo is None and b.lo is None and a.hi == b.hi
    lo, hi = max(a.lo, b.lo), min(a.hi if a.hi is not None else 9999, b.hi if b.hi is not None else 9999)
    if hi < lo:
        return False
    span = lambda r: (r.hi if r.hi is not None else r.lo) - r.lo + 1
    return (hi - lo + 1) >= 0.5 * min(span(a), span(b))


def bucket_of(key: FactKey) -> tuple:
    """Everything that must be exactly equal for two facts to be the same tier."""
    rest = tuple((k, v) for k, v in key.qualifiers if k != FUZZY)
    return (key.product, key.segment, key.kind, rest)


def cluster(cands: List[Candidate]) -> List[List[Candidate]]:
    """Group candidates of one bucket into tiers, joining overlapping model-year ranges."""
    groups: List[List[Candidate]] = []
    for c in sorted(cands, key=lambda x: str(x.key)):
        years = c.key.get(FUZZY)
        home = next((g for g in groups if _years_match(g[0].key.get(FUZZY), years)), None)
        if home is None:
            groups.append([c])
        else:
            home.append(c)
    return groups


def _query_matches(key: FactKey, query: dict) -> bool:
    """A query value is a number (inside a Range) or an exact value. A qualifier the
    query names must exist on the key; qualifiers the query leaves out match anything."""
    quals = dict(key.qualifiers)
    for name, want in query.items():
        if want is None:
            continue
        have = quals.get(name)
        if have is None:
            return False
        if isinstance(have, Range) and isinstance(want, (int, float)):
            if not have.contains(want):
                return False
        elif have != want:
            return False
    return True


# --------------------------------------------------------------------------
# The knowledge base
# --------------------------------------------------------------------------

class KnowledgeBase:
    def __init__(self) -> None:
        self.snapshot_ids: List[str] = []
        self.taken_on: List[str] = []
        self.pages: Dict[str, Dict[str, PageInfo]] = {}                      # snapshot -> path -> page
        self.buckets: Dict[str, Dict[tuple, List[Tier]]] = {}               # snapshot -> bucket -> tiers
        self.aliases: Dict[Tuple[str, str], set] = defaultdict(set)         # (product, segment) -> table titles
        self.product_info: Dict[str, Dict[Tuple[str, str], dict]] = {}     # snapshot -> product -> facts about it
        self.counts: Dict[str, List[CountStatement]] = {}                   # snapshot -> branch-count statements

    # ---- loading ---------------------------------------------------------

    def add_snapshot(self, snapshot_id: str, taken_on: str, pages: Iterable[Tuple[str, str, str]]) -> None:
        """pages: (url, markdown, fetched_at). Snapshots must arrive oldest first."""
        previous = self.taken_on[-1] if self.taken_on else None
        seen: Dict[str, PageInfo] = {}
        cands: List[Candidate] = []
        counts: List[CountStatement] = []
        page_list = sorted(pages, key=lambda p: is_variant(p[0]))     # plain URLs before ?/# variants
        for url, markdown, fetched_at in page_list:
            path = canonical_path(url)
            if path in seen:                                          # a variant of a page already read
                continue
            info = classify(url, markdown, fetched_at, previous)
            seen[path] = info
            counts += branch_counts(markdown, url, info)
            for fact in extract_tables(markdown, url, snapshot_id, fetched_at) + \
                    extract_rate_lines(markdown, url, snapshot_id, fetched_at):
                cands.append(Candidate(fact, info))
                title = fact.context.split(" > ")[-1]
                if info.tier <= 3 and title and "](" not in title:
                    # the site's own name for the table: 'ATV, Snowmobile & Jet Ski Loans'
                    self.aliases[(fact.key.product, fact.key.segment)].add(normalize(title))

        by_bucket: Dict[tuple, List[Candidate]] = defaultdict(list)
        for c in cands:
            by_bucket[bucket_of(c.key)].append(c)
        tiers = {b: [Tier(max(g, key=rank), g) for g in cluster(cs)] for b, cs in by_bucket.items()}
        tiers = {b: [t for t in ts if not t.winner.fact.ended] for b, ts in tiers.items()}   # only ended offers left
        tiers = {b: ts for b, ts in tiers.items() if ts}

        info_by_product: Dict[Tuple[str, str], dict] = {}
        for c in cands:
            pinfo = info_by_product.setdefault((c.key.product, c.key.segment),
                                               {"tier": 9, "qualifiers": set(), "kinds": set()})
            pinfo["tier"] = min(pinfo["tier"], c.page.tier)
            pinfo["qualifiers"] |= {name for name, _ in c.key.qualifiers}
            pinfo["kinds"].add(c.key.kind)
        self.product_info[snapshot_id] = info_by_product
        self.counts[snapshot_id] = counts

        self.snapshot_ids.append(snapshot_id)
        self.taken_on.append(taken_on[:10])
        self.pages[snapshot_id] = seen
        self.buckets[snapshot_id] = tiers

    # ---- lookup ----------------------------------------------------------

    def snapshot_for(self, as_of: Optional[str] = None) -> Optional[str]:
        """Newest snapshot taken on or before as_of; the latest when as_of is None."""
        if not self.snapshot_ids:
            return None
        if not as_of:
            return self.snapshot_ids[-1]
        i = bisect.bisect_right(self.taken_on, as_of[:10]) - 1
        return self.snapshot_ids[i] if i >= 0 else None

    def view(self, as_of: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """(snapshot, cutoff). A date before the first snapshot reads the first snapshot,
        limited to tables already effective on that date."""
        sid = self.snapshot_for(as_of)
        if sid is None and as_of and self.snapshot_ids:
            return self.snapshot_ids[0], as_of[:10]
        return sid, None

    def product_tier(self, product: str, segment: str, as_of: Optional[str] = None) -> int:
        """Most trusted page type that publishes the product (1 = rate sheet)."""
        sid, _ = self.view(as_of)
        return self.product_info.get(sid, {}).get((product, segment), {}).get("tier", 9)

    def qualifier_names(self, product: str, segment: str, as_of: Optional[str] = None) -> set:
        """What splits the product into tiers: {'term_months', 'size'} ..."""
        sid, _ = self.view(as_of)
        return self.product_info.get(sid, {}).get((product, segment), {}).get("qualifiers", set())

    def kinds_of(self, product: str, segment: str, as_of: Optional[str] = None) -> set:
        sid, _ = self.view(as_of)
        return self.product_info.get(sid, {}).get((product, segment), {}).get("kinds", set())

    def products(self, as_of: Optional[str] = None) -> List[Tuple[str, str]]:
        sid, _ = self.view(as_of)
        return sorted({(b[0], b[1]) for b in self.buckets.get(sid, {})})

    def lookup(self, product: str, segment: str = "personal", kind: Optional[str] = None,
               as_of: Optional[str] = None, **query) -> List[Tier]:
        """Current tiers of a product. Qualifiers in `query` filter the tiers:
        numbers fall inside ranges (model_years=2025, amount=15000), others match exactly."""
        sid, cutoff = self.view(as_of)
        found: List[Tier] = []
        for (prod, seg, knd, _), tiers in self.buckets.get(sid, {}).items():
            if prod != product or seg != segment or (kind and knd != kind):
                continue
            if cutoff:
                tiers = [t for t in (_effective_by(t, cutoff) for t in tiers) if t is not None]
            found += [t for t in tiers if _query_matches(t.key, query)]
        if FUZZY in query and query[FUZZY] is not None:
            found = _best_per_overlap(found)
        return sorted(found, key=lambda t: str(t.key))

    # ---- contradictions ---------------------------------------------------

    def conflicts(self) -> List[ConflictRecord]:
        """Within-snapshot contradictions, then changes across snapshots.

        Within a snapshot, every page whose value for a tier differs from the winner is
        stale for that product; pages with the same stale values (a calculator and a promo
        page repeating one old table) share a record. The same set of stale pages seen in
        several snapshots is one record, kept from the latest snapshot that shows it."""
        groups: Dict[tuple, dict] = {}
        for sid in self.snapshot_ids:                                 # oldest first; later ones replace
            found: Dict[tuple, List[Tier]] = defaultdict(list)
            for (prod, seg, kind, _), tiers in self.buckets[sid].items():
                if kind in MONEY_KINDS:
                    continue
                for t in tiers:
                    for path in {c.page.path for c in t.losers}:
                        found[(path, prod, seg)].append(t)
            same_values: Dict[tuple, List[str]] = defaultdict(list)
            for (path, prod, seg), tiers in found.items():
                stale_values = frozenset(c.value for t in tiers for c in t.losers if c.page.path == path)
                same_values[(prod, seg, stale_values)].append(path)
            for (prod, seg, _), paths in same_values.items():
                tiers = []
                for path in paths:
                    tiers += [t for t in found[(path, prod, seg)] if all(t is not x for x in tiers)]
                groups[(prod, seg, frozenset(paths))] = {"sid": sid, "tiers": tiers, "paths": sorted(paths)}
        records = [self._record(g["paths"], prod, seg, g["sid"], g["tiers"])
                   for (prod, seg, _), g in groups.items()]
        records = sorted(records, key=lambda r: (r.snapshot_id, r.topic), reverse=True)
        # A change whose old and new values are exactly a stale page's story is told once.
        told = {(r.product, frozenset(r.values)) for r in records}
        changes = [c for c in self.changes() if (c.product, frozenset(c.values)) not in told]
        return records + self.count_conflicts() + changes

    def count_conflicts(self) -> List[ConflictRecord]:
        """Articles in the newest snapshot that state different branch counts (prose_facts.py)."""
        if not self.snapshot_ids:
            return []
        sid = self.snapshot_ids[-1]
        statements = self.counts.get(sid, [])
        win = current_count(statements)
        if win is None:
            return []
        stale: Dict[str, CountStatement] = {}
        for st in sorted(statements, key=lambda x: (x.date, x.path), reverse=True):
            if st.value != win.value:
                stale.setdefault(st.value, st)                        # newest statement of each old count
        if not stale:
            return []
        topic = (f"branch count: older articles state {', '.join(v + ' branches' for v in stale)}; "
                 f"the newest article states {win.value}")
        reason = (f"{win.path} (published {win.date or 'undated'}) is the newest article stating the count; "
                  + "; ".join(f"{st.path} (published {st.date or 'undated'}) still says {st.value}"
                              for st in stale.values()) + ". Old news releases are never edited.")
        sources = [(win.url, sid, win.quote)] + [(st.url, sid, st.quote) for st in list(stale.values())[:3]]
        return [ConflictRecord(topic, [win.value] + list(stale), sources,
                               f"{win.value} branch office locations", reason, sid)]

    def changes(self) -> List[ConflictRecord]:
        """Tiers whose winning value changed from one snapshot to the next, one record per
        product: the older values, then the newest. Resolved to the newest snapshot."""
        moved: Dict[Tuple[str, str], List[Tuple[str, Tier, Tier]]] = defaultdict(list)
        ids = self.snapshot_ids
        for prev, nxt in zip(ids, ids[1:]):
            for bucket, tiers in self.buckets[nxt].items():
                if bucket[2] in MONEY_KINDS:
                    continue
                old_tiers = self.buckets[prev].get(bucket, [])
                for t in tiers:
                    old = next((o for o in old_tiers if _years_match(o.key.get(FUZZY), t.key.get(FUZZY))), None)
                    if old is not None and value_key(old.winner.value) != value_key(t.winner.value):
                        moved[(bucket[0], bucket[1])].append((nxt, old, t))
        records = []
        for (product, segment), steps in sorted(moved.items(), key=lambda kv: (kv[0][0], kv[0][1] != "personal")):
            last_sid = ids[-1]
            latest = [t for t in self.lookup(product, segment, as_of=self.taken_on[-1])
                      if any(_same_tier(t.key, s[2].key) for s in steps)]
            if not latest:
                continue
            # values in time order: each changed tier's old value, then the newest values
            values: List[str] = []
            for _, old, new in sorted(steps, key=lambda s: (s[0], _KIND_ORDER.index(s[2].key.kind))):
                for v in (old.winner.value, new.winner.value):
                    if v not in values:
                        values.append(v)
            labels = []
            for t in sorted(latest, key=lambda t: str(t.key)):
                lbl = tier_label(t.key)
                if lbl and lbl not in labels:
                    labels.append(lbl)
            kinds = sorted({t.key.kind for t in latest}, key=_KIND_ORDER.index)
            seg = "" if segment == "personal" else " (business)"
            first = min(s[0] for s in steps)
            topic = (f"{product}{seg} {'/'.join(_KIND_LABEL[k] for k in kinds)} changed between snapshots"
                     + (f" ({'; '.join(labels[:8])})" if labels else ""))
            resolution = "; ".join(
                f"{t.winner.value} {_KIND_LABEL[t.key.kind]}" + (f" ({tier_label(t.key)})" if tier_label(t.key) else "")
                for t in sorted(latest, key=lambda t: (str(t.key)))[:12])
            when = sorted({s[0] for s in steps})
            win = latest[0].winner
            reason = (f"The rate tables were updated between crawls ({', '.join(when)}); the newest crawl "
                      f"({last_sid}) is the current site" + (f", with rates effective {win.fact.effective}"
                                                              if win.fact.effective else "") + ".")
            sources: List[Tuple[str, str, str]] = []
            first_step = next(s for s in steps if s[0] == first)
            prev_sid = ids[ids.index(first) - 1]
            sources.append((first_step[1].winner.fact.url, prev_sid, first_step[1].winner.fact.quote))
            for t in latest[:2]:
                sources.append((t.winner.fact.url, last_sid, t.winner.fact.quote))
            records.append(ConflictRecord(topic, values[:24], sources, resolution, reason, last_sid, product))
        # The business rate sheet repeats the personal specials: the same change is one record.
        merged: Dict[frozenset, ConflictRecord] = {}
        for r in records:
            key = frozenset(r.values)
            if key in merged:
                first = merged[key]
                other = r.topic.split(" changed between snapshots")[0]
                first.topic = first.topic.replace(" changed between snapshots", f" (and {other}) changed between snapshots", 1)
                first.sources = first.sources + r.sources[-1:]
            else:
                merged[key] = r
        return list(merged.values())

    def _record(self, stale_paths, product, segment, sid, tiers) -> ConflictRecord:
        tiers = sorted(tiers, key=lambda t: str(t.key))
        stale = [c for t in tiers for c in t.losers if c.page.path in stale_paths]
        winners = [t.winner for t in tiers]
        values: List[str] = []
        for v in [c.value for c in stale] + [w.value for w in winners]:
            if v not in values:
                values.append(v)

        win_path = Counter(w.page.path for w in winners).most_common(1)[0][0]
        win_page = next(w for w in winners if w.page.path == win_path)
        kinds = sorted({t.key.kind for t in tiers}, key=_KIND_ORDER.index)
        labels = []
        for t in tiers:
            lbl = tier_label(t.key)
            if lbl and lbl not in labels:
                labels.append(lbl)
        seg = "" if segment == "personal" else " (business)"
        topic = f"{product}{seg} {'/'.join(_KIND_LABEL[k] for k in kinds)}"
        if labels:
            topic += f" ({'; '.join(labels[:10])})"
        verb = "disagrees" if len(stale_paths) == 1 else "disagree"
        if stale_paths == [win_path]:
            topic += f": an older table on {win_path} disagrees with the current one"
        else:
            topic += f": {', '.join(stale_paths)} {verb} with {win_path}"

        resolution = "; ".join(
            f"{w.value} {_KIND_LABEL[w.key.kind]}" + (f" ({tier_label(w.key)})" if tier_label(w.key) else "")
            for w in _dedupe(winners))
        stale_parts = []
        for path in stale_paths:
            c = next(x for x in stale if x.page.path == path)
            what = f"the older table on {path}" if path == win_path else f"{path} is {c.page.description}"
            stale_parts.append(f"{what}{_dated(c)}")
        reason = (f"{win_path} is {win_page.page.description}{_dated(win_page)}; "
                  f"{'; '.join(stale_parts)}, so the older values are out of date.")

        sources: List[Tuple[str, str, str]] = []
        for c in _dedupe(stale)[:3] + _dedupe(winners)[:3]:
            sources.append((c.fact.url, sid, c.fact.quote))
        return ConflictRecord(topic, values, sources, resolution, reason, sid, product)


# --------------------------------------------------------------------------
# Labels for topics and reasons
# --------------------------------------------------------------------------

_KIND_ORDER = ["rate", "apr", "intro_apr", "dividend_rate", "intro_dividend_rate", "apy", "intro_apy", "points",
               "min_open", "min_earn"]
_KIND_LABEL = {"rate": "rate", "apr": "APR", "intro_apr": "intro APR", "dividend_rate": "dividend rate",
               "intro_dividend_rate": "intro dividend rate", "apy": "APY", "intro_apy": "intro APY", "points": "points",
               "min_open": "minimum to open", "min_earn": "minimum to earn"}


def _same_tier(a: FactKey, b: FactKey) -> bool:
    return bucket_of(a) == bucket_of(b) and _years_match(a.get(FUZZY), b.get(FUZZY))


def _effective_by(t: Tier, cutoff: str) -> Optional[Tier]:
    """The tier as it stood on `cutoff`: only tables already effective by then count."""
    kept = [c for c in t.candidates if c.fact.effective and c.fact.effective <= cutoff]
    return Tier(max(kept, key=rank), kept) if kept else None


def tier_label(key: FactKey) -> str:
    """'60-month, 2024-2027 model years' / '30-year, purchase, up to $100,000'."""
    q = dict(key.qualifiers)
    parts = []
    term = q.get("term_months")
    if term:
        parts.append(f"{term // 12}-year" if term >= 120 and term % 12 == 0 else f"{term}-month")
    if q.get("purpose"):
        parts.append(q["purpose"])
    if q.get("size"):
        parts.append(q["size"])
    if q.get("ira_type"):
        parts.append(q["ira_type"])
    years = q.get("model_years")
    if years is not None:
        parts.append(f"{int(years.hi)} and older model years" if years.lo is None
                     else f"{int(years.lo)}-{int(years.hi)} model years")
    amount = q.get("amount")
    if amount is not None:
        parts.append(_money(amount))
    return ", ".join(parts)


def _money(r: Range) -> str:
    fmt = lambda v: f"${v:,.2f}".replace(".00", "")
    if r.lo is None:
        return f"up to {fmt(r.hi)}"
    if r.hi is None:
        return f"{fmt(r.lo)} and up"
    return f"{fmt(r.lo)}-{fmt(r.hi)}"


def _dated(c: Candidate) -> str:
    if c.fact.effective:
        return f" with rates effective {c.fact.effective}"
    if c.page.frozen:
        return f" last fetched {c.fact.fetched_at[:10]}"
    return ""


def _dedupe(cands: List[Candidate]) -> List[Candidate]:
    out, seen = [], set()
    for c in cands:
        k = (c.fact.url, c.value, str(c.key))
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _best_per_overlap(tiers: List[Tier]) -> List[Tier]:
    """When a model year falls in two tiers (a stale page's '2023-26' and /rates' '2024-27'),
    keep only the better-ranked one per bucket."""
    best: Dict[tuple, Tier] = {}
    for t in tiers:
        b = bucket_of(t.key)
        if b not in best or rank(t.winner) > rank(best[b].winner):
            best[b] = t
    return list(best.values())
