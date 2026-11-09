# 001: Code verifies every quote; risk is decided by rules, not the model

- **Status:** accepted
- **Date:** 2026-11-08

## Context
A contract reviewer acts on the quote, not on the model's summary. A quote that is
paraphrased, stitched from two places or "completed" past where the document stops is worse
than no quote, because it looks authoritative. The model also should not decide what counts
as risky: that is a policy decision the legal/ops team owns.

## Decision
- The model returns quotes only (structured output). `verify.py` checks each one against the
  document after normalization (case, whitespace, typographic quotes/dashes, `-----`
  separator lines) and maps the match back to original character offsets.
- Outcomes: `exact`; `repaired` (the quote is the document's text with a page footer/header
  removed, matched as up to 4 in-order fragments with gaps <= 400 chars, or a fuzzy
  alignment >= 92); `unverified` (dropped). A repaired quote is **replaced by the document's
  own text**, so the UI only ever shows verbatim text.
- A clause counts as found only if at least one quote verifies. Playbook rules run on
  verified clauses only, and numeric facts (notice days, jurisdiction) are parsed by code
  from the verified quote text.

## Evidence (CUAD test, 102 contracts, Haiku 5.5)
- 2 of 599 quotes failed verification. Examples: the model finished a sentence the
  extracted text truncates ("which consent shall" → "...shall not be unreasonably
  withheld"), and joined list items 7.1 and 7.1.3 into one quote.
- An exact-only verifier (ablation, same cached outputs) dropped 13 of 599 quotes: it lost 6
  true-positive clauses and removed 3 false positives, micro-F1 0.888 vs 0.893. Most of the
  11 extra drops were real clause text broken by PDF page footers. On the 20-contract dev
  set before the repair step, strict matching lost 4 true positives.

## Alternatives considered
- **Trust the model's quotes:** fabricated text reaches the user.
- **Second LLM call to check quotes:** costs tokens and is still probabilistic.
- **Native citations API:** returns spans, but is incompatible with JSON-schema structured
  output in the same request, and we need both.

## Consequences
- Gain: every highlighted span exists in the document; risk flags are reproducible.
- Give up: a real clause whose only quote is badly paraphrased is dropped (recall cost).
- Revisit if: a model starts returning many near-miss quotes (repaired rate rises).
