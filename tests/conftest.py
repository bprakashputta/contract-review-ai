from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from contract_review_ai.llm import LLMClient, ResponseCache, SpendLedger

CONTRACT = """MASTER SERVICES AGREEMENT

1. Term. This Agreement commences on the Effective Date and continues for two (2) years. \
Thereafter it shall automatically renew for successive one (1) year terms unless either \
party gives written notice of non-renewal at least fifteen (15) days prior to the end of \
the then-current term.

2. Termination. Either party may terminate this Agreement for convenience upon ninety (90) \
days' prior written notice to the other party.

3. Limitation of Liability. In no event shall either party's aggregate liability exceed the \
fees paid in the twelve (12) months preceding the claim.

-------------------------------------------------------------------- Page 2

4. Assignment. Neither party may assign this Agreement without the prior written consent of \
the other party.

5. Governing Law. This Agreement shall be governed by the laws of the State of Ohio.
"""


def fake_response(payload: dict | str, inp: int = 1000, out: int = 200, stop: str = "end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=inp,
            output_tokens=out,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        stop_reason=stop,
    )


class FakeTransport:
    """Records every request and answers with ``responder(kwargs)``."""

    def __init__(self, responder: Callable[[dict[str, Any]], Any]) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.responder(kwargs)


@pytest.fixture
def make_client(tmp_path: Path):
    def _make(responder, cap: float = 5.0, cache: bool = True) -> tuple[LLMClient, FakeTransport]:
        t = FakeTransport(responder)
        c = LLMClient(
            t,
            SpendLedger(tmp_path / "spend.jsonl", cap),
            ResponseCache(tmp_path / "cache.sqlite") if cache else None,
        )
        return c, t

    return _make
