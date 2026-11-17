# NOTES

## API keys and dependencies

**None.** The solution uses only the Python standard library (tests pass on Python 3.9, 3.11 and 3.12).
No paid APIs, no model downloads, no network calls at ingest or answer time.
`requirements.txt` is intentionally empty.

## Approach in one paragraph

All judgement happens in `ingest()`. Every rate table on every page (and every rate written as a
labelled line, "6-month certificate APY: 3.50%") becomes a dated, keyed fact: product,
personal/business, kind, tier, value, page, and the table's own "effective as of" date. For each
snapshot a resolver picks one winner per fact with explicit rules and remembers every value it
overruled. Page text is indexed for keyword search, ranked by trust and date. `answer()` only parses
the question and looks things up: rate questions go to the resolved facts, everything else to
passage search, and anything the site does not state is refused. Because answering is a lookup, it
takes about 0.3 s and gives the same answer every time.

## How "current" is decided

Facts from rate tables (`resolver.py`), per snapshot, among pages stating the same fact:

1. An offer the page itself says has ended or expired never wins.
2. A table with an "effective as of" date beats one without.
3. The newest effective date wins. Each table carries its own date (the note under it);
   one page can hold tables effective months apart.
4. A page that was re-fetched beats a frozen copy (fetched before the previous snapshot).
5. The more trusted page type wins: rate sheet > product page > site page > news > blog >
   calculator, promo landing page or PDF.
6. The most recently fetched page wins.

A majority vote is never used: pages that copy the same table are not independent votes.

"Same fact" means same product, segment, kind and tier. Model-year ranges match when they overlap
by at least half (a calculator's "2023-26 Model Year" and `/rates`' "2024-27 Model Year" both cover a
2025 car), so stale copies under old tier labels are still caught. Values are compared by their
numbers, so "$25" and "$25.00" agree.

Rows that start "Purchase:" or "Refinance:" are mortgage rows wherever they appear, so the
home-equity page's copy of the refinance table ("Cash-Out Refinance", "Home Equity Refinance") is
checked against `/rates` instead of becoming a separate product. Tables on `/business-rates` and
`/business/...` pages are business products even when the table title does not say "Business".

Facts from prose (`answerer.py`, `passages.py`):

- Relevance is BM25 over word roots, times a trust weight per page type, boosted when the page
  title matches the question. A small thesaurus adds the site's word for a member's word at half
  weight (update/change, university/college, established/founded); it never counts toward how much
  of the question a passage covers.
- Among passages close to the best score, the newest wins. News and blog articles carry their
  publish date (the first date at the top of the article, never a date mentioned in the text);
  undated articles count as oldest; undated site and product pages count as current.
- For counts and dates ("how many branches", "when does it close"), recency weighs more:
  the newest article's "22 branch office locations" beats older articles' "23".
- The answer sentence itself must contain what was asked: a time, an address, a date, a year, a
  dollar amount or a number before the counted noun, plus the question's most distinctive word
  ("$30 overdraft fee", not another "$10 fee" on the page) and any name the question mentions
  (Hy-Vee, Forbes, Mullan Avenue). Rate tables, "N min read" lines and related-article listings are
  never quoted as prose.

