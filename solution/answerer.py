"""
Answerer: question in, one short answer out.

Routes, in order:
    1. link questions        -> the best-matching link on the site, weighted by how
                                trusted the destination page is and what it contains
    2. rate questions        -> fact lookup in the resolved rate tables
                                (a rate question with no matching product is refused:
                                rates are only ever quoted from rate tables)
       A question with no rate word but a tier ("the 17-month special", "a 2025 car for
       60 months", "$15k") is tried against the rate tables too.
    3. everything else       -> passage search: the newest trusted passage that actually
                                contains what was asked (hours, an address, a date, a fee...)
    4. nothing grounded      -> a refusal with no numbers in it

Which date: the as_of argument, else a date written in the question ("in May 2026"),
else the newest snapshot. A date before the first snapshot reads the first snapshot,
but only rate tables already effective on that date and articles published by then.

Every number and link in an answer is copied from a snapshot page.

Only the standard library is used.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from fact_key import MONEY_KINDS, default_kinds, normalize, parse_term_months
from page_classifier import canonical_path
from passages import TRUST, Passage, PassageIndex, coverage, index_words, newest_close_to_best, root, sentences
from question_parser import STOPWORDS, Query, content_words, normalize_date, parse, stem, tokenize
from resolver import KnowledgeBase, Tier, tier_label

REFUSAL = "I'm sorry, that isn't listed on Dupaco's website. Please contact Dupaco directly for that detail."

KIND_LABEL = {"rate": "rate", "apr": "APR", "intro_apr": "intro APR", "dividend_rate": "dividend rate",
              "intro_dividend_rate": "intro dividend rate", "apy": "APY", "intro_apy": "intro APY",
              "points": "points", "min_open": "minimum to open", "min_earn": "minimum balance to earn the rate"}
INSISTED = ("size", "ira_type")          # a tier the product must actually have ("jumbo", "Roth")
TIER_QUALIFIERS = ("term_months", "model_years", "amount", "size", "ira_type")

# Words that name a product type. If a rate question names one that no product on the
# site carries ("7/1 adjustable-rate", "construction"), that rate is not published.
TYPE_WORDS = {"adjustable", "arm", "7/1", "5/1", "10/1", "construction", "land", "lot", "commercial",
              "fha", "usda", "balloon", "bridge", "reverse", "student", "variable"}
# Words too common in product names to tell products apart.
GENERIC = {"loan", "rate", "account", "fixed", "standard", "program", "for", "qualified", "borrower",
           "the", "of", "&"}
# Account wrappers: "the 17-month special" means the certificate special, not its IRA twin,
# unless the question says IRA.
WRAPPERS = {"ira", "hsa"}
# A bare family word means the family's basic product: "savings tiers" -> Share Savings.
FAMILY_DEFAULT = {"savings": "share savings"}
ACRONYMS = {"ira": "IRA", "heloc": "HELOC", "rv": "RV", "atv": "ATV", "1st": "1st", "&": "&"}
FAMILY_FIRST = {"mortgage", "ira"}       # 'mortgage fixed conventional' reads 'Fixed Conventional Mortgage'

LINK_WORDS = {"link", "url", "website", "web", "page", "use", "see", "address", "online", "find"}

_TIME = re.compile(r"\b\d{1,2}(?::\d\d)?\s*(?:a\.?m\.?|p\.?m\.?)|\bnoon\b", re.I)
_ADDRESS = re.compile(r"\b\d{2,6}\s+[A-Z][a-z]+.*\b(?:street|st|avenue|ave|lane|ln|road|rd|drive|dr|boulevard|blvd|"
                      r"court|ct|way|parkway|pkwy|highway|hwy)\b|\b[A-Z]{2}\s+\d{5}\b", re.I)
_PHONE = re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")
_DATE = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)"
                   r"\s+\d{1,2},?\s+\d{4}\b", re.I)
# Member words -> words the site uses for the same idea. Added to a passage search at half
# weight; they never count toward how much of the question a passage covers.
THESAURUS = {
    "update": ("change",), "change": ("update",), "eligible": ("join",), "eligibility": ("join",),
    "university": ("college", "school"), "college": ("university", "school"), "student": ("college",),
    "fee": ("charge", "cost"), "cost": ("fee", "price"), "charge": ("fee",), "price": ("cost", "fee"),
    "location": ("branch",), "office": ("branch",), "flexibility": ("flexible",),
    "established": ("founded", "chartered"), "founded": ("chartered",), "started": ("founded",),
}

ATTRIBUTE_WORDS = {"year": ("year",), "phone": ("phone", "number"), "hours": ("hour",)}
_HOW_TO = re.compile(r"^\s*how (?:can|do|should|would) (?:i|we)\b", re.I)
_ACTION = re.compile(r"\b(you can|you may|call|visit|log in|stop by|contact us|email us|go to)\b", re.I)

_FEE = re.compile(r"\$\s?\d")
_YEAR = re.compile(r"\b(?:18|19|20)\d\d\b")
ATTRIBUTE_PATTERN = {"hours": _TIME, "address": _ADDRESS, "phone": _PHONE, "date": _DATE, "fee": _FEE,
                     "year": _YEAR}
_DEFINITION = re.compile(r"^\s*(what is|what's|what are|tell me about|explain|how does|how do)\b", re.I)
_TIER_WORDS = re.compile(r"\b(special|specials|pay|pays|paying|earn|earns|earning)\b")
# Page furniture after an article's last sentence: never part of an answer.
_FURNITURE = re.compile(r"^(?:learn more at\b|handpicked for you|read more\b|related\b|share this\b|\d+ min read|"
                        r"(?:january|february|march|april|may|june|july|august|september|october|november|"
                        r"december) \d{1,2}, \d{4}$)", re.I)
_DATELINE = re.compile(r"^[A-Z][A-Z. ]+,\s+[A-Z][a-z]+\.?\s*[\u2014\u2013-]+\s*")


@dataclass
class Draft:
    text: str
    sources: List[Tuple[str, str, str]] = field(default_factory=list)   # (url, snapshot_id, quote)
    confident: bool = True
    conflict_note: Optional[str] = None


def refuse() -> Draft:
    return Draft(REFUSAL, [], False, None)


def display(product: str, segment: str) -> str:
    words = product.split()
    if len(words) > 1 and words[0] in FAMILY_FIRST:
        words = words[1:] + words[:1]
    name = " ".join(ACRONYMS.get(w, w.capitalize()) for w in words)
    return f"Business {name}" if segment == "business" and "business" not in product else name


class Answerer:
    def __init__(self, kb: KnowledgeBase, indexes: Dict[str, PassageIndex]) -> None:
        self.kb = kb
        self.indexes = indexes
        self._page_products: Dict[str, Dict[str, set]] = {}
        self._aliases: Dict[Tuple[str, str], set] = {}
        self.trace: dict = {}

    # ------------------------------------------------------------------
    def answer(self, question: str, as_of: Optional[str] = None) -> Draft:
        q = parse(question)
        as_of = normalize_date(as_of) or q.as_of or self._month_date(q.as_of_month)
        sid, cutoff = self.kb.view(as_of)
        self.trace = {"question": question, "as_of": as_of, "snapshot": sid, "cutoff": cutoff,
                      "parsed": {k: v for k, v in vars(q).items() if v not in (None, [], {}, False)}}
        if sid is None:
            return self._done("no snapshot", refuse())
        if q.url and not cutoff:
            draft = self._link(q, sid)
            if draft:
                return self._done("link", draft)
        if q.rate:
            draft = self._rates(q, as_of) or (None if cutoff else self._rate_sentence(q, sid))
            if draft:
                return self._done("rate table" if draft.sources and "|" in draft.sources[0][2] else "rate sentence",
                                  draft)
            if q.kind in MONEY_KINDS and q.attribute is None:
                q.attribute = "fee"           # 'minimum to open' not in a table: look for '$' in prose
            elif q.attribute is None and not q.entities:
                return self._done("rate not published", refuse())   # never guessed from blogs or news
        elif self._looks_like_tier(q):
            draft = self._rates(q, as_of)
            if draft:
                return self._done("rate table (tier named)", draft)
        draft = self._prose(q, sid, cutoff=cutoff)
        if draft:
            return self._done("passage", draft)
        draft = None if cutoff else self._from_dropped_pages(q, sid)
        if draft:
            return self._done("passage from a dropped page", draft)
        return self._done("nothing grounded", refuse())

    def _done(self, route: str, draft: Draft) -> Draft:
        self.trace["route"] = route
        self.trace["answer"] = draft.text
        return draft

    def _month_date(self, month: Optional[int]) -> Optional[str]:
        """'back in May' -> the latest May on or before the newest snapshot."""
        if not month or not self.kb.taken_on:
            return None
        latest = self.kb.taken_on[-1]
        year = int(latest[:4])
        date = normalize_date(f"{year:04d}-{month:02d}")
        if date > latest:
            date = normalize_date(f"{year - 1:04d}-{month:02d}")
        return date

    @staticmethod
    def _looks_like_tier(q: Query) -> bool:
        """No rate word, but a tier of a rate product: 'the 17-month special', '$15k'."""
        if q.attribute or q.url:
            return False
        if any(k in q.qualifiers for k in TIER_QUALIFIERS):
            return True                           # 'what's the 17-month jumbo special?'
        return not _DEFINITION.search(q.text) and bool(_TIER_WORDS.search(normalize(q.text)))

    def _from_dropped_pages(self, q: Query, sid: str) -> Optional[Draft]:
        """A page missing from the newer crawl is not a contradiction. If the current site
        cannot answer, an older snapshot may, but only from pages the newer crawl lacks."""
        current = set(self.indexes[sid].pages)
        order = self.kb.snapshot_ids
        for older in reversed(order[:order.index(sid)]):
            draft = self._prose(q, older, skip_paths=current)
            if draft:
                draft.conflict_note = f"From an earlier version of the site ({older}); the page is no longer published."
                return draft
        return None

    # ------------------------------------------------------------------
    # Rates
    # ------------------------------------------------------------------
    def _alias_words(self, product: str, segment: str) -> set:
        """Words of the site's own table titles for the product ('ATV, Snowmobile & Jet Ski Loans')."""
        key = (product, segment)
        if key not in self._aliases:
            words = set()
            for title in self.kb.aliases.get(key, ()):
                words |= set(content_words(title))
            self._aliases[key] = words - GENERIC - {"rate", "rates", "loans", "account", "accounts"}
        return self._aliases[key]

    def match_products(self, q: Query, as_of: Optional[str]) -> List[Tuple[str, str]]:
        """Products whose distinctive words the question uses, best matches only.

        Score: words matched (product name and the site's table titles), then whether it has
        the kind of rate asked for (an APR question, a product with an APR), then whether it is
        an IRA/HSA twin the question did not ask for, then whether a rate sheet or product page
        publishes it, then the share of the product name matched."""
        qwords = set(q.words)
        named_types = qwords & TYPE_WORDS
        need = {k for k in q.qualifiers if k in INSISTED}
        scored = []
        for product, segment in self.kb.products(as_of):
            pwords = {stem(w) for w in tokenize(product)}
            core = pwords - GENERIC or pwords
            aliases = self._alias_words(product, segment)
            hit = (core | aliases) & qwords
            if not hit or named_types - pwords - aliases:
                continue
            if need and not need <= self.kb.qualifier_names(product, segment, as_of):
                continue                          # 'jumbo Share Savings': no jumbo tier exists
            has_kind = not q.kind or q.kind in self.kb.kinds_of(product, segment, as_of)
            unwrapped = not (pwords & WRAPPERS) - qwords
            official = self.kb.product_tier(product, segment, as_of) <= 2
            scored.append(((len(hit), has_kind, unwrapped, official, len(core & qwords) / len(core), -len(pwords)),
                           product, segment, hit))
        if not scored:
            return []
        best = max(s[:5] for s, _, _, _ in scored)
        if q.highest:                             # 'highest certificate rate': every certificate product
            best_hit = next(h for s, _, _, h in scored if s[:5] == best)
            top = [(p, seg) for s, p, seg, h in scored if h >= best_hit and s[3]]
        else:
            top = [(p, seg) for s, p, seg, _ in scored if s[:5] == best]
        shortest = min(len(p.split()) for p, _ in top)
        top = [(p, seg) for p, seg in top if len(p.split()) <= shortest + 3]   # drop long promo names
        if len({p for p, _ in top}) > 1:
            family = [w for w in qwords if w in FAMILY_DEFAULT]
            if family:
                top = [(p, seg) for p, seg in top if p == FAMILY_DEFAULT[family[0]]] or top
        if q.segment:
            top = [(p, seg) for p, seg in top if seg == q.segment] or top
        elif not q.listing:
            personal = [(p, seg) for p, seg in top if seg == "personal"]
            if personal and {p for p, _ in personal} == {p for p, _ in top}:
                top = personal                    # same products in both segments: personal by default
        return sorted(set(top), key=lambda ps: (ps[1] != "personal", ps[0]))

    def _rates(self, q: Query, as_of: Optional[str]) -> Optional[Draft]:
        found = []
        for product, segment in self.match_products(q, as_of):
            tiers = self._tiers(product, segment, q, as_of)
            if tiers:
                found.append((product, segment, tiers))
        if q.highest and len(found) > 1:          # the one product holding the highest value
            def top_value(item):
                primary = item[2][0].key.kind
                return max(_num(t.winner.value) for t in item[2] if t.key.kind == primary)
            found = [max(found, key=lambda item: (top_value(item), -len(item[0])))]
        self.trace["products"] = [f"{p} ({seg})" for p, seg, _ in found]
        self.trace["tiers"] = [f"{t.key} = {t.winner.value} from {t.winner.page.path}"
                               + (f" (effective {t.winner.fact.effective})" if t.winner.fact.effective else "")
                               + (f"; disagreeing: {sorted({(c.page.path, c.value) for c in t.losers})}"
                                  if t.losers else "")
                               for _, _, tiers in found for t in tiers][:30]
        blocks, sources, notes = [], [], []
        for product, segment, tiers in found:
            text, srcs, note = self._describe(product, segment, tiers, q)
            blocks.append(text)
            sources += srcs
            if note:
                notes.append(note)
        if not blocks:
            return None
        text = " ".join(blocks)
        if as_of:
            text = f"As of {as_of}: {text}"
        return Draft(text, _dedupe_sources(sources)[:4], True, " ".join(notes) or None)

    def _tiers(self, product: str, segment: str, q: Query, as_of: Optional[str]) -> List[Tier]:
        all_tiers = self.kb.lookup(product, segment, as_of=as_of)
        if not all_tiers:
            return []
        present = {k for t in all_tiers for k, _ in t.key.qualifiers}
        if any(k not in present for k in q.qualifiers if k in INSISTED):
            return []                                             # 'jumbo' on a product with no jumbo tier
        query = {k: v for k, v in q.qualifiers.items() if k in present}
        if "amount" in present and "amount" not in query:
            amounts = [n for n in q.loose_numbers if n >= 100 and not 1900 <= n <= 2100]
            if len(amounts) == 1:
                query["amount"] = amounts[0]                      # 'a 15000 balance'
        if "purpose" in present and "purpose" not in query:
            query["purpose"] = "purchase"                         # the base mortgage tier
        kinds = self._kinds(product, q, {t.key.kind for t in all_tiers})
        found = [t for t in self.kb.lookup(product, segment, as_of=as_of, **query) if t.key.kind in kinds]
        if "amount" in query:
            found = _narrowest_amount(found)
        return sorted(found, key=lambda t: kinds.index(t.key.kind))

    @staticmethod
    def _kinds(product: str, q: Query, available) -> List[str]:
        if q.kind in MONEY_KINDS:
            return [q.kind] if q.kind in available else []
        available = [k for k in available if k not in MONEY_KINDS]
        defaults = [k for k in default_kinds(product) if k in available]
        if q.kind in available:
            companions = [] if q.kind.startswith("intro") else [k for k in defaults if k != q.kind]
            return [q.kind] + companions
        if q.kind == "rate" and "apr" in available:              # 'interest rate' on a loan = its APR
            return ["apr"]
        if defaults:
            return defaults
        plain = sorted(k for k in available if not k.startswith("intro"))
        return plain[:1] or sorted(available)[:1]

    def _describe(self, product: str, segment: str, tiers: List[Tier], q: Query):
        """One product: '<Name> (<tier>): 5.39% APR', or its tiers one after another."""
        kinds: List[str] = []
        for t in tiers:                           # tiers arrive in the question's kind order
            if t.key.kind not in kinds:
                kinds.append(t.key.kind)
        primary = kinds[0]
        by_label: Dict[str, Dict[str, Tier]] = {}
        order: List[str] = []
        for t in sorted(tiers, key=_tier_order):
            lbl = tier_label(t.key)
            if lbl not in by_label:
                by_label[lbl] = {}
                order.append(lbl)
            by_label[lbl][t.key.kind] = t
        labels = [l for l in order if primary in by_label[l]] or order
        if q.highest and len(labels) > 1:
            labels = [max(labels, key=lambda l: _num(by_label[l][primary].winner.value))]

        def values(lbl: str) -> str:
            row = by_label[lbl]
            first = row.get(primary) or next(iter(row.values()))
            extra = [f"{row[k].winner.value} {KIND_LABEL[k]}" for k in kinds[1:] if k in row]
            return f"{first.winner.value} {KIND_LABEL[first.key.kind]}" + (f" ({', '.join(extra)})" if extra else "")

        name = display(product, segment)
        if len(labels) > 1 and len({values(l) for l in labels}) == 1:
            labels = labels[:1]                   # every tier has the same value: say it once
            text = f"{name}: {values(labels[0])}" + (" for every tier." if labels[0] else ".")
        elif q.highest and labels:
            lbl = labels[0]
            text = f"The highest {name} {KIND_LABEL[primary]} is {values(lbl)}" + (f" ({lbl})." if lbl else ".")
        elif len(labels) == 1:
            lbl = labels[0]
            text = name + (f" ({lbl})" if lbl else "") + f": {values(lbl)}."
        else:
            text = f"{name}: " + "; ".join(f"{l or 'standard'}: {values(l)}" for l in labels) + "."

        used = [t for l in labels for t in by_label[l].values()]
        sources = []
        for t in used:
            agreeing = sorted(t.agreeing(), key=lambda c: (c.page.tier != 2, c.page.tier, c.page.path))
            sources += [(c.fact.url, c.fact.snapshot_id, c.fact.quote) for c in agreeing[:2]]
        stale = sorted({(c.page.path, c.value) for t in used for c in t.losers})
        win = used[0].winner
        when = f"effective {win.fact.effective}" if win.fact.effective else ""
        note = f"Rates {when} ({win.page.path})." if when else None
        if stale:
            note = (f"Some pages disagree ({', '.join(f'{p} shows {v}' for p, v in stale[:3])}); "
                    f"using {win.page.path}" + (f", {when}" if when else "") + ".")
        return text, _prioritise(sources), note

    def _rate_sentence(self, q: Query, sid: str) -> Optional[Draft]:
        """No rate table covers the product: accept a sentence stating the rate, but only on a
        rate sheet, product or site page (not news, blogs or calculators), not describing an
        ended or expired offer, naming the question's words that the site uses, and matching
        every qualifier the question insists on (term, jumbo, Roth, product type)."""
        index = self.indexes[sid]
        rate_words = {"rate", "apr", "apy", "dividend", "interest", "yield", "current", "pay", "charge", "earn"}
        need = [root(w) for w in q.words if w not in rate_words and not _is_date_or_number(w)]
        need = [w for w in need if index.idf(w) > 0]
        insist = {root(str(v)) for k, v in q.qualifiers.items() if k in ("size", "ira_type")}
        insist |= {root(w) for w in q.words if w in TYPE_WORDS}
        if not need or any(index.idf(w) == 0 for w in insist):
            return None                           # e.g. 'jumbo' on a site that has no jumbo product
        term = q.qualifiers.get("term_months")
        best = None
        for score, p in index.search(need + sorted(insist), limit=200):
            if p.page.tier > 3:
                continue
            for sent in sentences(p.text):
                low = normalize(sent)
                if "%" not in sent or re.search(r"\b(ended|expired|no longer|discontinued)\b", low):
                    continue
                words = set(index_words(sent))
                if not (set(need) | insist) <= words:
                    continue
                if term and parse_term_months(sent) != term:
                    continue
                key = (p.date, score)
                if best is None or key > best[0]:
                    best = (key, p, sent)
                break
        if best is None:
            return None
        _, p, sent = best
        return Draft(sent[:300], [(p.url, sid, sent[:200])], True, None)

    # ------------------------------------------------------------------
    # Links
    # ------------------------------------------------------------------
    def _products_on(self, sid: str) -> Dict[str, set]:
        """Canonical path -> words of the products in that page's rate tables."""
        if sid not in self._page_products:
            out: Dict[str, set] = defaultdict(set)
            for tiers in self.kb.buckets.get(sid, {}).values():
                for t in tiers:
                    for c in t.candidates:
                        out[c.page.path] |= set(index_words(c.key.product))
            self._page_products[sid] = out
        return self._page_products[sid]

    def _link(self, q: Query, sid: str) -> Optional[Draft]:
        index = self.indexes[sid]
        want = [root(w) for w in q.words if w not in LINK_WORDS]
        if not want:
            return None
        online = bool(re.search(r"\bonline\b|\bapply\b", normalize(q.text)))
        products = self._products_on(sid)
        scores: Dict[str, float] = defaultdict(float)
        urls: Dict[str, Counter] = defaultdict(Counter)
        where: Dict[str, Tuple[str, str]] = {}
        for anchor, target, page in index.links:
            if not re.match(r"https?://([a-z]+\.)?dupaco\.com", target):
                continue
            internal = re.match(r"https?://(www\.)?dupaco\.com", target) is not None
            path = canonical_path(target)
            key = path if internal else target.split("?")[0].rstrip("/")
            words = set(index_words(anchor)) | set(index_words(re.sub(r"[/_\-.:#?=&]+", " ", target)))
            if internal:
                words |= index.titles.get(path, set()) | products.get(path, set())
            hit = sum(index.idf(w) for w in want if w in words)
            if not hit:
                continue
            dest = index.pages.get(path) if internal else None
            hit *= TRUST[dest.tier] if dest else 1.0
            if online and not internal:
                hit *= 1.5                                       # join., shine. are the online apps
            scores[key] += hit
            urls[key][target] += 1
            where.setdefault(key, (page.url, anchor))
        if not scores:
            return None
        best = max(scores, key=lambda k: (scores[k], -len(k)))
        url = urls[best].most_common(1)[0][0]
        page_url, anchor = where[best]
        return Draft(f"You can use {url}", [(url, sid, anchor), (page_url, sid, anchor)], True, None)

    # ------------------------------------------------------------------
    # Prose
    # ------------------------------------------------------------------
    def _prose(self, q: Query, sid: str, skip_paths=frozenset(), cutoff: Optional[str] = None) -> Optional[Draft]:
        index = self.indexes[sid]
        words, weight = [], {}
        verbs = {stem(t) for t in tokenize(q.text) if t.endswith(("ed", "ing")) and len(t) > 5}
        own = ATTRIBUTE_WORDS.get(q.attribute, ())      # 'what year' / 'phone number' name the pattern
        if all(w in own for w in q.words):
            own = ()
        for w in q.words:
            r = root(w)
            if w in own:
                continue
            if index.idf(r) > 0 and r not in words:
                words.append(r)
                weight[r] = 0.5 if w in verbs else 1.0     # 'received' says less than 'recognition'
        if not words:
            return None
        extra = []
        for w in q.words:
            for syn in THESAURUS.get(w, ()):
                r = root(syn)
                if index.idf(r) > 0 and r not in words and r not in extra:
                    extra.append(r)
                    weight[r] = 0.5
        ranked = index.search(words + extra, limit=300 if q.attribute else 60)
        if not ranked:
            return None
        pattern = ATTRIBUTE_PATTERN.get(q.attribute)
        entity_words = [index_words(e) for e in q.entities]
        threshold = 0.5 if len(words) <= 4 else 0.35
        if q.attribute in ATTRIBUTE_PATTERN:
            threshold *= 0.6                     # the hours / address / date pattern does the checking
        count_re = None
        if q.attribute == "count":
            after = re.search(r"\bhow many\s+(.*)", normalize(q.text))
            counted = [root(stem(t)) for t in tokenize(after.group(1)) if t not in STOPWORDS][:1] if after else []
            nouns = "|".join(re.escape(w) for w in (counted or words))
            number = (r"(?:(?!(?:19|20)\d\d\b)\d[\d,]*|one|two|three|four|five|six|seven|eight|nine|ten|"
                      r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)")
            count_re = re.compile(rf"\b{number}\s+(?:[\w\u2019'-]+\s+){{0,2}}(?:{nouns})", re.I)
        compounds = [c for c in re.findall(r"\b[a-z]+-[a-z]+\b", normalize(q.text))]

        def grounded(p: Passage) -> bool:
            if p.page.path in skip_paths:
                return False
            if cutoff and not (p.date and p.date <= cutoff):
                return False                     # before the first snapshot: only articles published by then
            if coverage(words, p, index) < threshold:
                return False
            if any(ew and not all(w in p.words for w in ew) for ew in entity_words):
                return False
            if pattern and not pattern.search(p.text):
                return False
            if count_re and not count_re.search(normalize(p.text)):
                return False
            low = normalize(p.text)
            if any(c not in low and c.replace("-", " ") not in low for c in compounds):
                return False                     # 'drive-up' must appear as a phrase
            return True

        ratio = 0.25 if q.attribute in ("count", "date") else 0.8  # counts and dates change: newest wins
        pool = newest_close_to_best([(s, p) for s, p in ranked if grounded(p)], ratio)
        if not pool:
            return None
        best, text = None, ""
        # For a fee, date, year or count, the answer sentence must also name the question's most
        # distinctive word: '$30 overdraft fee', not some other '$10 fee' on the page.
        key_word = max(words, key=index.idf) if q.attribute in ("fee", "date", "year", "count") else None
        for _, p in pool[:40]:                    # the answer sentence itself must hold what was asked
            t = self._pick_text(q, p, words + extra, index, count_re, weight)
            if count_re and not count_re.search(normalize(t)):
                continue
            if pattern and not pattern.search(t):
                continue
            if key_word and key_word not in set(index_words(t)):
                continue
            if q.attribute not in ("hours", "address") and any(
                    ew and not set(ew) <= set(index_words(t)) for ew in entity_words):
                continue                          # the sentence must be about what was named
            best, text = p, t
            break
        if best is None:
            return None
        heading = index.headings.get(best.page.path, "")
        if best.page.tier <= 2 and not q.attribute and heading and len(heading) <= 60 \
                and not set(index_words(heading)) <= set(index_words(text)):
            text = f"{heading}: {text}"           # a product page's sentence, named: 'Student Loans & ...: ...'
        sources = [(best.url, sid, text[:200])]
        if q.attribute == "hours":
            # One other schedule as a hint that hours vary; when the question asks whether all
            # branches match, up to three schedules that actually differ.
            wanted = 3 if re.search(r"\b(same|all|every|vary|differ\w*)\b", normalize(q.text)) else 1
            schedules = [frozenset(_TIME.findall(text.lower()))]
            others = []
            for _, other in pool:
                if other.page.path == best.page.path:
                    continue
                more = self._pick_text(q, other, words, index, count_re, weight)
                times = frozenset(_TIME.findall(more.lower())) if more else frozenset()
                if times and times not in schedules:
                    schedules.append(times)
                    others.append(more)
                    sources.append((other.url, sid, more[:200]))
                    if len(others) >= wanted:
                        break
            if others:
                text = f"{text} Hours vary by branch; " + (
                    f"another branch lists: {others[0]}" if len(others) == 1
                    else "other branches list: " + " / ".join(others))
        return Draft(text, sources, True, None)

    @staticmethod
    def _pick_text(q: Query, p: Passage, words: List[str], index: PassageIndex, count_re, weight=None) -> str:
        weight = weight or {}
        pattern = ATTRIBUTE_PATTERN.get(q.attribute)
        if q.attribute in ("hours", "address"):
            return _expand_list(p, pattern) or p.text[:300]
        # The page's first 60 blocks (a news lead sums it up) plus the passage's own blocks.
        own = p.blocks[p.start:p.start + 3] if p.start >= 60 else []
        sents = sentences("\n".join(p.blocks[:60] + own))
        if not sents:
            return p.text[:300]
        total = sum(index.idf(w) for w in words) or 1.0

        how_to = bool(_HOW_TO.search(q.text))

        def score(s: str) -> float:
            sw = set(index_words(s))
            val = sum(index.idf(w) * weight.get(w, 1.0) for w in words if w in sw)
            if how_to and _ACTION.search(s):
                val += 0.3 * total                # 'how can I ...' is answered by what to do
            if pattern and pattern.search(s):
                val += 0.6 * total
            if count_re and count_re.search(" ".join(index_words(s))):
                val += 0.6 * total
            return val

        # When the question needs a time, date, fee or count, the sentence must hold one.
        usable = [k for k in range(len(sents)) if not _FURNITURE.match(sents[k])] or list(range(len(sents)))
        cands = [k for k in usable if pattern and pattern.search(sents[k])
                 or count_re and count_re.search(" ".join(index_words(sents[k])))]
        i = max(cands or usable, key=lambda k: (score(sents[k]), -k))
        out = _DATELINE.sub("", sents[i])
        extend = out.endswith("?") or len(out) < 60 or bool(_DEFINITION.search(q.text))
        j = i + 1
        while extend and j < len(sents) and len(out) < 220:   # a FAQ question, a heading, or "what is X"
            if _FURNITURE.match(sents[j]):
                break
            out = f"{out} {sents[j]}"
            j += 1
            extend = len(sents[j - 1]) < 60 or out.count("?") >= 1 and len(out) < 120
        return out[:450]


