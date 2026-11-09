"""Reproducible eval on CUAD v1.

    uv run python -m contract_review_ai.evaluation.run --config pipeline --split test --n 102
    uv run python -m contract_review_ai.evaluation.run --report      # rebuild RESULTS.md

Configurations:
  naive     whole contract, minimal prompt (category names only), extract_naive.v1
  full      whole contract, extract.v1 (definitions + verbatim-quote rules)
  pipeline  sectioned + BM25/keyword prefilter for long contracts, extract.v1

Every configuration is scored twice: on the model's raw output (a category counts as
present when the model says found) and after code-side quote verification (present only
if at least one quote is found in the contract). Identical calls are served from the LLM
cache, and cost is always computed from the recorded token usage so cached re-runs still
report the true cost per contract."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..categories import BY_KEY, KEYS
from ..config import get_settings
from ..llm import build_client, price_call
from ..logging_setup import setup_logging
from ..parsing import from_text
from ..pipeline import Reviewer
from ..playbook import load_playbook
from .dataset import load, sample
from .metrics import CategoryCounts, micro_counts, paired_bootstrap

RESULTS = Path("evals/results")
CONFIGS = {
    "naive": {"prompt": "extract_naive.v1", "mode": "full"},
    "full": {"prompt": "extract.v2", "mode": "full"},
    "pipeline": {"prompt": "extract.v2", "mode": "auto"},
    "pipeline_v1": {"prompt": "extract.v1", "mode": "auto"},
    # ablation: same cached model outputs, verifier limited to exact normalized matches
    "pipeline_exact_only": {
        "prompt": "extract.v2",
        "mode": "auto",
        "allow_repair": False,
    },
}


def run_one(reviewer: Reviewer, contract, mode: str) -> dict:
    doc = from_text(contract.id, contract.text)
    t0 = time.perf_counter()
    report = reviewer.review(doc, mode=mode)  # type: ignore[arg-type]
    wall = time.perf_counter() - t0
    cats = {}
    n_quotes = n_exact = n_repaired = n_unverified = 0
    for c in report.clauses:
        # raw scoring trusts the model's found flag; only quotes that could be located in
        # the contract have a span (an unlocatable quote contributes no overlap)
        n_unverified += len(c.dropped_quotes)
        n_quotes += len(c.quotes) + len(c.dropped_quotes)
        n_exact += sum(q.status == "exact" for q in c.quotes)
        n_repaired += sum(q.status == "repaired" for q in c.quotes)
        cats[c.category] = {
            "model_found": c.model_said_found,
            "found": c.found,
            "spans": [(q.start, q.end) for q in c.quotes],
            "statuses": [q.status for q in c.quotes],
            "dropped": [q.model_text for q in c.dropped_quotes],
        }
    st = report.calls[0]
    cost = price_call(st.model, st.input_tokens, st.output_tokens)
    return {
        "id": contract.id,
        "mode": report.mode,
        "chars": len(contract.text),
        "sections_sent": report.sections_sent,
        "sections_total": report.sections_total,
        "input_tokens": st.input_tokens,
        "output_tokens": st.output_tokens,
        "cost_usd": cost,
        "latency_s": st.latency_ms / 1000,
        "wall_s": wall,
        "quotes": n_quotes,
        "quotes_exact": n_exact,
        "quotes_repaired": n_repaired,
        "quotes_unverified": n_unverified,
        "risks": [r.rule_id for r in report.risks],
        "categories": cats,
    }


def score(rows: list[dict], contracts: dict) -> dict:
    out = {}
    for variant in ("raw", "verified"):
        per = {k: CategoryCounts() for k in KEYS}
        for r in rows:
            gold = contracts[r["id"]].gold
            for k in KEYS:
                c = r["categories"][k]
                present = c["model_found"] if variant == "raw" else c["found"]
                spans = [tuple(s) for s in c["spans"]]
                per[k].add(spans, [(g.start, g.end) for g in gold[k]], present)
        total = CategoryCounts()
        for v in per.values():
            total.merge(v)
        macro_f1 = [v.summary()["f1"] for v in per.values() if v.summary()["f1"] is not None]
        out[variant] = {
            "overall": {**total.summary(), "macro_f1": sum(macro_f1) / len(macro_f1)},
            "per_category": {k: v.summary() for k, v in per.items()},
        }
    q = sum(r["quotes"] for r in rows)
    out["quotes"] = {
        "returned": q,
        "exact": sum(r["quotes_exact"] for r in rows),
        "repaired": sum(r["quotes_repaired"] for r in rows),
        "unverified": sum(r["quotes_unverified"] for r in rows),
        "unverified_rate": sum(r["quotes_unverified"] for r in rows) / q if q else 0.0,
    }
    # what verification changed at the clause level
    fixed_fp = caused_fn = 0
    for r in rows:
        gold = contracts[r["id"]].gold
        for k in KEYS:
            c = r["categories"][k]
            if c["model_found"] and not c["found"]:
                if gold[k]:
                    caused_fn += 1
                else:
                    fixed_fp += 1
    out["verification_flips"] = {
        "false_positive_removed": fixed_fp,
        "true_positive_lost": caused_fn,
    }
    costs = [r["cost_usd"] for r in rows]
    lat = [r["latency_s"] for r in rows]
    out["cost"] = {
        "total_usd": sum(costs),
        "per_contract_mean_usd": statistics.mean(costs),
        "per_contract_max_usd": max(costs),
        "input_tokens_mean": statistics.mean(r["input_tokens"] for r in rows),
        "output_tokens_mean": statistics.mean(r["output_tokens"] for r in rows),
    }
    out["latency"] = {
        "mean_s": statistics.mean(lat),
        "p50_s": statistics.median(lat),
        "p95_s": sorted(lat)[max(0, int(round(0.95 * len(lat))) - 1)],
    }
    out["prefiltered_contracts"] = sum(r["mode"] == "prefilter" for r in rows)
    return out


def run(config: str, split: str, n: int | None, model: str, workers: int) -> Path:
    settings = get_settings()
    setup_logging("WARNING")
    client = build_client(settings)
    cfg = CONFIGS[config]
    reviewer = Reviewer(
        client,
        load_playbook(settings.playbook_path),
        model=model,
        prefilter_min_tokens=settings.prefilter_min_tokens,
        prefilter_k=settings.prefilter_k,
        prompt_name=cfg["prompt"],
        allow_repair=cfg.get("allow_repair", True),
    )
    contracts = sample(load(split), n)
    print(
        f"{config} on {split} n={len(contracts)} model={model}; spent so far "
        f"${client.ledger.total:.4f}"
    )
    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(lambda c: run_one(reviewer, c, cfg["mode"]), contracts))
    summary = score(rows, {c.id: c for c in contracts})
    tag = f"{config}_{model.removeprefix('claude-')}_{split}_n{len(contracts)}"
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{tag}.json"
    out.write_text(
        json.dumps(
            {
                "config": config,
                "model": model,
                "split": split,
                "n": len(contracts),
                "prompt": cfg["prompt"],
                "mode": cfg["mode"],
                "prefilter": {
                    "k": settings.prefilter_k,
                    "min_tokens": settings.prefilter_min_tokens,
                },
                "summary": summary,
                "contracts": rows,
            },
            indent=1,
        )
    )
    o = summary["verified"]["overall"]
    print(
        f"-> {out}  micro-F1 {o['f1']:.3f}  macro-F1 {o['macro_f1']:.3f}  "
        f"cost ${summary['cost']['total_usd']:.4f}  ledger ${client.ledger.total:.4f}"
    )
    return out


def _f(x: float | None, d: int = 3) -> str:
    return "–" if x is None else f"{x:.{d}f}"


def build_report() -> Path:
    files = sorted(f for f in RESULTS.glob("*_n*.json") if not f.name.startswith("qa_"))
    runs = [json.loads(f.read_text()) for f in files]
    lines = ["# Eval results (generated by `make report`; do not edit)", ""]
    lines += [
        "| config | model | split | N | variant | micro P | micro R | micro F1 | macro F1 "
        "| span hit | mean Jaccard | unverified quotes | $/contract | p50 latency |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        s = r["summary"]
        for variant in ("raw", "verified"):
            o = s[variant]["overall"]
            lines.append(
                f"| {r['config']} | {r['model'].removeprefix('claude-')} | {r['split']} | {r['n']} "
                f"| {variant} | {_f(o['precision'])} | {_f(o['recall'])} | {_f(o['f1'])} "
                f"| {_f(o['macro_f1'])} | {_f(o['span_hit_rate'])} | {_f(o['mean_jaccard'])} "
                f"| {s['quotes']['unverified']}/{s['quotes']['returned']} "
                f"| {s['cost']['per_contract_mean_usd']:.5f} | {s['latency']['p50_s']:.1f}s |"
            )
    by_tag = {(r["config"], r["model"], r["split"], r["n"]): r for r in runs}
    pairs = [
        (
            ("naive", "claude-haiku-5-5", "test", 102),
            ("pipeline", "claude-haiku-5-5", "test", 102),
        ),
        (
            ("full", "claude-haiku-5-5", "test", 102),
            ("pipeline", "claude-haiku-5-5", "test", 102),
        ),
        (
            ("pipeline", "claude-haiku-5-5", "test", 40),
            ("pipeline", "claude-sonnet-5-5", "test", 40),
        ),
    ]
    rows_cmp = []
    test_gold = None
    for a_key, b_key in pairs:
        if a_key in by_tag and b_key in by_tag:
            test_gold = test_gold or {c.id: c for c in load("test")}
            ra, rb = by_tag[a_key], by_tag[b_key]
            ids = sorted({x["id"] for x in ra["contracts"]} & {x["id"] for x in rb["contracts"]})

            def pick(run: dict, ids: list[str] = ids) -> list[dict]:
                return sorted(
                    (x for x in run["contracts"] if x["id"] in ids), key=lambda x: x["id"]
                )

            bs = paired_bootstrap(
                micro_counts(pick(ra), test_gold, KEYS),
                micro_counts(pick(rb), test_gold, KEYS),
            )
            rows_cmp.append(
                f"| {b_key[0]} · {b_key[1].removeprefix('claude-')} vs {a_key[0]} · "
                f"{a_key[1].removeprefix('claude-')} | {len(ids)} | {bs['delta']:+.3f} "
                f"| [{bs['ci_low']:+.3f}, {bs['ci_high']:+.3f}] | {bs['p_le_0']:.3f} |"
            )
    if rows_cmp:
        lines += [
            "",
            "## Paired bootstrap: micro-F1 difference (verified), 5,000 resamples of contracts",
            "",
            "| comparison | N | Δ micro-F1 | 95% CI | P(Δ ≤ 0) |",
            "|---|---|---|---|---|",
            *rows_cmp,
        ]
    for r in runs:
        main = (r["n"] == 102 and r["config"] in {"naive", "full", "pipeline"}) or (
            "sonnet" in r["model"]
        )
        if r["split"] != "test" or not main:
            continue
        s = r["summary"]["verified"]["per_category"]
        lines += [
            "",
            f"## {r['config']} · {r['model'].removeprefix('claude-')} · "
            f"{r['split']} N={r['n']} (verified)",
            "",
            "| category | support | P | R | F1 | span hit | Jaccard |",
            "|---|---|---|---|---|---|---|",
        ]
        for k in KEYS:
            c = s[k]
            lines.append(
                f"| {BY_KEY[k].label} | {c['support']} | {_f(c['precision'])} | "
                f"{_f(c['recall'])} | {_f(c['f1'])} | {_f(c['span_hit_rate'])} | "
                f"{_f(c['mean_jaccard'])} |"
            )
    for f in sorted(RESULTS.glob("qa_*.json")):
        q = json.loads(f.read_text())
        s = q["summary"]
        lines += [
            "",
            f"## Cited Q&A · {q['model'].removeprefix('claude-')} · "
            f"retrieval: {q.get('retrieval', 'bm25')} · {q.get('split', 'test')}, "
            f"{s['contracts']} contracts, {s['questions']} questions",
            "",
            "| metric | value |",
            "|---|---|",
            f"| answerable accuracy (answers iff clause exists) | {s['answerable_accuracy']:.3f} |",
            f"| clause present → answered | {s['present_answered_rate']:.3f} |",
            f"| clause absent → abstained | {s['absent_abstained_rate']:.3f} |",
            f"| clause present → a verified citation overlaps gold | {s['present_citation_hit_rate']:.3f} |",
            f"| citations unverified / returned | {s['citations_unverified']}/{s['citations_returned']} |",
            f"| cost total / per question | ${s['cost_total_usd']:.4f} / ${s['cost_total_usd'] / s['questions']:.5f} |",
            f"| p50 latency | {s['latency_p50_s']:.1f}s |",
        ]
    out = RESULTS / "RESULTS.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=list(CONFIGS), default="pipeline")
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        build_report()
        return
    run(a.config, a.split, a.n, a.model, a.workers)
    build_report()


if __name__ == "__main__":
    main()
