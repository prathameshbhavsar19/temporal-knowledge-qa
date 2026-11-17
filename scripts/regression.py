"""Generated regression check: ask about every rate tier the system knows, in every snapshot.

    python scripts/regression.py                 # all snapshots, summary + first failures
    python scripts/regression.py --show 50       # print more failures

For each snapshot and each tier of each product, a question is written from the tier's
own description in two wordings, e.g.

    "What is the APR on Auto Loan for 60-month, 2024-2027 model years?"
    "Auto Loan 60-month, 2024-2027 model years APR"

and asked with as_of = that snapshot's date. A check passes when the answer contains
the tier's winning value and none of the stale values other pages show for that tier
(unless that number is also the current value of another tier of the same product).

This tests the round trip question -> parser -> product match -> tier lookup on far
more cases than the 40 public questions. It needs the snapshots locally.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "solution"))

from run_eval import load_snapshots, load_submission  # noqa: E402

_NUM = re.compile(r"\d+(?:\.\d+)?")


def numbers(text: str) -> set:
    return {float(x) for x in _NUM.findall(text.replace(",", ""))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", default=str(ROOT / "snapshots"))
    ap.add_argument("--submission", default=str(ROOT / "solution"))
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()

    system = load_submission(Path(args.submission))
    for snap in load_snapshots(Path(args.snapshots)):
        system.ingest(snap)
    from answerer import KIND_LABEL, display   # loaded by the submission
    from fact_key import MONEY_KINDS
    from resolver import tier_label, value_key

    kb = system.kb
    total, failures, by_product, slow = 0, [], Counter(), 0.0
    for sid, taken in zip(kb.snapshot_ids, kb.taken_on):
        for (product, segment, kind, _), tiers in sorted(kb.buckets[sid].items(), key=lambda kv: str(kv[0])):
            if kind in MONEY_KINDS or kind == "points":
                continue
            current = {value_key(t.winner.value) for ts in kb.buckets[sid].values() for t in ts
                       if t.key.product == product and t.key.segment == segment}
            current_numbers = {n for v in current for n in v}
            name = display(product, segment)
            for t in tiers:
                label = tier_label(t.key)
                for question in (f"What is the {KIND_LABEL[kind]} on {name}" + (f" for {label}?" if label else "?"),
                                 f"{name} {label} {KIND_LABEL[kind]}".replace("  ", " ")):
                    total += 1
                    started = time.perf_counter()
                    text = system.answer(question, taken).text
                    slow = max(slow, time.perf_counter() - started)
                    got = numbers(re.sub(r"^As of [\d-]+: ", "", text))
                    want = set(value_key(t.winner.value))
                    stale = {n for c in t.losers for n in value_key(c.value)} - current_numbers
                    problem = None
                    if not want <= got:
                        problem = f"missing {t.winner.value}"
                    elif stale & got:
                        problem = f"stale {sorted(stale & got)}"
                    if problem:
                        failures.append((sid, question, problem, text))
                        by_product[f"{product} ({segment})"] += 1

    print(f"{total} generated questions, {total - len(failures)} passed, {len(failures)} failed; "
          f"slowest answer {slow * 1000:.0f} ms")
    for product, n in by_product.most_common():
        print(f"  {n:4d}  {product}")
    for sid, question, problem, text in failures[:args.show]:
        print(f"\n[{sid}] {question}\n  {problem}\n  -> {text[:240]}")


if __name__ == "__main__":
    main()
