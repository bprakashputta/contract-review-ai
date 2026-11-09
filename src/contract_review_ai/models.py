"""Pydantic models shared by the pipeline, API and eval."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Section(BaseModel):
    id: str
    start: int
    end: int
    heading: str = ""


class Document(BaseModel):
    filename: str
    text: str
    sections: list[Section]

    def section_text(self, s: Section) -> str:
        return self.text[s.start : s.end]

    def section_at(self, offset: int) -> Section | None:
        for s in self.sections:
            if s.start <= offset < s.end:
                return s
        return None


# ---- what the model returns (validated against a JSON schema) ----


class RawClause(BaseModel):
    category: str
    found: bool
    quotes: list[str] = Field(default_factory=list)
    note: str = ""


class RawExtraction(BaseModel):
    clauses: list[RawClause]


class RawCitation(BaseModel):
    section_id: str
    quote: str


class RawAnswer(BaseModel):
    answerable: bool
    answer: str
    citations: list[RawCitation] = Field(default_factory=list)


# ---- what the service reports (after code-side verification) ----

QuoteStatus = Literal["exact", "repaired", "unverified"]


class Quote(BaseModel):
    text: str  # always the document's own text when status != unverified
    model_text: str  # what the model returned, kept for audit
    status: QuoteStatus
    start: int = -1
    end: int = -1
    section_id: str | None = None


class Clause(BaseModel):
    category: str
    label: str
    found: bool  # true only if at least one quote verified
    quotes: list[Quote] = Field(default_factory=list)  # verified quotes only
    dropped_quotes: list[Quote] = Field(default_factory=list)  # failed verification
    note: str = ""
    model_said_found: bool = False


Severity = Literal["high", "medium", "low", "info"]


class RiskFlag(BaseModel):
    rule_id: str
    severity: Severity
    category: str
    message: str
    evidence: list[Quote] = Field(default_factory=list)


class CallStats(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cached: bool = False


class Report(BaseModel):
    filename: str
    mode: Literal["full", "prefilter"]
    clauses: list[Clause]
    risks: list[RiskFlag]
    sections_sent: int
    sections_total: int
    doc_tokens_est: int
    sent_tokens_est: int
    calls: list[CallStats] = Field(default_factory=list)
    prompt_version: str = ""

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)


class Answer(BaseModel):
    question: str
    answerable: bool
    answer: str
    citations: list[Quote]
    dropped_citations: list[Quote] = Field(default_factory=list)
    supported: bool  # answerable and at least one verified citation
    calls: list[CallStats] = Field(default_factory=list)
