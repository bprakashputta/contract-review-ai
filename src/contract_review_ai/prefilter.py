"""Cheap lexical prefilter: pick the sections worth sending to the model.

For each category we score every section with BM25 over the category's description and
keyword regexes, keep the top ``k`` plus any section with a strong keyword hit (capped),
then send the union. No embeddings: CUAD clause language is formulaic ("shall be governed
by the laws of"), lexical recall on the train split was already high, and it costs nothing
at query time (see docs/decisions/003-lexical-prefilter.md)."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .categories import CATEGORIES, Category
from .models import Document
from .text import tokenize

_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "and",
        "or",
        "in",
        "on",
        "for",
        "by",
        "with",
        "as",
        "is",
        "be",
        "are",
        "was",
        "any",
        "this",
        "that",
        "such",
        "its",
        "it",
        "party",
        "parties",
        "shall",
        "may",
        "will",
        "which",
        "from",
        "at",
        "other",
        "e",
        "g",
    ]
)


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(docs)) if docs else 0.0
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, ln in zip(self.tf, self.len, strict=True):
            s = 0.0
            norm = self.k1 * (1 - self.b + self.b * ln / (self.avg or 1))
            for q in query:
                f = tf.get(q)
                if f:
                    s += self.idf[q] * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out


@dataclass
class Selection:
    by_category: dict[str, list[int]]  # category key -> section indexes
    union: list[int]  # sorted section indexes to send


def _query(cat: Category) -> list[str]:
    words = tokenize(cat.description + " " + cat.label + " " + cat.cuad_name)
    words += [w for k in cat.keywords for w in tokenize(re.sub(r"[^a-z ]", " ", k))]
    return [w for w in words if w not in _STOP]


def select_sections(doc: Document, k: int = 3, max_keyword: int = 6) -> Selection:
    texts = [doc.section_text(s).lower() for s in doc.sections]
    bm25 = BM25([tokenize(t) for t in texts])
    by_cat: dict[str, list[int]] = {}
    for cat in CATEGORIES:
        scores = bm25.scores(_query(cat))
        pats = [re.compile(p) for p in cat.keywords]
        hits = [sum(1 for p in pats if p.search(t)) for t in texts]
        # keyword hits boost the lexical score; the strongest hits are kept regardless
        combined = [s + 1.5 * h for s, h in zip(scores, hits, strict=True)]
        ranked = sorted(range(len(texts)), key=lambda i: -combined[i])
        chosen = [i for i in ranked[:k] if combined[i] > 0]
        # the first keyword is the category's anchor term; a hit on it, or on two others,
        # makes a section a candidate even if BM25 ranks it below k
        anchor = pats[0]
        strong = [
            i for i in ranked if (hits[i] >= 2 or anchor.search(texts[i])) and i not in chosen
        ][:max_keyword]
        by_cat[cat.key] = sorted(set(chosen + strong))
    union = sorted({i for v in by_cat.values() for i in v})
    return Selection(by_category=by_cat, union=union)
