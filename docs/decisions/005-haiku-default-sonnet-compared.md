# 005: Haiku 5.5 is the default; Sonnet 5.5 measured, not adopted

- **Status:** accepted
- **Date:** 2026-11-08

## Context
Hard budget for the whole project: $5. The task is reading and quoting, not reasoning.

## Decision
`claude-haiku-5-5` with thinking disabled and effort `low` for extraction and Q&A.

## Evidence (same 40 CUAD test contracts, final pipeline)
| model | micro-F1 | macro-F1 | $/contract | p50 latency |
|---|---|---|---|---|
| Haiku 5.5 | 0.901 | 0.875 | $0.00149 | 3.6 s |
| Sonnet 5.5 | 0.902 | 0.880 | $0.0292 | 5.6 s |

Paired bootstrap Δ micro-F1 +0.001, 95% CI [−0.024, +0.025]. Sonnet costs 19.6x more per
contract for no measurable gain on this task. N=40 is small; the CI is the honest summary.

## Consequences
- Revisit if: categories with low precision (uncapped liability, IP assignment) matter
  enough to pay for a stronger model on just those categories.