**As of a date.** The date is the `as_of` argument, else a date written in the question ("in May
2026" with past tense, "as of June 9, 2026", "back in May"), else the newest snapshot. Partial
dates mean the end of the period ("2026-05" -> 2026-05-31). The newest snapshot taken on or before
the date is read. If the current site cannot answer, older snapshots may, but only from pages
missing from the newer crawl; a page that still exists is never overruled by its old copy.

**Before the first snapshot** (May 12, 2026) the site was never seen. The first snapshot is read, but
only rate tables whose "effective as of" date is on or before the asked date count, and only
articles published by then. Anything else is refused rather than guessed.

## Reading the question (`question_parser.py`)

- Amounts in the forms members write them: $15,000, $15k, 15K, "15,000 dollars", "fifteen thousand",
  "between $10k and $25k", "under $2,500" (read as $2,499.99), "$25,000+". "401k" is not $401,000.
  When several "up to" tiers contain the amount, the tightest one applies ($90,000 -> up to $100,000).
- Terms: "60 months", "60-month", "60 mo", "5 years", "5yr", "five-year". "3 years old" is not a term.
- Model years: "2025", "MY2025", "'25", only next to a vehicle word, never the year of an "as of" date.
- Kinds: APR, APY, dividend rate, "interest rate", intro/promotional rates, "minimum to open",
  "minimum to earn". What is asked otherwise: hours, address, phone, count, date, year, fee, link.
- A question with no rate word but a tier ("What's the 17-month jumbo special?", "$15k in
  savings") is tried against the rate tables first.

## Matching a product (`answerer.py`)

Product names come from the tables, never from a hand-written product list. A question matches a
product by, in order: how many of its words appear in the product name or in the site's own table
titles for that product ("ATV, Snowmobile & Jet Ski Loans", "Home Equity Line of Credit (HELOC)");
whether the product has the kind asked for (an APR question prefers a product with an APR); whether
a rate sheet or product page publishes it (so a promo landing page's product does not beat the rate
sheet's); and the share of the product name matched. "Highest" looks across every product sharing
the matched words ("highest certificate APY" includes the specials).

A tier the product does not have is not guessed: "jumbo Share Savings" is refused because Share
Savings has no jumbo tier, while "jumbo special" finds the jumbo certificate special.

## What the system refuses

- A rate question about a product with no rate table, unless a rate sheet, product or site page
  states the rate in a sentence that names every distinctive word of the question (never news,
  blogs or calculators, never an ended or expired offer). This refuses the 7/1 ARM and construction
  loan rates, which the site discusses but never prices.
- A tier the product does not have (jumbo Share Savings, a jumbo personal loan).
- A question naming a place or product the site mentions without the requested detail
  ("Mullan Avenue branch" appears only in a blog story, with no hours).
- Refusals contain no numbers: "I'm sorry, that isn't listed on Dupaco's website. Please contact
  Dupaco directly for that detail."

Every number and link in an answer is copied from a snapshot page; nothing is generated.

## Answer style

- One product, one tier: `Auto Loan (60-month, 2024-2027 model years): 5.39% APR.`
- No tier named: all current tiers, base tier first (mortgages default to purchase). When every
  tier has the same value (a $25 minimum to open), it is said once.
- Deposits answer with APY and dividend rate; mortgages with rate and APR; loans and cards with APR
  (never the intro rate unless asked).
- Stale values never appear in the answer text. They go in `conflict_note`, with the winning
  page and its effective date ("Some pages disagree (/calculators/vehicle-affordability shows
  5.34%); using /rates, effective 2026-08-31.").
- A sentence from a product page is named by the page heading ("Student Loans & Refinancing: ...").
- Sources: the product page and rate sheet that agree with the winner.

## Conflicts

Three kinds, 16 entries on the real data:

| Kind | Rule | Entries |
| --- | --- | --- |
| Within a snapshot | A page whose value for a tier differs from the winner is stale for that product. Pages repeating the same stale table share one entry. The same stale pages seen in several snapshots are one entry, kept from the latest snapshot that shows them. | 11 |
| Branch count | Every news release ends with "It has ... N branch office locations." The newest article's count is current; older articles' counts (23, 24, 20, 19, and 18 on the About page) are the contradiction. | 1 |
| Across snapshots | A tier whose winning value changed from one crawl to the next; one entry per product, resolved to the newest value. A change that is exactly a stale page's story, or that the business rate sheet repeats for the personal specials, is told once. | 4 |

The within-snapshot entries cover every stale value the public questions trap on: auto 5.34% and
5.54% (vehicle calculator, Switch & Earn page) vs 5.39% and 5.59%; mortgage 6.037% / 6.061% / 5.954%
(`/homebuying-guidance`) vs `/rates`; and the change entries cover the 10-month jumbo special moving
from 3.90% to 4.00% APY. Minimum-balance columns are used for answers but never reported as
contradictions. Precision counts in the score, so near-duplicates are merged and growth figures
(members, assets, employees) are not reported.

## Testing and guarding against overfitting

| Check | What it is | Result |
| --- | --- | --- |
| Public set | the kit's 40 questions | 0.890 (0 stale-value hits) |
| Extra set | 24 questions written independently: rewordings, as-of dates, changed facts, refusals | 1.00 |
| Blind set v1 | 28 questions written after the first version, before looking at its answers | 0.785 at first; 1.00 after the fixes below (so no longer blind) |
| Blind set v2 | 32 new questions written after all fixes, values read from the raw pages | **0.969 on first run** (one miss, "Is the Hy-Vee branch still open?", then fixed) |
| Generated regression | `scripts/regression.py`: every tier of every product in every snapshot, asked two ways with that snapshot's date | 2,056 / 2,056 |
| Unit and end-to-end tests | 89 tests on small built-in pages shaped like the real site; run in CI on 3.9, 3.11, 3.12 | all pass |
| Fuzz tests | empty, very long, unicode, injection-like and random questions; impossible dates | no crash, under 2 s, refusals never state a number |
| Kit self-test | `harness/selftest` | 5 / 5 answers, conflict F1 1.0 |
| Fresh clone | Python 3.11, two hash seeds | same answers, p95 0.4 s, ingest 15 s |

The first blind set found real gaps, which were fixed in general form, not for the questions:
$15k / ten thousand dollars / 5yr / '25 were not read; a date written in the question was ignored;
"home equity line of credit" and "ATV" did not find their products (fixed with the site's own table
titles); "founded" collided with "Foundation"; "jumbo Share Savings" returned all Share Savings tiers.
The generated regression then found three more: overlapping "up to" amount tiers all being listed,
"intro APY" being read as intro APR, and a HELOC name variant without an APR winning a tie.

## Known limitations (public set)

| Question | Result | Why |
| --- | --- | --- |
| dup-005 certificate list | 0 locally | Correct full list, but the item has no `must_include`; it scores only with the LLM judge. |
| dup-088 volunteer time off | 256 instead of 294 | The newest article (2024 figure) says 256; the expected answer uses the older article. Newest-wins is applied consistently. |
| dup-081 eligibility | 0 | The join page says "live or work within our charter area" and links a map; the states appear only in the About paragraph, which shares no words with the question. |
| dup-060 updating contact info | 0 | The answer ("call 800-373-7600") is deep in the privacy policy; a closer-matching privacy sentence wins. |
| dup-064 / dup-066 hours | 0.85 | Correct hours from branch-opening news; the expected source is the home or contact page. |

## Debugging

`scripts/ask.py "question" [--as-of DATE]` prints the answer, sources and note, then `explain()`:
the parsed question, the snapshot read, the route taken (link, rate table, passage, refusal) and,
for rate answers, every tier with its winning page, effective date and the pages that disagree.
`scripts/ask.py --conflicts` prints the conflict list.

## Why no LLM

An LLM was considered for messy prose at ingest time and rejected for the submission: the
authoritative values are in tables that rules read exactly; an LLM adds cost, a key, latency and
run-to-run variation, and the scorer zeroes an answer for one stale number. The natural next step
would be an optional, cached LLM pass only for prose questions the keyword search cannot ground
(eligibility, how-to questions), with the same "every number must be copied from a page" check.
