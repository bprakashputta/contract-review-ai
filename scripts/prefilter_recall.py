"""Measure prefilter recall (no LLM calls, free): for each gold clause span, is it inside a
section the prefilter would send? Run on the dev (train) split only when tuning.

    uv run python scripts/prefilter_recall.py --split dev --k 4
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from contract_review_ai.categories import KEYS
from contract_review_ai.evaluation.dataset import load
from contract_review_ai.parsing import from_text
from contract_review_ai.prefilter import select_sections
from contract_review_ai.text import approx_tokens


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--max-keyword", type=int, default=6)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    contracts = load(a.split)
    hit = defaultdict(int)
    tot = defaultdict(int)
    hit_any = defaultdict(int)  # gold span inside the union of sections sent
    full_tok = sent_tok = 0
    c_hit = c_tot = 0  # contract-level: at least one gold span of the category reachable
    for c in contracts:
        doc = from_text(c.id, c.text)
        sel = select_sections(doc, k=a.k, max_keyword=a.max_keyword)
        full_tok += approx_tokens(c.text)
        sent_tok += sum(approx_tokens(doc.section_text(doc.sections[i])) for i in sel.union)
        union = [doc.sections[i] for i in sel.union]
        for key in KEYS:
            mine = [doc.sections[i] for i in sel.by_category[key]]
            gs = c.gold.get(key, [])
            if gs:
                c_tot += 1
                c_hit += any(s.start < g.end and g.start < s.end for g in gs for s in union)
            for g in gs:
                tot[key] += 1
                if any(s.start < g.end and g.start < s.end for s in mine):
                    hit[key] += 1
                if any(s.start < g.end and g.start < s.end for s in union):
                    hit_any[key] += 1
    rows = {
        k: (hit[k] / tot[k] if tot[k] else None, hit_any[k] / tot[k] if tot[k] else None, tot[k])
        for k in KEYS
    }
    allh, alla, allt = sum(hit.values()), sum(hit_any.values()), sum(tot.values())
    if a.json:
        print(
            json.dumps(
                {
                    "k": a.k,
                    "span_recall_union": alla / allt,
                    "contract_reachability": c_hit / c_tot,
                    "token_ratio": sent_tok / full_tok,
                    "per_category": rows,
                }
            )
        )
        return
    print(f"{'category':32} own-k  union  n")
    for k, (r, u, n) in rows.items():
        print(f"{k:32} {r:.3f}  {u:.3f}  {n}")
    print(f"{'ALL':32} {allh / allt:.3f}  {alla / allt:.3f}  {allt}")
    print(f"contract-level clause reachability (union) = {c_hit / c_tot:.3f}  n={c_tot}")
    print(f"tokens sent / full = {sent_tok / full_tok:.3f}  ({sent_tok:,} / {full_tok:,})")


if __name__ == "__main__":
    main()
