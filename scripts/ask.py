"""Ask the system questions from the command line and see why it answered as it did.

    python scripts/ask.py "What's the APR for a 60-month auto loan on a 2025 car?"
    python scripts/ask.py --as-of 2026-05-31 "10-month jumbo special APY?"
    python scripts/ask.py                       # interactive: one question per line, 'q' to quit
    python scripts/ask.py --conflicts           # print every contradiction found

Needs the snapshots locally (snapshots/ or snapshots_zip/ at the repo root, as in the kit).
Prints the answer, its sources and note, then the trace from explain(): the parsed
question, the snapshot read, the route taken, and for rate answers every tier with its
winning page and any pages that disagree.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

from run_eval import load_snapshots, load_submission  # noqa: E402


def show(system, question: str, as_of) -> None:
    started = time.perf_counter()
    ans = system.answer(question, as_of)
    took = time.perf_counter() - started
    print(f"\nQ: {question}" + (f"   (as of {as_of})" if as_of else ""))
    print(f"A: {ans.text}")
    print(f"   confident={ans.confident}  {took * 1000:.0f} ms")
    for s in ans.sources:
        print(f"   source: {s.url}  [{s.snapshot_id}]")
    if ans.conflict_note:
        print(f"   note: {ans.conflict_note}")
    trace = system.explain(question, as_of)
    print("   why:")
    for key in ("route", "snapshot", "cutoff", "parsed", "products", "tiers"):
        value = trace.get(key)
        if value in (None, [], {}):
            continue
        if isinstance(value, list):
            print(f"     {key}:")
            for item in value:
                print(f"       - {item}")
        else:
            print(f"     {key}: {value}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="*")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--snapshots", default=str(ROOT / "snapshots"))
    ap.add_argument("--submission", default=str(ROOT / "solution"))
    ap.add_argument("--conflicts", action="store_true")
    args = ap.parse_args()

    started = time.perf_counter()
    system = load_submission(Path(args.submission))
    for snap in load_snapshots(Path(args.snapshots)):
        system.ingest(snap)
    print(f"ingested in {time.perf_counter() - started:.1f} s")

    if args.conflicts:
        for c in system.conflicts():
            print(f"\n- {c.topic}\n  values: {', '.join(c.values)}\n  resolution: {c.resolution}\n  reason: {c.reason}")
        return
    if args.question:
        show(system, " ".join(args.question), args.as_of)
        return
    if not sys.stdin.isatty():
        for line in sys.stdin:
            if line.strip():
                show(system, line.strip(), args.as_of)
        return
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in ("q", "quit", "exit"):
            break
        if line:
            show(system, line, args.as_of)


if __name__ == "__main__":
    main()
