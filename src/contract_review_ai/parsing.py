"""Turn an uploaded PDF / DOCX / TXT into plain text plus sections with character offsets.

Sections are the unit of retrieval and citation: every section is a [start, end) slice of
``Document.text`` so a quote verified inside the text can always be attributed to one."""

from __future__ import annotations

import io
import re
from pathlib import PurePath

from .models import Document, Section

MAX_SECTION_CHARS = 1800
MIN_SECTION_CHARS = 250
_BLANK = re.compile(r"\n[ \t\f\v]*\n+")
_SENTENCE_END = re.compile(r"(?<=[.;:])\s+(?=[A-Z(\"])")


class UnsupportedFileType(ValueError):
    pass


def extract_text(filename: str, data: bytes) -> str:
    suffix = PurePath(filename).suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
        return "\n\n".join(p for p in pages if p)
    if suffix == ".docx":
        import docx

        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n\n".join(p for p in parts if p.strip())
    if suffix in {".txt", ".md", ""}:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1")
    raise UnsupportedFileType(f"unsupported file type {suffix!r}; use PDF, DOCX or TXT")


def _blocks(text: str) -> list[tuple[int, int]]:
    """Paragraph blocks (split on blank lines; on single newlines if the text has none)."""
    splitter = _BLANK if _BLANK.search(text) else re.compile(r"\n+")
    spans, pos = [], 0
    for m in splitter.finditer(text):
        spans.append((pos, m.start()))
        pos = m.end()
    spans.append((pos, len(text)))
    out = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            out.append((s, e))
    return out


def _split_long(text: str, s: int, e: int) -> list[tuple[int, int]]:
    if e - s <= MAX_SECTION_CHARS:
        return [(s, e)]
    cuts = [s] + [s + m.start() for m in _SENTENCE_END.finditer(text[s:e])] + [e]
    out, cur = [], s
    for i in range(1, len(cuts)):
        if cuts[i] - cur > MAX_SECTION_CHARS and cuts[i - 1] > cur:
            out.append((cur, cuts[i - 1]))
            cur = cuts[i - 1]
    out.append((cur, e))
    # a sentence longer than the max is hard-wrapped at whitespace
    final = []
    for a, b in out:
        while b - a > MAX_SECTION_CHARS * 1.5:
            cut = text.rfind(" ", a, a + MAX_SECTION_CHARS)
            cut = cut if cut > a else a + MAX_SECTION_CHARS
            final.append((a, cut))
            a = cut
        final.append((a, b))
    return [(a + (len(text[a:b]) - len(text[a:b].lstrip())), b) for a, b in final]


def sectionize(text: str) -> list[Section]:
    pieces: list[tuple[int, int]] = []
    for s, e in _blocks(text):
        pieces.extend(_split_long(text, s, e))
    merged: list[list[int]] = []
    for s, e in pieces:
        if merged and (
            merged[-1][1] - merged[-1][0] < MIN_SECTION_CHARS
            and e - merged[-1][0] <= MAX_SECTION_CHARS
        ):
            merged[-1][1] = e  # glue short headings / fragments onto the next block
        else:
            merged.append([s, e])
    sections = []
    for i, (s, e) in enumerate(merged, start=1):
        first_line = text[s:e].strip().split("\n", 1)[0]
        sections.append(Section(id=f"S{i}", start=s, end=e, heading=first_line[:90]))
    return sections


def parse_document(filename: str, data: bytes) -> Document:
    text = extract_text(filename, data).replace("\r\n", "\n").replace("\r", "\n")
    return Document(filename=filename, text=text, sections=sectionize(text))


def from_text(filename: str, text: str) -> Document:
    return Document(filename=filename, text=text, sections=sectionize(text))
