# 003: Lexical (BM25 + keyword) prefilter, no embeddings

- **Status:** accepted
- **Date:** 2026-11-08

## Context
Whole-contract requests on CUAD average ~18K input tokens and the longest exceed 100K, where Haiku 5.5's
price per token is 5x higher. Most of a contract is irrelevant to 12 clause categories.

## Decision
Split the document into sections with character offsets (`parsing.py`). For each category,
score sections with BM25 over the category's description and keywords plus a keyword-hit
bonus; keep the top `k=3`, plus up to 6 sections that hit the category's anchor keyword.
Send the union, labelled `[S#]`, with `[...]` marking gaps. Documents estimated under 3,000
tokens skip the prefilter (nothing to save, only recall to lose).

`k` and the keyword lists were tuned on the CUAD **train** split only, with a free script
(`scripts/prefilter_recall.py`, results in `evals/results/prefilter_sweep_dev.json`):

| k | gold spans inside sent sections | contracts where every present clause is reachable* | tokens sent / full |
|---|---|---|---|
| 2 | 0.909 | 0.984 | 0.411 |
| **3** | **0.921** | **0.986** | **0.451** |
| 4 | 0.935 | 0.989 | 0.487 |
| 8 | 0.964 | 0.995 | 0.601 |

\* per contract-category with gold spans: at least one gold span is in the sent sections.

## Evidence (CUAD test, 102 contracts)
81 of 102 contracts were prefiltered. Mean input tokens 10,060 vs 17,881 sending the whole
contract (−44%); cost per contract $0.00151 vs $0.00272; worst contract $0.0041 vs $0.0538.
Micro-F1 0.893 vs 0.889 (paired bootstrap Δ +0.005, 95% CI [−0.013, +0.022]): no
measurable quality loss.

## Alternatives considered
- **Embeddings (Voyage) + vector search:** an extra paid call per section and a vector
  store, for a corpus where clause language is formulaic and lexical recall is already 0.986.
- **Map-reduce over chunks:** many calls per document; more cost and latency than one call.

## Consequences
- Revisit if: contracts in a new domain use unusual wording (check recall with the script).
