"""
Question parser: turns a member's question into what the answerer needs.

Nothing here knows about specific products; product names are matched later
against the products actually found in the snapshot (answerer.py).

    words        normalised content words, with member words mapped to site words
                 ('car' -> 'auto', 'cd' -> 'certificate', 'credit card' -> 'visa')
    rate         asks for a rate, APR, APY, dividend or minimum balance
    url          asks for a link
    listing      asks for everything of a kind ("what cards do you offer")
    highest      asks for the best / highest value
    kind         'apr' | 'apy' | 'dividend_rate' | 'rate' | 'intro_apr' | 'min_open' | 'min_earn' | None
    segment      'business' | 'personal' | None (not said)
    qualifiers   term_months, model_years (a year), amount (a dollar figure),
                 purpose, size, ira_type
    attribute    'hours' | 'address' | 'phone' | 'count' | 'date' | 'year' | 'fee' | None
    entities     capitalised names the answer must mention ('Mullan Avenue', 'Galena')
    as_of        a date written in the question ("in May 2026") -> 'YYYY-MM-DD'
    loose_numbers  numbers in the question that no qualifier or date used

Amounts are read in the forms members write them: $15,000, $15k, 15K, 15,000 dollars,
"fifteen thousand", "between $10k and $25k", "under $2,500". A number that cannot be
read with confidence is left loose rather than guessed into a tier.

Only the standard library is used.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from fact_key import PRODUCT_SYNONYMS, normalize

STOPWORDS = set("""
a an the and or of to for in on at by with from is are was were be been do does did can could
would should will shall may might i me my we our you your it its this that these those what
which who whom whose when where how why there here have has had get give tell know like want
please about any all some much many dupaco dupacos credit union s current currently right now
today still also just than then into out up over under per i'm im
""".split())

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
_MONTH_RE = "|".join(MONTHS)

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_NUMWORD = "|".join(list(_UNITS) + list(_TENS) + ["a", "an", "hundred", "and"])

_RATE = re.compile(r"\b(aprs?|apys?|rates?|dividends?|interest|yield)\b|%")
_URL = re.compile(r"\b(link|url|web ?address|web ?page|website|locator)\b")
_LISTING = re.compile(r"\b(what|which)\b.*\b(do you|does dupaco)\s+(offer|have)\b|\ball\b|\blist\b|\bevery\b"
                      r"|\brates for the\b|\bthe rates?\b.*\b(cards|certificates|accounts)\b")
_HIGHEST = re.compile(r"\b(highest|best|top|maximum|max|most)\b")
_TERM_M = re.compile(r"\b(\d{1,3})\s*-?\s*(?:months?|mos?\.?|mths?)(?![a-z])")
_TERM_Y = re.compile(r"\b(\d{1,2})\s*-?\s*(?:years?|yrs?)(?![a-z])")
_YEAR = re.compile(r"(?:\b|(?<=my))((?:19|20)\d\d)\b")       # 2025, MY2025
_SHORT_YEAR = re.compile(r"['\u2019](\d\d)\b")
_VEHICLE = re.compile(r"\b(model|vehicle|car|auto|truck|motorcycle|boat|rv|camper|suv|atv|snowmobile|"
                      r"jet ski|van|pickup|my)\b")
_ENTITY = re.compile(r"(?<![.?!]\s)(?<!^)\b([A-Z][a-zA-Z$]+(?:[- ][A-Z][a-zA-Z]+)*)")
_ENTITY_GENERIC = {"branch", "account", "accounts", "program", "loan", "loans"}
_NOT_ENTITIES = {"Dupaco", "URL", "ATM", "APRs", "I", "APR", "APY", "CD", "CDs", "IRA", "Visa", "Platinum", "Rewards",
                 "Business", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
                 "January", "February", "March", "April", "May", "June", "July", "August",
                 "September", "October", "November", "December", "Share", "Savings", "Checking",
                 "Can", "What", "How", "Is", "Do", "Who", "Where", "When", "Which", "Provide", "Get", "MY"}


@dataclass
class Query:
    text: str
    words: List[str]
    rate: bool = False
    url: bool = False
    listing: bool = False
    highest: bool = False
    kind: Optional[str] = None
    segment: Optional[str] = None
    qualifiers: Dict[str, object] = field(default_factory=dict)
    attribute: Optional[str] = None
    entities: List[str] = field(default_factory=list)
    numbers: List[str] = field(default_factory=list)          # every number written in the question
    loose_numbers: List[float] = field(default_factory=list)  # numbers no qualifier or date used
    as_of: Optional[str] = None                               # a date written in the question
    as_of_month: Optional[int] = None                         # "back in May" (year from context)


_KEEP_S = {"savings", "forbes", "news", "plus", "bonus", "status", "business", "always", "hours", "rewards"}


def stem(w: str) -> str:
    """Fold plurals the same way for questions, product names and pages."""
    if w in _KEEP_S or len(w) <= 3 or w.isdigit() or not w.endswith("s") or w.endswith(("ss", "us", "is")):
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "xes")):
        return w[:-2]
    return w[:-1]


def tokenize(text: str) -> List[str]:
    """Lowercase words and numbers ('branches' stays 'branches'; see stem)."""
    out = []
    for w in re.findall(r"[a-z0-9$]+(?:[/'.][a-z0-9]+)*", normalize(text)):
        w = w.replace("'s", "").strip("'.")
        if w:
            out.append(w)
    return out


def content_words(text: str) -> List[str]:
    t = normalize(text)
    for phrase, site_word in PRODUCT_SYNONYMS.items():
        if " " in phrase:
            t = re.sub(rf"\b{re.escape(phrase)}s?\b", site_word, t)
    words = []
    for w in tokenize(t):
        if w in STOPWORDS:
            continue
        w = stem(PRODUCT_SYNONYMS.get(w, w))
        w = PRODUCT_SYNONYMS.get(w, w)
        if w not in STOPWORDS and w not in words:
            words.append(w)
    return words


# --------------------------------------------------------------------------
# Numbers written as words
# --------------------------------------------------------------------------

def words_to_number(phrase: str) -> Optional[float]:
    """'twenty five thousand' -> 25000; 'a hundred thousand' -> 100000; 'one million' -> 1e6."""
    total, current, seen = 0.0, 0.0, False
    for w in re.split(r"[\s-]+", phrase.strip()):
        if w in ("and", ""):
            continue
        if w in ("a", "an"):
            current = current or 1
        elif w in _UNITS:
            current += _UNITS[w]
            seen = True
        elif w in _TENS:
            current += _TENS[w]
            seen = True
        elif w == "hundred":
            current = (current or 1) * 100
            seen = True
        elif w == "thousand":
            total += (current or 1) * 1000
            current, seen = 0, True
        elif w == "million":
            total += (current or 1) * 1_000_000
            current, seen = 0, True
        else:
            return None
    return total + current if seen else None


def _spelled_amounts(t: str) -> str:
    """'fifteen thousand dollars' -> '$15000'."""
    pat = rf"\b((?:(?:{_NUMWORD})[\s-]+)*(?:{_NUMWORD})[\s-]+(?:thousand|million))\b(?:\s+dollars)?"

    def sub(m):
        n = words_to_number(m.group(1))
        return f"${n:.0f}" if n else m.group(0)
    return re.sub(pat, sub, t)


def _spelled_terms(t: str) -> str:
    """'five-year' -> '5-year', 'twenty-four months' -> '24 months'."""
    pat = rf"\b((?:{'|'.join(_TENS)})(?:[\s-](?:{'|'.join(k for k in _UNITS if _UNITS[k] < 10)}))?|" \
          rf"{'|'.join(_UNITS)})(?=[\s-]*(?:months?|years?|yrs?|mos?)\b)"

    def sub(m):
        n = words_to_number(m.group(1))
        return str(int(n)) if n is not None else m.group(0)
    return re.sub(pat, sub, t)


# --------------------------------------------------------------------------
# Amounts
# --------------------------------------------------------------------------

_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"


def _money_forms(t: str) -> str:
    """Bring every way of writing money to '$N'."""
    t = _spelled_amounts(t)
    def thousands(m):                                                    # $15k, 15k (not 401k / 457k)
        if m.group(1) in ("401", "403", "457") and "$" not in m.group(0):
            return m.group(0)
        return f"${float(m.group(1).replace(',', '')) * 1000:.0f}"
    t = re.sub(rf"\$?\s*{_NUM}\s*k\b", thousands, t)
    t = re.sub(rf"\$?\s*{_NUM}\s*(?:m|mm|million)\b",
               lambda m: f"${float(m.group(1).replace(',', '')) * 1_000_000:.0f}", t)
    t = re.sub(rf"(?<!\$){_NUM}\s*(?:dollars|usd|bucks)\b", lambda m: f"${m.group(1)}", t)
    return t


def _amount(t: str) -> Tuple[Optional[float], List[Tuple[int, int]]]:
    """The dollar amount a question is about, and the text spans it used."""
    def val(s):
        return float(s.replace(",", ""))
    m = re.search(rf"\bbetween\s+\${_NUM}\s+(?:and|to|-)\s+\$?{_NUM}", t) or \
        re.search(rf"\${_NUM}\s*(?:-|to)\s*\$?{_NUM}", t)
    if m:
        return val(m.group(1)), [m.span()]
    m = re.search(rf"\b(under|less than|below|up to|at least|over|more than|above)\s+\${_NUM}", t)
    if m:
        x = val(m.group(2))
        x = x - 0.01 if m.group(1) in ("under", "less than", "below") else \
            x + 0.01 if m.group(1) in ("over", "more than", "above") else x
        return x, [m.span()]
    m = re.search(rf"\${_NUM}\s*(?:\+|or more|and up)", t)
    if m:
        return val(m.group(1)), [m.span()]
    m = re.search(rf"\${_NUM}", t)
    if m:
        return val(m.group(1)), [m.span()]
    return None, []


# --------------------------------------------------------------------------
# Dates written in the question
# --------------------------------------------------------------------------

_PAST = re.compile(r"\b(was|were|did|had|used to|back in|as of|previously|earlier)\b")


def _month_end(y: int, mo: int, d: Optional[int] = None) -> Optional[str]:
    """'YYYY-MM-DD', or None for an impossible date (month 13, day 40)."""
    if not 1 <= mo <= 12:
        return None
    last = calendar.monthrange(y, mo)[1]
    if d is not None and not 1 <= d <= 31:
        return None
    return f"{y:04d}-{mo:02d}-{min(d or last, last):02d}"


def _question_date(t: str) -> Tuple[Optional[str], Optional[int], List[Tuple[int, int]]]:
    """'as of May 2026', 'in June 2026' (past tense), 'on 2026-05-31', 'back in May'."""
    cue = r"(?:as of|in|on|during|back in|at the end of|by)\s+"
    m = re.search(rf"{cue}(20\d\d)-(\d\d)(?:-(\d\d))?\b", t)
    if m:
        date = _month_end(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        if date:
            return date, None, [m.span()]
    m = re.search(rf"{cue}({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d\d)\b", t)
    if m and (_PAST.search(t) or "as of" in m.group(0)):
        date = _month_end(int(m.group(3)), MONTHS.index(m.group(1)) + 1, int(m.group(2)))
        if date:
            return date, None, [m.span()]
    m = re.search(rf"{cue}({_MONTH_RE})\s+(?:of\s+)?(20\d\d)\b", t)
    if m and (_PAST.search(t) or "as of" in m.group(0)):
        return _month_end(int(m.group(2)), MONTHS.index(m.group(1)) + 1), None, [m.span()]
    m = re.search(rf"\b(?:as of|back in|in|during|last)\s+({_MONTH_RE})\b(?!\s+\d)", t)
    if m and _PAST.search(t):
        return None, MONTHS.index(m.group(1)) + 1, [m.span()]
    return None, None, []


def normalize_date(value: Optional[str]) -> Optional[str]:
    """'2026-05' -> '2026-05-31'; '2026-05-31T10:00' -> '2026-05-31'; 'May 2026' -> '2026-05-31'."""
    if not value:
        return None
    v = normalize(str(value))
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        return _month_end(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{4})-(\d{1,2})$", v) or re.match(r"^(\d{4})/(\d{1,2})$", v)
    if m:
        return _month_end(int(m.group(1)), int(m.group(2)))
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", v)
    if m:
        return _month_end(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    d, _, _ = _question_date("as of " + v)
    return d


# --------------------------------------------------------------------------
# Kind and attribute
# --------------------------------------------------------------------------

def _kind(t: str) -> Optional[str]:
    if re.search(r"\bminimum\b.*\b(open|opening|deposit)\b|\b(open|opening)\b.*\bminimum\b|\bmin\.? to open\b", t):
        return "min_open"
    if re.search(r"\bminimum\b.*\b(earn|earning)\b|\bmin\.? to earn\b", t):
        return "min_earn"
    if re.search(r"\b(intro|introductory|promotional|balance transfer)\b", t):
        if re.search(r"\b(apy|annual percentage yield|yield)\b", t):
            return "intro_apy"
        if re.search(r"\bdividend\b", t):
            return "intro_dividend_rate"
        return "intro_apr"
    if re.search(r"\b(apy|annual percentage yield|yield)\b", t):
        return "apy"
    if re.search(r"\b(aprs?|annual percentage rate)\b", t):
        return "apr"
    if re.search(r"\bdividend\b", t):
        return "dividend_rate"
    if re.search(r"\binterest rate\b", t):
        return "rate"
    return None


def _attribute(t: str) -> Optional[str]:
    if re.search(r"\bhours?\b", t):
        return "hours"
    if re.search(r"\bwhat year\b|\bwhich year\b|\b(founded|established|chartered)\b", t):
        return "year"
    if re.search(r"\baddress\b|\bwhere is\b|\bwhere's\b|\blocated\b", t):
        return "address"
    if re.search(r"\bphone\b|\bphone number\b|\bcall\b", t):
        return "phone"
    if re.search(r"\bhow many\b", t):
        return "count"
    if re.search(r"^when\b|\bwhat date\b|\bwhen does\b|\bwhen did\b|\bwhen will\b", t):
        return "date"
    if re.search(r"\bstill open\b|\bopen today\b|\b(closed|closing|closure)\b", t):
        return "date"
    if re.search(r"\bfees?\b|\bcost\b|\bcharge\b|\bhow much is\b|\bhow much does it cost\b|\bprice\b", t):
        return "fee"
    return None


# --------------------------------------------------------------------------
# The parser
# --------------------------------------------------------------------------

def parse(question: str) -> Query:
    raw = normalize(question)
    q = Query(text=question, words=content_words(question))
    q.kind = _kind(raw)
    q.rate = bool(_RATE.search(raw)) or q.kind in ("min_open", "min_earn") or \
        bool(re.search(r"\bhow much (?:does|do|will|would)\b.*\b(pay|earn)\b", raw))
    q.url = bool(_URL.search(raw)) or bool(re.search(r"\b(join|apply|sign up)\b.*\bonline\b", raw))
    q.listing = bool(_LISTING.search(raw))
    q.highest = bool(_HIGHEST.search(raw))
    q.attribute = _attribute(raw)
    if q.rate and q.attribute == "fee":
        q.attribute = None                       # "what rate do you charge" asks for a rate
    if re.search(r"\bbusiness\b", raw):
        q.segment = "business"
    elif re.search(r"\b(personal|consumer)\b", raw):
        q.segment = "personal"

    t = _spelled_terms(_money_forms(raw))
    used: List[Tuple[int, int]] = []
    q.as_of, q.as_of_month, spans = _question_date(t)
    used += spans

    quals: Dict[str, object] = {}
    m = _TERM_M.search(t)
    if m:
        quals["term_months"] = int(m.group(1))
        used.append(m.span())
    else:
        for m in _TERM_Y.finditer(t):
            before, after = t[max(0, m.start() - 6):m.start()], t[m.end():m.end() + 7]
            if "model" in before or after.startswith((" model", "-model", " old", "-old", " ago")):
                continue                                   # '2025 model-year', '3 years old' are not terms
            quals["term_months"] = int(m.group(1)) * 12
            used.append(m.span())
            break
    if _VEHICLE.search(t):
        for m in list(_YEAR.finditer(t)) + list(_SHORT_YEAR.finditer(t)):
            if any(a <= m.start() < b for a, b in used):
                continue
            near = t[max(0, m.start() - 25):m.end() + 25]
            if _VEHICLE.search(near):
                y = int(m.group(1))
                quals["model_years"] = y if y > 99 else 2000 + y
                used.append(m.span())
                break
    amount, spans = _amount(t)
    if amount is not None:
        quals["amount"] = amount
        used += spans
    if re.search(r"\b(refinanc\w*|refi)\b", t):
        quals["purpose"] = "refinance"
    elif re.search(r"\b(purchase|buy|buying|purchasing)\b", t) and re.search(r"\b(mortgage|home)\b", t):
        quals["purpose"] = "purchase"
    if re.search(r"\bjumbo\b", t):
        quals["size"] = "jumbo"
    m = re.search(r"\b(roth|traditional)\b", t)
    if m:
        quals["ira_type"] = m.group(1)
    q.qualifiers = quals

    loose = []
    for m in re.finditer(r"(?<![\w/.])(\d[\d,]*(?:\.\d+)?)(?![\w/%])", t):
        if not any(a <= m.start() < b for a, b in used):
            loose.append(float(m.group(1).replace(",", "")))
    q.loose_numbers = loose

    entities = []
    for e in _ENTITY.findall(question):
        words = [w for w in e.split() if w not in _NOT_ENTITIES and w.lower() not in _ENTITY_GENERIC]
        if words and not all(w.isupper() for w in words):
            entities.append(" ".join(words))
    q.entities = entities
    q.numbers = re.findall(r"\d+(?:[.,]\d+)*", question)
    return q
