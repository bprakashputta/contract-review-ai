"""Clause extraction: build the prompt, call the model, verify every quote in code."""

from __future__ import annotations

import re
from typing import Any

from .categories import BY_KEY, CATEGORIES, KEYS
from .llm import LLMClient
from .models import CallStats, Clause, Document, Quote, RawExtraction
from .prompts import load_prompt
from .verify import DocumentIndex

_MARKER = re.compile(r"^\s*\[S\d+\]\s*")


def extraction_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "clauses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": list(KEYS)},
                        "found": {"type": "boolean"},
                        "quotes": {"type": "array", "items": {"type": "string"}},
                        "note": {"type": "string"},
                    },
                    "required": ["category", "found", "quotes", "note"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["clauses"],
        "additionalProperties": False,
    }


def render_contract(doc: Document, section_idx: list[int] | None) -> str:
    """Whole text, or the selected sections labelled with ids and gaps marked."""
    if section_idx is None:
        return doc.text
    parts, prev = [], -1
    for i in section_idx:
        if prev >= 0 and i != prev + 1:
            parts.append("[...]")
        s = doc.sections[i]
        parts.append(f"[{s.id}] {doc.section_text(s)}")
        prev = i
    return "\n\n".join(parts)


def build_prompt(
    doc: Document, section_idx: list[int] | None, prompt_name: str = "extract.v1"
) -> tuple[str, str, str]:
    p = load_prompt(prompt_name)
    cats = "\n".join(f"- {c.key} ({c.label}): {c.description}" for c in CATEGORIES)
    guided = "\n".join(
        f"- {c.key} ({c.label}): {c.description}" + (f" Note: {c.guidance}" if c.guidance else "")
        for c in CATEGORIES
    )
    names = ", ".join(c.key for c in CATEGORIES)
    scope = (
        ""
        if section_idx is None
        else (
            f"Only the {len(section_idx)} of {len(doc.sections)} sections most likely to "
            "contain these clauses are shown, labelled [S#]; [...] marks omitted text."
        )
    )
    system = p.system.format(categories=cats, categories_guided=guided, category_names=names)
    user = p.user.format(
        filename=doc.filename,
        contract=render_contract(doc, section_idx),
        scope_note=scope,
    )
    return system, user, p.version


def verify_extraction(
    doc: Document,
    raw: RawExtraction,
    index: DocumentIndex | None = None,
    allow_repair: bool = True,
) -> list[Clause]:
    index = index or DocumentIndex(doc.text)
    by_cat = {c.category: c for c in raw.clauses if c.category in BY_KEY}
    out = []
    for key in KEYS:
        rc = by_cat.get(key)
        cat = BY_KEY[key]
        if rc is None:
            out.append(Clause(category=key, label=cat.label, found=False))
            continue
        ok, dropped, seen = [], [], set()
        for q in rc.quotes[:5] if rc.found else []:
            q_clean = _MARKER.sub("", q).strip()
            if not q_clean:
                continue
            v = index.verify(q_clean, allow_repair=allow_repair)
            if v.ok and (v.start, v.end) not in seen:
                seen.add((v.start, v.end))
                sec = doc.section_at(v.start)
                ok.append(
                    Quote(
                        text=doc.text[v.start : v.end],
                        model_text=q,
                        status=v.status,
                        start=v.start,
                        end=v.end,
                        section_id=sec.id if sec else None,
                    )
                )
            elif not v.ok:
                dropped.append(Quote(text=q_clean, model_text=q, status="unverified"))
        out.append(
            Clause(
                category=key,
                label=cat.label,
                found=bool(ok),
                quotes=ok,
                dropped_quotes=dropped,
                note=rc.note if ok else "",
                model_said_found=rc.found,
            )
        )
    return out


def extract(
    client: LLMClient,
    doc: Document,
    section_idx: list[int] | None,
    model: str,
    prompt_name: str = "extract.v1",
) -> tuple[RawExtraction, CallStats, str]:
    system, user, version = build_prompt(doc, section_idx, prompt_name)
    res = client.complete_json(
        model=model,
        system=system,
        user=user,
        schema=extraction_schema(),
        out_type=RawExtraction,
        max_tokens=6000,
        tag=f"extract:{version}",
    )
    return res.parsed, res.stats, version  # type: ignore[return-value]
