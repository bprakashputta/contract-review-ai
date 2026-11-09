"""The one place the service talks to Claude.

Every call goes through ``LLMClient.complete_json``, which:
1. renders a cache key from the full request and returns a cached response if present
   (identical calls are free: re-running an eval or re-reviewing a document costs $0);
2. checks the spend ledger and refuses if the worst-case cost of the call would cross
   the hard cap;
3. calls the Messages API with a JSON-schema structured output, a timeout and retries;
4. validates the JSON against a pydantic model, records tokens / cost / latency in the
   ledger and logs one structured line per call.

The raw Anthropic SDK is used instead of a LangChain chat model; see
docs/decisions/002-raw-sdk-inside-langgraph.md."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from .logging_setup import log
from .models import CallStats

logger = logging.getLogger("contract_review_ai.llm")

# USD per million tokens (Anthropic first-party pricing, from the claude-api skill,
# cached 2026-10-06). Haiku 5.5 prices double-tier above 100K prompt tokens.
PRICES: dict[str, dict[str, float]] = {
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "in_long": 0.50, "out_long": 2.50},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "in_long": 2.00, "out_long": 10.00},
}
LONG_CONTEXT_TOKENS = 100_000
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10


class BudgetExceeded(RuntimeError):
    pass


def price_call(model: str, inp: int, out: int, cache_read: int = 0, cache_write: int = 0) -> float:
    p = PRICES[model]
    long = (inp + cache_read + cache_write) > LONG_CONTEXT_TOKENS
    pin, pout = (p["in_long"], p["out_long"]) if long else (p["in"], p["out"])
    return (
        inp * pin + cache_write * pin * CACHE_WRITE_MULT + cache_read * pin * CACHE_READ_MULT
    ) / 1e6 + out * pout / 1e6


class SpendLedger:
    """Append-only JSONL of every billed call. The running total is the source of truth
    for the hard cap; it survives restarts because it is re-read from disk."""

    def __init__(self, path: Path, cap_usd: float) -> None:
        self.path = path
        self.cap = cap_usd
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._total = 0.0
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    self._total += json.loads(line)["cost_usd"]

    @property
    def total(self) -> float:
        return self._total

    def check(self, worst_case_usd: float) -> None:
        with self._lock:
            if self._total + worst_case_usd > self.cap:
                raise BudgetExceeded(
                    f"spend cap ${self.cap:.2f} would be exceeded "
                    f"(spent ${self._total:.4f}, next call up to ${worst_case_usd:.4f})"
                )

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._total += entry["cost_usd"]
            with self.path.open("a") as f:
                f.write(json.dumps(entry) + "\n")


class ResponseCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT)")
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT v FROM cache WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?)", (key, json.dumps(value)))
            self._db.commit()


class Transport(Protocol):
    """What we need from the SDK; tests swap in a fake."""

    def create(self, **kwargs: Any) -> Any: ...


class AnthropicTransport:
    def __init__(self, api_key: str, timeout_s: float, max_retries: int) -> None:
        import anthropic

        self._client = anthropic.Anthropic(
            api_key=api_key or None, timeout=timeout_s, max_retries=max_retries
        )

    def create(self, **kwargs: Any) -> Any:
        return self._client.messages.create(**kwargs)


def _thinking_params(model: str) -> dict[str, Any]:
    # Extraction is a reading task; thinking tokens would be billed as output for little
    # gain. Haiku 5.5 accepts thinking disabled at effort <= high; Sonnet 5.5 rejects
    # "disabled" and uses "between_tools" (thinking off) instead.
    if model.startswith("claude-sonnet-5-5"):
        return {
            "thinking": {"type": "between_tools"},
            "output_config": {"effort": "low"},
        }
    return {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}


@dataclass
class LLMResult:
    parsed: BaseModel
    stats: CallStats
    raw_text: str


class LLMClient:
    def __init__(
        self,
        transport: Transport,
        ledger: SpendLedger,
        cache: ResponseCache | None = None,
    ) -> None:
        self.transport = transport
        self.ledger = ledger
        self.cache = cache

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        out_type: type[BaseModel],
        max_tokens: int = 4096,
        tag: str = "",
    ) -> LLMResult:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system}],
            "messages": [{"role": "user", "content": user}],
            **_thinking_params(model),
        }
        params["output_config"] = {
            **params["output_config"],
            "format": {"type": "json_schema", "schema": schema},
        }
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        if self.cache and (hit := self.cache.get(key)):
            stats = CallStats(**{**hit["stats"], "cached": True, "cost_usd": 0.0})
            log(logger, "llm_call", tag=tag, model=model, cached=True)
            return LLMResult(out_type.model_validate_json(hit["text"]), stats, hit["text"])

        est_in = (len(system) + len(user)) // 3  # conservative chars->tokens
        self.ledger.check(price_call(model, est_in, max_tokens))
        t0 = time.perf_counter()
        resp = self.transport.create(**params)
        latency = (time.perf_counter() - t0) * 1000
        u = resp.usage
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        cost = price_call(model, u.input_tokens, u.output_tokens, cr, cw)
        stats = CallStats(
            model=model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=cr,
            cache_write_tokens=cw,
            cost_usd=cost,
            latency_ms=round(latency, 1),
        )
        self.ledger.record({"ts": time.time(), "tag": tag, **stats.model_dump()})
        log(
            logger,
            "llm_call",
            tag=tag,
            **stats.model_dump(),
            stop_reason=resp.stop_reason,
            spent_total=round(self.ledger.total, 4),
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("model refused the request")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(f"output truncated at max_tokens={max_tokens}")
        parsed = out_type.model_validate_json(text)
        if self.cache:
            self.cache.put(key, {"text": text, "stats": stats.model_dump()})
        return LLMResult(parsed, stats, text)


def build_client(settings: Any) -> LLMClient:
    transport = AnthropicTransport(
        settings.anthropic_api_key, settings.llm_timeout_s, settings.llm_max_retries
    )
    ledger = SpendLedger(settings.data_dir / "spend.jsonl", settings.spend_cap_usd)
    cache = ResponseCache(settings.data_dir / "llm_cache.sqlite")
    return LLMClient(transport, ledger, cache)
