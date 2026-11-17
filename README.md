# Temporal Knowledge QA

A knowledge system that answers credit-union member questions from five website snapshots
(May to September 2026), giving the **current, correctly tiered value** with its source,
answering **as of** a past date, listing the site's **real contradictions**, and saying
**"I don't know"** when the site doesn't say. Built for the interface.ai Software Engineer II
take-home ("Knowledge Hackathon").

No API keys, no network, standard library only. See [NOTES.md](NOTES.md) for every decision rule.

## Results

| | Starter | This solution |
| --- | --- | --- |
| Final score, public set (40 questions) | 0.093 | **0.890** |
| Stale-value trap hits | | **0** |
| Fresh blind set (32 questions written after the last change, values read from the raw pages) | | **0.969** on first run |
| Generated regression (every tier, every snapshot, two wordings: 2,056 questions) | | **2,056 pass** |
| Own extra set (24 questions: rewordings, as-of dates, changed facts, refusals) | | 1.00 |
| Kit self-test | | 5 / 5 answers, conflict F1 1.0 |
| Consistency (rewordings and repeats) | 1.00 | 1.00 |
| Answer time, 95th percentile | 0.00 s | 0.26 s |
| Ingest, all five snapshots | | 10 s |

Public set by category:

| Category | Starter | This solution |
| --- | --- | --- |
| stale_trap | 0.14 | **1.00** |
| tier_disambiguation | 0.00 | **1.00** |
| should_refuse | 0.00 | **1.00** |
| entity_attribution | 0.25 | **1.00** |
| branch_info | 0.17 | 0.95 |
| temporal_as_of | 0.25 | 0.75 |
| list_completeness | 0.00 | 0.75 |
| conflict_resolution | 0.00 | 0.67 |
| current_value | 0.00 | 0.67 |

The conflicts pillar is not scored locally (the kit has no public conflict answer key). The system
reports 16 conflicts: stale pages within a snapshot, the branch count older press releases still
state, and rates that changed between crawls. They cover every stale value the public questions
trap on. The remaining public misses are explained in [NOTES.md](NOTES.md#known-limitations-public-set).

## How it works

```
ingest(snapshot)
  page_classifier   trust tier from the URL, frozen pages, news/blog publish dates
  table_extractor   every rate table (and labelled rate line) -> facts: product, segment, kind,
                    tier, value, the table's own "effective as of" date, the quoted row
  resolver          winner per fact per snapshot: not ended > table date > re-fetched > page tier
                    > fetch date; as-of views; conflicts within and across snapshots
  prose_facts       branch counts stated in press releases, with publish dates
  passages          keyword index over page text, weighted by trust and date

answer(question, as_of)
  question_parser   product words; kind (APR/APY/rate/minimum); tier (term, model year, amount,
                    purpose, jumbo, Roth) in the forms members write them; what is asked (hours,
                    address, count, date, year, fee, link); a date written in the question
  answerer          link  -> best link on the site
                    rate  -> resolved facts (rate tables, or a rate stated on a trusted page)
                    other -> newest trusted passage whose sentence holds what was asked
                    nothing grounded -> refusal with no numbers
```

| File | Role |
| --- | --- |
| `solution/solution.py` | `build()`: the contract (`ingest`, `answer`, `conflicts`), plus `explain()` |
| `solution/fact_key.py` | Shared vocabulary: what a fact is about |
| `solution/table_extractor.py` | Rate tables and labelled rate lines to facts |
| `solution/page_classifier.py` | Page trust, frozen pages, publish dates |
| `solution/resolver.py` | Winners per snapshot, as-of lookups, conflicts |
| `solution/prose_facts.py` | Branch counts in press releases |
| `solution/passages.py` | Trust- and date-ranked keyword search |
| `solution/question_parser.py` | Question to product, kind, tier, intent, date |
| `solution/answerer.py` | Routing and answer writing |
| `scripts/ask.py` | Ask from the command line and see why (`explain()`) |
| `scripts/regression.py` | Generated regression over every tier in every snapshot |
| `tests/` | 89 unit, end-to-end and fuzz tests; extra and blind question sets |
| `.github/workflows/tests.yml` | CI: lint and tests on Python 3.10, 3.11, 3.12, 3.13 |

## Run it

Python 3.9 or newer (the kit grades on 3.11). Put the kit's `snapshots_zip/` (or unpacked
`snapshots/`) in the repo root; the snapshot data is not in this repo.

```bash
pip install -r requirements.txt          # nothing to install: standard library only
python -m unittest discover -s tests -v  # 89 tests, no snapshot data needed
python harness/run_eval.py --submission solution --eval eval/public_eval.jsonl
python harness/run_eval.py --submission solution --eval tests/blind_v2.jsonl
python scripts/regression.py             # needs the snapshots
python scripts/ask.py "What's the APR for a 60-month auto loan on a 2025 car?"
```

## Example answers

| Question | Answer |
| --- | --- |
| What's the APR for a 60-month auto loan on a 2025 model-year vehicle? | Auto Loan (60-month, 2024-2027 model years): 5.39% APR. *(note: /calculators/vehicle-affordability shows 5.34%; using /rates, effective 2026-08-31)* |
| What was the 17-month jumbo special APY in August 2026? | As of 2026-08-31: Fixed Certificate Special (17-month, jumbo): 4.05% APY (4.01% dividend rate). |
| What's the APR on a $15,000 boat loan for 36 months? | Boat & RV Loan (36-month, $10,000-$24,999): 7.29% APR. |
| What rate does a jumbo Share Savings account earn? | I'm sorry, that isn't listed on Dupaco's website. Please contact Dupaco directly for that detail. |
| How many branches does Dupaco have? | It has over 600 employees and 22 branch office locations. |
| When does the Hy-Vee branch close? | This location will close March 31, 2026. All Dupaco Hy-Vee branch employees will remain with the credit union, transitioning to other branches. |
