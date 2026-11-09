"""Text normalization that keeps a map back to original character offsets.

Quote verification compares a model's quote against the document after both are
normalized (case, whitespace, typographic quotes and dashes). The offset map lets a
match in normalized space be reported as an exact span of the original text.
"""

from __future__ import annotations

import re
import unicodedata

_TRANSLATE = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "′": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "″": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
        " ": " ",
        " ": " ",
        " ": " ",
        "­": None,  # soft hyphen
        "​": None,  # zero-width space
        "‌": None,
        "‍": None,
        "﻿": None,
    }
)

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def _norm_char(ch: str) -> str:
    ch = ch.translate(_TRANSLATE)
    if not ch:
        return ""
    if len(ch) == 1 and ord(ch) < 128:
        return ch.lower()
    return unicodedata.normalize("NFKC", ch).lower()


_RULE = re.compile(r"[-_=]{3,}")


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Return (normalized text, offsets) where offsets[i] is the original index of
    normalized character i. Whitespace runs collapse to one space; separator lines such as
    "-----" (page-break artifacts in PDF-extracted text) count as whitespace."""
    out: list[str] = []
    offsets: list[int] = []
    prev_space = True  # strips leading whitespace
    rule_chars = {i for m in _RULE.finditer(text) for i in range(m.start(), m.end())}
    for i, raw in enumerate(text):
        ch = " " if i in rule_chars else _norm_char(raw)
        for c in ch:
            if c.isspace():
                if prev_space:
                    continue
                out.append(" ")
                offsets.append(i)
                prev_space = True
            else:
                out.append(c)
                offsets.append(i)
                prev_space = False
    if out and out[-1] == " ":
        out.pop()
        offsets.pop()
    return "".join(out), offsets


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens for lexical retrieval."""
    return _WORD.findall(text.lower())


def approx_tokens(text: str) -> int:
    """Cheap token estimate (≈4 chars/token for English legal text). Used only for
    routing and budget pre-checks; real counts come from the API's usage field."""
    return max(1, len(text) // 4)
