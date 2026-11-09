"""LangGraph review pipeline.

    parse ──► route ──(long doc)──► prefilter ──► extract ──► verify ──► playbook ──► report
                 └────(short doc / mode=full)─────────┘

Every node is a plain function over ``ReviewState``; the graph is compiled once per
``Reviewer``. The model is called once per document (in ``extract``)."""

from __future__ import annotations

import logging
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from .extract import extract, verify_extraction
from .llm import LLMClient
from .logging_setup import log
from .models import CallStats, Clause, Document, RawExtraction, Report, RiskFlag
from .parsing import parse_document
from .playbook import Playbook, evaluate
from .prefilter import select_sections
from .text import approx_tokens

logger = logging.getLogger("contract_review_ai.pipeline")
Mode = Literal["auto", "full", "prefilter"]


class ReviewState(TypedDict, total=False):
    filename: str
    data: bytes
    doc: Document
    mode: Mode
    section_idx: list[int] | None
    raw: RawExtraction
    calls: list[CallStats]
    prompt_version: str
    clauses: list[Clause]
    risks: list[RiskFlag]
    report: Report


class Reviewer:
    def __init__(
        self,
        client: LLMClient,
        playbook: Playbook,
        model: str = "claude-haiku-5-5",
        prefilter_min_tokens: int = 3000,
        prefilter_k: int = 3,
        prompt_name: str = "extract.v2",
        allow_repair: bool = True,
    ) -> None:
        self.allow_repair = allow_repair
        self.client = client
        self.playbook = playbook
        self.model = model
        self.min_tokens = prefilter_min_tokens
        self.k = prefilter_k
        self.prompt_name = prompt_name
        self.graph = self._build()

    # ---- nodes ----
    def _parse(self, s: ReviewState) -> ReviewState:
        if "doc" in s:
            return {}
        return {"doc": parse_document(s["filename"], s["data"])}

    def _route(self, s: ReviewState) -> Literal["prefilter", "extract"]:
        mode = s.get("mode", "auto")
        if mode == "full":
            return "extract"
        if mode == "prefilter":
            return "prefilter"
        return "prefilter" if approx_tokens(s["doc"].text) >= self.min_tokens else "extract"

    def _prefilter(self, s: ReviewState) -> ReviewState:
        sel = select_sections(s["doc"], k=self.k)
        return {"section_idx": sel.union}

    def _extract(self, s: ReviewState) -> ReviewState:
        raw, stats, version = extract(
            self.client, s["doc"], s.get("section_idx"), self.model, self.prompt_name
        )
        return {
            "raw": raw,
            "calls": [*s.get("calls", []), stats],
            "prompt_version": version,
        }

    def _verify(self, s: ReviewState) -> ReviewState:
        return {"clauses": verify_extraction(s["doc"], s["raw"], allow_repair=self.allow_repair)}

    def _playbook(self, s: ReviewState) -> ReviewState:
        return {"risks": evaluate(self.playbook, s["clauses"])}

    def _report(self, s: ReviewState) -> ReviewState:
        doc = s["doc"]
        idx = s.get("section_idx")
        sent = doc.text if idx is None else "".join(doc.section_text(doc.sections[i]) for i in idx)
        report = Report(
            filename=doc.filename,
            mode="full" if idx is None else "prefilter",
            clauses=s["clauses"],
            risks=s["risks"],
            sections_sent=len(doc.sections) if idx is None else len(idx),
            sections_total=len(doc.sections),
            doc_tokens_est=approx_tokens(doc.text),
            sent_tokens_est=approx_tokens(sent),
            calls=s.get("calls", []),
            prompt_version=s.get("prompt_version", ""),
        )
        dropped = sum(len(c.dropped_quotes) for c in report.clauses)
        log(
            logger,
            "review_done",
            filename=doc.filename,
            mode=report.mode,
            found=sum(c.found for c in report.clauses),
            dropped_quotes=dropped,
            risks=len(report.risks),
            cost_usd=round(report.cost_usd, 6),
        )
        return {"report": report}

    def _build(self):  # noqa: ANN202 - compiled graph type is internal to langgraph
        g = StateGraph(ReviewState)
        g.add_node("parse", self._parse)
        g.add_node("prefilter", self._prefilter)
        g.add_node("extract", self._extract)
        g.add_node("verify", self._verify)
        g.add_node("playbook", self._playbook)
        g.add_node("report", self._report)
        g.add_edge(START, "parse")
        g.add_conditional_edges("parse", self._route, ["prefilter", "extract"])
        g.add_edge("prefilter", "extract")
        g.add_edge("extract", "verify")
        g.add_edge("verify", "playbook")
        g.add_edge("playbook", "report")
        g.add_edge("report", END)
        return g.compile()

    def review(self, doc: Document, mode: Mode = "auto") -> Report:
        out = self.graph.invoke({"doc": doc, "mode": mode, "calls": []})
        return out["report"]

    def review_file(self, filename: str, data: bytes, mode: Mode = "auto") -> Report:
        out = self.graph.invoke({"filename": filename, "data": data, "mode": mode, "calls": []})
        return out["report"]
