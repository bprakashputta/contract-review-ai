"""Code-side verification that every quote the model returns exists in the document.

Three outcomes per quote:
- ``exact``: the normalized quote occurs in the normalized document; the span is mapped
  back to original offsets.
- ``repaired``: no exact match, but either (a) the quote is the document's text with
  short interruptions removed (a page footer, header or page number in the middle of a
  sentence: common in PDF-extracted contracts), found as up to 4 in-order fragments with
  small gaps, or (b) a near-identical passage exists (fuzzy alignment score >= threshold,
  e.g. the model dropped a comma). The quote is *replaced* by the document's own text over
  the matched span, so what the user sees is always verbatim.
- ``unverified``: nothing close enough. The quote is dropped from the report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

from .text import normalize, normalize_with_map

Status = Literal["exact", "repaired", "unverified"]
_ELLIPSIS = re.compile(r"\s*(?:\.\s*\.\s*\.|…|\[\s*\.\.\.\s*\])\s*")


@dataclass(frozen=True)
class Verification:
    status: Status
    start: int = -1
    end: int = -1
    score: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status != "unverified"


class DocumentIndex:
    """Normalized view of a document, built once and reused for every quote."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.norm, self.offsets = normalize_with_map(text)

    def _to_original(self, nstart: int, nend: int) -> tuple[int, int]:
        return self.offsets[nstart], self.offsets[nend - 1] + 1

    def _find_exact(self, nquote: str, from_pos: int = 0) -> tuple[int, int] | None:
        i = self.norm.find(nquote, from_pos)
        if i < 0:
            return None
        return i, i + len(nquote)

    def _longest_prefix(self, nq: str, lo: int, hi: int) -> tuple[int, int]:
        """Longest prefix of nq occurring in norm[lo:hi]; returns (position, length)."""
        best = (-1, 0)
        a, b = 1, len(nq)
        while a <= b:  # prefix occurrence is monotone in length -> binary search
            m = (a + b) // 2
            i = self.norm.find(nq[:m], lo, hi)
            if i >= 0:
                best = (i, m)
                a = m + 1
            else:
                b = m - 1
        return best

    def _find_stitched(
        self, nq: str, max_gap: int = 400, max_parts: int = 4, min_part: int = 20
    ) -> tuple[int, int] | None:
        pos, length = self._longest_prefix(nq, 0, len(self.norm))
        if length < min_part:
            return None
        start, end, rest, parts = pos, pos + length, nq[length:].lstrip(), 1
        while rest:
            if parts >= max_parts:
                return None
            pos, length = self._longest_prefix(rest, end, min(len(self.norm), end + max_gap))
            if length == 0 or (length < min_part and length < len(rest)):
                return None
            end, rest, parts = pos + length, rest[length:].lstrip(), parts + 1
        return start, end

    def _find_fuzzy(self, nquote: str, threshold: float) -> tuple[int, int, float] | None:
        if len(nquote) < 25:
            return None  # short strings fuzzy-match too easily
        res = fuzz.partial_ratio_alignment(nquote, self.norm, score_cutoff=threshold)
        if res is None:
            return None
        start, end = res.dest_start, res.dest_end
        # partial_ratio aligns to a window of len(quote); reject big length mismatches
        if (end - start) < 0.85 * len(nquote):
            return None
        return start, end, res.score

    def verify(
        self, quote: str, fuzzy_threshold: float = 92.0, allow_repair: bool = True
    ) -> Verification:
        parts = [p for p in _ELLIPSIS.split(quote) if normalize(p)]
        if not parts:
            return Verification("unverified")
        if len(parts) == 1:
            nq = normalize(parts[0])
            hit = self._find_exact(nq)
            if hit:
                return Verification("exact", *self._to_original(*hit), score=100.0)
            if not allow_repair:
                return Verification("unverified")
            st = self._find_stitched(nq)
            if st:
                return Verification("repaired", *self._to_original(*st), score=99.0)
            fz = self._find_fuzzy(nq, fuzzy_threshold)
            if fz:
                return Verification("repaired", *self._to_original(fz[0], fz[1]), score=fz[2])
            return Verification("unverified")
        # elided quote ("A ... B"): if every fragment appears in order within a short
        # window, the span covers them all; otherwise keep the longest fragment that verifies
        pos, first, last, ok = 0, None, None, True
        for p in parts:
            hit = self._find_exact(normalize(p), pos)
            if hit is None or (first is not None and hit[0] - pos > 3000):
                ok = False
                break
            first = hit[0] if first is None else first
            last, pos = hit[1], hit[1]
        if ok:
            return Verification("exact", *self._to_original(first, last), score=100.0)
        if not allow_repair:
            return Verification("unverified")
        for p in sorted(parts, key=len, reverse=True):
            if len(normalize(p)) >= 40:
                v = self.verify(p, fuzzy_threshold)
                if v.ok:
                    return Verification("repaired", v.start, v.end, score=v.score)
        return Verification("unverified")
