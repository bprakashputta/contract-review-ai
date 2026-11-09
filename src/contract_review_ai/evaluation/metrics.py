"""Clause-presence and span-quality metrics against CUAD gold spans."""

from __future__ import annotations

from dataclasses import dataclass, field

Span = tuple[int, int]


def _union_len(spans: list[Span]) -> int:
    total, cur_s, cur_e = 0, None, None
    for s, e in sorted(spans):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def _intersection_len(a: list[Span], b: list[Span]) -> int:
    # intersections of the two unions = |A| + |B| - |A ∪ B|
    return _union_len(a) + _union_len(b) - _union_len(a + b)


def jaccard(pred: list[Span], gold: list[Span]) -> float:
    u = _union_len(pred + gold)
    return _intersection_len(pred, gold) / u if u else 0.0


def overlaps(span: Span, others: list[Span]) -> bool:
    return any(span[0] < e and s < span[1] for s, e in others)


@dataclass
class CategoryCounts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    jaccards: list[float] = field(default_factory=list)
    hits: int = 0  # TP where at least one predicted quote overlaps a gold span
    quotes: int = 0  # predicted (verified) quotes on gold-positive contracts
    quotes_on_gold: int = 0  # ... of which overlap a gold span

    def add(self, pred: list[Span], gold: list[Span], present: bool) -> None:
        g = bool(gold)
        if present and g:
            self.tp += 1
            self.jaccards.append(jaccard(pred, gold))
            self.hits += any(overlaps(p, gold) for p in pred)
            self.quotes += len(pred)
            self.quotes_on_gold += sum(overlaps(p, gold) for p in pred)
        elif present:
            self.fp += 1
        elif g:
            self.fn += 1
        else:
            self.tn += 1

    def merge(self, o: CategoryCounts) -> None:
        self.tp += o.tp
        self.fp += o.fp
        self.fn += o.fn
        self.tn += o.tn
        self.jaccards += o.jaccards
        self.hits += o.hits
        self.quotes += o.quotes
        self.quotes_on_gold += o.quotes_on_gold

    def summary(self) -> dict[str, float | int | None]:
        p = self.tp / (self.tp + self.fp) if self.tp + self.fp else None
        r = self.tp / (self.tp + self.fn) if self.tp + self.fn else None
        f1 = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
        return {
            "precision": p,
            "recall": r,
            "f1": f1,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "support": self.tp + self.fn,
            "span_hit_rate": self.hits / self.tp if self.tp else None,
            "mean_jaccard": sum(self.jaccards) / len(self.jaccards) if self.jaccards else None,
            "quote_precision": self.quotes_on_gold / self.quotes if self.quotes else None,
        }


def micro_counts(rows: list[dict], gold: dict, keys: tuple[str, ...]) -> list[tuple[int, int, int]]:
    """Per-contract (tp, fp, fn) for verified clause presence, for bootstrapping."""
    out = []
    for r in rows:
        tp = fp = fn = 0
        for k in keys:
            p, g = r["categories"][k]["found"], bool(gold[r["id"]].gold[k])
            tp += p and g
            fp += p and not g
            fn += g and not p
        out.append((tp, fp, fn))
    return out


def _f1(c: list[tuple[int, int, int]]) -> float:
    tp, fp, fn = (sum(x[i] for x in c) for i in range(3))
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def paired_bootstrap(
    a: list[tuple[int, int, int]],
    b: list[tuple[int, int, int]],
    iters: int = 5000,
    seed: int = 0,
) -> dict[str, float]:
    """95% CI of micro-F1(b) - micro-F1(a), resampling contracts with replacement."""
    import random

    rng = random.Random(seed)
    n = len(a)
    diffs = []
    for _ in range(iters):
        idx = [rng.randrange(n) for _ in range(n)]
        diffs.append(_f1([b[i] for i in idx]) - _f1([a[i] for i in idx]))
    diffs.sort()
    return {
        "delta": _f1(b) - _f1(a),
        "ci_low": diffs[int(0.025 * iters)],
        "ci_high": diffs[int(0.975 * iters) - 1],
        "p_le_0": sum(d <= 0 for d in diffs) / iters,
    }
