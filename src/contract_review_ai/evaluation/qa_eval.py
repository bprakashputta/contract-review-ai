"""Cited Q&A eval on CUAD test contracts.

Each CUAD category becomes a plain-English question. For every sampled contract we ask
two questions whose clause is present (gold spans exist) and two whose clause is absent,
so abstention is measured as well as answering.

    uv run python -m contract_review_ai.evaluation.qa_eval --n 30

Metrics: answerable accuracy (did the model answer exactly when the clause exists),
citation verification rate, and citation hit rate (a verified citation overlaps the gold
span) on present-clause questions."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from concurrent.futures import ThreadPoolExecutor

from ..config import get_settings
from ..llm import build_client, price_call
from ..logging_setup import setup_logging
from ..parsing import from_text
from ..qa import QA
from .dataset import load, sample
from .metrics import overlaps
from .run import RESULTS

QUESTIONS = {
    "governing_law": "Which jurisdiction's law governs this agreement?",
    "renewal_term": "Does this agreement renew automatically, and for how long?",
    "notice_to_terminate_renewal": "How much notice is needed to stop the agreement renewing?",
    "termination_for_convenience": "Can either party terminate without cause, just by giving notice?",
    "cap_on_liability": "Is there a limit on either party's liability for damages?",
    "uncapped_liability": "Are there any claims for which liability is unlimited?",
    "non_compete": "Is either party restricted from competing with the other?",
    "exclusivity": "Does the agreement create any exclusive dealing obligations or rights?",
    "ip_ownership_assignment": "Does intellectual property created by one party become owned by the other?",
    "change_of_control": "What happens if a party undergoes a change of control or is acquired?",
    "anti_assignment": "Can a party assign this agreement without the other party's consent?",
    "audit_rights": "Does either party have the right to audit the other's books or records?",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--split", choices=["dev", "test"], default="test")
    ap.add_argument("--no-expand", action="store_true", help="retrieval without query expansion")
    a = ap.parse_args()
    setup_logging("WARNING")
    client = build_client(get_settings())
    qa = QA(client, model=a.model, expand=not a.no_expand)
    rng = random.Random(a.seed)
    items = []
    for c in sample(load(a.split), a.n, seed=a.seed):
        present = sorted(k for k in QUESTIONS if c.gold[k])
        absent = sorted(k for k in QUESTIONS if not c.gold[k])
        for k in rng.sample(present, min(2, len(present))) + rng.sample(
            absent, min(2, len(absent))
        ):
            items.append((c, k))

    def one(item):
        c, k = item
        doc = from_text(c.id, c.text)
        ans = qa.ask(doc, QUESTIONS[k])
        gold = [(g.start, g.end) for g in c.gold[k]]
        st = ans.calls[0]
        return {
            "id": c.id,
            "category": k,
            "gold_present": bool(gold),
            "answerable": ans.answerable,
            "supported": ans.supported,
            "citations": len(ans.citations),
            "dropped": len(ans.dropped_citations),
            "dropped_text": [q.model_text for q in ans.dropped_citations],
            "hit": any(overlaps((q.start, q.end), gold) for q in ans.citations),
            "cost_usd": price_call(st.model, st.input_tokens, st.output_tokens),
            "latency_s": st.latency_ms / 1000,
            "answer": ans.answer,
        }

    with ThreadPoolExecutor(6) as ex:
        rows = list(ex.map(one, items))
    pres = [r for r in rows if r["gold_present"]]
    absn = [r for r in rows if not r["gold_present"]]
    cits = sum(r["citations"] + r["dropped"] for r in rows)
    summary = {
        "questions": len(rows),
        "contracts": a.n,
        "answerable_accuracy": sum(r["answerable"] == r["gold_present"] for r in rows) / len(rows),
        "present_answered_rate": sum(r["answerable"] for r in pres) / len(pres),
        "absent_abstained_rate": sum(not r["answerable"] for r in absn) / len(absn),
        "present_citation_hit_rate": sum(r["hit"] for r in pres) / len(pres),
        "citations_returned": cits,
        "citations_unverified": sum(r["dropped"] for r in rows),
        "answerable_but_unsupported": sum(r["answerable"] and not r["supported"] for r in rows),
        "cost_total_usd": sum(r["cost_usd"] for r in rows),
        "latency_p50_s": statistics.median(r["latency_s"] for r in rows),
    }
    variant = "bm25" if a.no_expand else "bm25+expansion"
    tag = f"qa_{'bm25' if a.no_expand else 'expand'}_{a.model.removeprefix('claude-')}"
    out = RESULTS / f"{tag}_{a.split}_n{a.n}.json"
    payload = {"model": a.model, "split": a.split, "retrieval": variant, "summary": summary}
    out.write_text(json.dumps({**payload, "rows": rows}, indent=1))
    print(json.dumps(summary, indent=1))
    print(f"-> {out}  ledger ${client.ledger.total:.4f}")


if __name__ == "__main__":
    main()
