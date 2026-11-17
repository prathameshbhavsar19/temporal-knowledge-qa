"""
Temporal knowledge QA for the interface.ai Knowledge Hackathon.

    build() -> KnowledgeSystem
        ingest(snapshot)          rate tables -> dated facts -> winners per snapshot;
                                  page text -> passage index per snapshot
        answer(question, as_of)   link / rate lookup / passage search / refusal
        conflicts()               real contradictions, one per product and stale value set

Everything is decided at ingest time with plain rules (see NOTES.md); answering
is a lookup, so it is fast and gives the same answer every time.
No API keys and no network calls. Only the standard library is used.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(next(d / "harness" for d in HERE.parents if (d / "harness" / "contract.py").exists())))

from contract import Answer, Conflict, Snapshot, Source  # noqa: E402

from answerer import Answerer  # noqa: E402
from page_classifier import canonical_path, classify, is_variant  # noqa: E402
from passages import PassageIndex  # noqa: E402
from resolver import KnowledgeBase  # noqa: E402


class TemporalKnowledgeSystem:
    def __init__(self) -> None:
        self.kb = KnowledgeBase()
        self.indexes = {}
        self.answerer = Answerer(self.kb, self.indexes)

    def ingest(self, snapshot: Snapshot) -> None:
        pages = [(p.url, p.markdown, p.fetched_at) for p in snapshot.pages]
        previous = self.kb.taken_on[-1] if self.kb.taken_on else None
        self.kb.add_snapshot(snapshot.snapshot_id, snapshot.taken_on, pages)

        index = PassageIndex(snapshot.snapshot_id, snapshot.taken_on)
        seen = set()
        for p in sorted(snapshot.pages, key=lambda p: is_variant(p.url)):
            path = canonical_path(p.url)
            if path in seen:
                continue
            seen.add(path)
            index.add_page(p.url, p.title, p.markdown, classify(p.url, p.markdown, p.fetched_at, previous))
        index.finish()
        self.indexes[snapshot.snapshot_id] = index

    def answer(self, question: str, as_of=None) -> Answer:
        d = self.answerer.answer(question, as_of)
        return Answer(
            text=d.text,
            sources=[Source(url=u, snapshot_id=s, quote=q) for u, s, q in d.sources],
            confident=d.confident,
            conflict_note=d.conflict_note,
        )

    def explain(self, question: str, as_of=None) -> dict:
        """Why an answer came out the way it did: the parsed question, the snapshot read,
        the route taken, and for rate answers every tier with its winning page. Not part
        of the contract; used by scripts/ask.py and when debugging."""
        self.answerer.answer(question, as_of)
        return dict(self.answerer.trace)

    def conflicts(self) -> list:
        return [
            Conflict(
                topic=c.topic,
                values=c.values,
                sources=[Source(url=u, snapshot_id=s, quote=q) for u, s, q in c.sources],
                resolution=c.resolution,
                reason=c.reason,
            )
            for c in self.kb.conflicts()
        ]


def build() -> TemporalKnowledgeSystem:
    return TemporalKnowledgeSystem()
