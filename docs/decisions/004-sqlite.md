# 004: SQLite for documents, reviews and the LLM cache

- **Status:** accepted
- **Date:** 2026-11-08

## Context
The service is single-tenant: a small team uploads contracts, runs reviews and asks
questions. Writes are one row per upload, review or question.

## Decision
SQLite (WAL mode) in `data/contracts.db` for documents, reviews and Q&A history; a second
SQLite file for the LLM response cache; an append-only JSONL spend ledger. All live in the
`data/` volume, so `docker compose up` is a single container.

## Alternatives considered
- **Postgres:** a second container and migrations for a workload of a few writes a minute.
  No search or vector features are needed (see 003).

## Consequences
- Gain: one container, zero ops, trivially backed up (copy `data/`).
- Give up: one writer process; no multi-instance deployment.
- Revisit if: multiple API replicas or multi-tenant access control are needed; the `Store`
  class is the only module that would change.
