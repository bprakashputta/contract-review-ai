# 002: LangGraph for orchestration, the Anthropic SDK for the model calls

- **Status:** accepted
- **Date:** 2026-11-08

## Context
The owner's preference is to reach models through LangChain interfaces where an agent
graph is involved. This service has a fixed pipeline (parse → route → prefilter → extract →
verify → playbook → report) and makes exactly one structured-extraction call per document
(plus one per question).

## Decision
- The pipeline and Q&A are LangGraph `StateGraph`s (`pipeline.py`, `qa.py`): typed state,
  a conditional edge that routes short documents past the prefilter, nodes that are plain
  functions and individually testable.
- The model call itself goes through one wrapper (`llm.py`) over the official `anthropic`
  SDK, using `output_config.format` JSON-schema structured output.

## Why not `ChatAnthropic.with_structured_output`
- It implements structured output through forced tool use. Forced `tool_choice` returns a
  400 on Claude Sonnet 5.5 (the comparison model), so the Sonnet arm would need a different
  code path.
- The wrapper needs exact per-call `usage` (input, output, cache read/write tokens) for the
  spend ledger and per-call cost logging, a pre-call budget check, a request-level cache key
  over the exact request body, and model-specific thinking settings (`disabled` on Haiku
  5.5, `between_tools` on Sonnet 5.5). All of these are one function with the SDK.

## Consequences
- Gain: one code path for both models; cost accounting is exact; tests swap the transport
  for a fake (`tests/conftest.py`) and run offline.
- Give up: no LangChain callbacks/tracing; adding another provider means another transport.
