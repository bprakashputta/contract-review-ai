"""Cited Q&A: retrieve sections with BM25, answer from them, verify every citation."""

from __future__ import annotations

import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .categories import CATEGORIES
from .extract import _MARKER
from .llm import LLMClient
from .models import Answer, CallStats, Document, Quote, RawAnswer
from .prefilter import _STOP, BM25, _query
from .prompts import load_prompt
from .text import tokenize
from .verify import DocumentIndex

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["section_id", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["answerable", "answer", "citations"],
    "additionalProperties": False,
}


class QAState(TypedDict, total=False):
    doc: Document
    question: str
    section_idx: list[int]
    raw: RawAnswer
    stats: CallStats
    answer: Answer


class QA:
    def __init__(
        self,
        client: LLMClient,
        model: str = "claude-haiku-5-5",
        k: int = 6,
        expand: bool = True,
    ) -> None:
        self.client, self.model, self.k, self.expand = client, model, k, expand
        g = StateGraph(QAState)
        g.add_node("retrieve", self._retrieve)
        g.add_node("answer", self._answer)
        g.add_node("verify", self._verify)
        g.add_edge(START, "retrieve")
        g.add_edge("retrieve", "answer")
        g.add_edge("answer", "verify")
        g.add_edge("verify", END)
        self.graph = g.compile()

    def _retrieve(self, s: QAState) -> QAState:
        doc = s["doc"]
        bm = BM25([tokenize(doc.section_text(x)) for x in doc.sections])
        q = [t for t in tokenize(s["question"]) if t not in _STOP]
        if self.expand:
            # query expansion: a question that mentions a clause type ("renewing",
            # "liability") also searches with that category's prefilter vocabulary, so
            # it finds clauses phrased differently from the question
            ql = s["question"].lower()
            for cat in CATEGORIES:
                if any(re.search(p, ql) for p in cat.keywords[:2]):
                    q += _query(cat)
        scores = bm.scores(q)
        top = sorted(range(len(scores)), key=lambda i: -scores[i])[: self.k]
        return {"section_idx": sorted(i for i in top if scores[i] > 0) or top[:2]}

    def _answer(self, s: QAState) -> QAState:
        doc, p = s["doc"], load_prompt("qa.v1")
        excerpts = "\n\n".join(
            f"[{doc.sections[i].id}] {doc.section_text(doc.sections[i])}" for i in s["section_idx"]
        )
        res = self.client.complete_json(
            model=self.model,
            system=p.system,
            user=p.user.format(filename=doc.filename, excerpts=excerpts, question=s["question"]),
            schema=ANSWER_SCHEMA,
            out_type=RawAnswer,
            max_tokens=1500,
            tag=f"qa:{p.version}",
        )
        return {"raw": res.parsed, "stats": res.stats}  # type: ignore[typeddict-item]

    def _verify(self, s: QAState) -> QAState:
        doc, raw = s["doc"], s["raw"]
        index = DocumentIndex(doc.text)
        ok, dropped = [], []
        for c in raw.citations:
            qt = _MARKER.sub("", c.quote).strip()
            v = index.verify(qt)
            if v.ok:
                sec = doc.section_at(v.start)
                ok.append(
                    Quote(
                        text=doc.text[v.start : v.end],
                        model_text=c.quote,
                        status=v.status,
                        start=v.start,
                        end=v.end,
                        section_id=sec.id if sec else c.section_id,
                    )
                )
            else:
                dropped.append(
                    Quote(
                        text=qt,
                        model_text=c.quote,
                        status="unverified",
                        section_id=c.section_id,
                    )
                )
        ans = Answer(
            question=s["question"],
            answerable=raw.answerable,
            answer=raw.answer,
            citations=ok,
            dropped_citations=dropped,
            supported=raw.answerable and bool(ok),
            calls=[s["stats"]],
        )
        return {"answer": ans}

    def ask(self, doc: Document, question: str) -> Answer:
        return self.graph.invoke({"doc": doc, "question": question})["answer"]