# ----------------------------------------------------------------------
def _expand_list(p: Passage, pattern) -> str:
    """Hours or address lines starting in this passage, followed down the page while
    the lines stay short (day names, 'Drive-up', 'Sunday: Closed')."""
    lines: List[str] = []
    for block in p.blocks[p.start:p.start + 12]:
        lines += [l.strip() for l in block.split("\n") if l.strip()]
    first = next((i for i, l in enumerate(lines) if pattern.search(l)), None)
    if first is None:
        return ""
    start = first - 1 if first > 0 and len(lines[first - 1]) < 40 else first
    out = []
    for l in lines[start:]:
        if out and (l.isupper() or not (pattern.search(l) or len(l) < 40)):
            break                                   # stop at 'ABOUT DUPACO...' or a long paragraph
        out.append(l if l.endswith((".", ":")) else l + ".")
        if len(out) >= 10:
            break
    return " ".join(out)


_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december"}


def _is_date_or_number(word: str) -> bool:
    return word in _MONTHS or word.replace(".", "").replace(",", "").isdigit()


def _num(value: str) -> float:
    m = re.search(r"\d+(?:\.\d+)?", value)
    return float(m.group(0)) if m else 0.0


def _tier_order(t: Tier):
    q = dict(t.key.qualifiers)
    amt, yrs = q.get("amount"), q.get("model_years")
    return (
        q.get("purpose") or "",
        q.get("ira_type") or "",
        q.get("term_months") or 0,
        q.get("size") or "",
        -(yrs.hi or 0) if yrs is not None else 0,
        (amt.lo or 0, amt.hi or 0) if amt is not None else (0, 0),
    )


def _narrowest_amount(tiers: List[Tier]) -> List[Tier]:
    """'Up to $100,000', 'up to $200,000' ... all contain $90,000; the tightest tier is the one
    that applies. Kept per kind and per the other qualifiers."""
    best: Dict[tuple, Tier] = {}
    for t in tiers:
        q = dict(t.key.qualifiers)
        amount = q.pop("amount", None)
        group = (t.key.kind, tuple(sorted((k, str(v)) for k, v in q.items())))
        width = (amount.hi if amount and amount.hi is not None else float("inf")) - \
                (amount.lo if amount and amount.lo is not None else 0.0)
        if group not in best or width < best[group][0]:
            best[group] = (width, t)
    return [t for _, t in best.values()]


def _dedupe_sources(sources):
    out, seen = [], set()
    for s in sources:
        key = re.split(r"[?#]", s[0])[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _prioritise(sources):
    """Product pages first, then the rate sheet, so a cited page matches what members read."""
    return sorted(_dedupe_sources(sources), key=lambda s: ("/rates" in s[0] or "business-rates" in s[0], s[0]))
