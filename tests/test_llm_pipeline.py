import pytest

from contract_review_ai.categories import KEYS
from contract_review_ai.llm import BudgetExceeded, price_call
from contract_review_ai.models import RawExtraction
from contract_review_ai.parsing import from_text
from contract_review_ai.pipeline import Reviewer
from contract_review_ai.playbook import load_playbook
from contract_review_ai.qa import QA
from tests.conftest import CONTRACT, fake_response

PLAYBOOK = load_playbook(__import__("pathlib").Path("playbooks/default.yaml"))


def extraction_payload(**quotes):
    return {
        "clauses": [
            {"category": k, "found": k in quotes, "quotes": quotes.get(k, []), "note": ""}
            for k in KEYS
        ]
    }


def test_price_call_uses_long_context_tier_for_haiku():
    assert price_call("claude-haiku-5-5", 50_000, 0) == pytest.approx(0.005)
    assert price_call("claude-haiku-5-5", 150_000, 0) == pytest.approx(0.075)
    assert price_call("claude-sonnet-5-5", 1000, 1000) == pytest.approx(0.012)


def test_budget_cap_refuses_before_calling(make_client):
    client, transport = make_client(lambda kw: fake_response({"clauses": []}), cap=0.0001)
    with pytest.raises(BudgetExceeded):
        client.complete_json(
            model="claude-haiku-5-5",
            system="s",
            user="u" * 10_000,
            schema={},
            out_type=RawExtraction,
            max_tokens=4000,
        )
    assert transport.calls == []


def test_ledger_records_and_cache_serves_identical_calls(make_client, tmp_path):
    client, transport = make_client(lambda kw: fake_response({"clauses": []}, 2000, 100))
    kw = dict(
        model="claude-haiku-5-5",
        system="s",
        user="u",
        schema={"type": "object"},
        out_type=RawExtraction,
    )
    first = client.complete_json(**kw)
    second = client.complete_json(**kw)
    assert len(transport.calls) == 1
    assert first.stats.cost_usd == pytest.approx(price_call("claude-haiku-5-5", 2000, 100))
    assert second.stats.cached and second.stats.cost_usd == 0
    assert client.ledger.total == pytest.approx(first.stats.cost_usd)
    assert (tmp_path / "spend.jsonl").read_text().count("\n") == 1


def test_request_shape_structured_output_and_thinking(make_client):
    client, transport = make_client(lambda kw: fake_response({"clauses": []}))
    client.complete_json(
        model="claude-haiku-5-5",
        system="s",
        user="u",
        schema={"type": "object"},
        out_type=RawExtraction,
    )
    req = transport.calls[0]
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["thinking"] == {"type": "disabled"}
    client.complete_json(
        model="claude-sonnet-5-5",
        system="s",
        user="u",
        schema={"type": "object"},
        out_type=RawExtraction,
    )
    assert transport.calls[1]["thinking"] == {"type": "between_tools"}


def test_truncated_output_raises(make_client):
    client, _ = make_client(lambda kw: fake_response('{"clauses": [', stop="max_tokens"))
    with pytest.raises(RuntimeError, match="truncated"):
        client.complete_json(
            model="claude-haiku-5-5", system="s", user="u", schema={}, out_type=RawExtraction
        )


def test_review_keeps_verified_quotes_and_drops_fabricated(make_client):
    payload = extraction_payload(
        governing_law=["This Agreement shall be governed by the laws of the State of Ohio."],
        exclusivity=["Supplier shall be the exclusive provider in North America."],  # invented
        cap_on_liability=[
            "[S3] In no event shall either party's aggregate liability exceed the fees paid"
        ],
        renewal_term=["it shall automatically renew for successive one (1) year terms"],
        notice_to_terminate_renewal=[
            "unless either party gives written notice of non-renewal at least fifteen (15) days prior to the end of the then-current term"
        ],
    )
    client, _ = make_client(lambda kw: fake_response(payload))
    reviewer = Reviewer(client, PLAYBOOK)
    report = reviewer.review(from_text("msa.txt", CONTRACT))
    by = {c.category: c for c in report.clauses}
    assert by["governing_law"].found and by["governing_law"].quotes[0].status == "exact"
    assert by["cap_on_liability"].found  # section marker stripped before verification
    assert not by["exclusivity"].found and by["exclusivity"].model_said_found
    assert by["exclusivity"].dropped_quotes[0].status == "unverified"
    rules = {f.rule_id for f in report.risks}
    assert "exclusivity" not in rules  # a dropped quote can never raise a flag
    assert "auto-renewal-notice" in rules  # 15 days < 30, parsed by code
    assert "governing-law-allowed" in rules  # Ohio not in the allowed list
    assert report.mode == "full"  # short document skips the prefilter
    q = by["governing_law"].quotes[0]
    assert CONTRACT[q.start : q.end] == q.text


def test_long_document_is_prefiltered(make_client):
    filler = "\n\n".join(f"Schedule item {i}. The vendor ships widgets weekly." for i in range(400))
    doc = from_text("long.txt", filler + "\n\n" + CONTRACT)
    client, transport = make_client(lambda kw: fake_response(extraction_payload()))
    report = Reviewer(client, PLAYBOOK, prefilter_min_tokens=3000).review(doc)
    assert report.mode == "prefilter"
    assert report.sections_sent < report.sections_total
    sent = transport.calls[0]["messages"][0]["content"]
    assert "governed by the laws of the State of Ohio" in sent
    assert "[S" in sent


def test_qa_drops_unverified_citations(make_client):
    payload = {
        "answerable": True,
        "answer": "Either party may terminate on 90 days' notice.",
        "citations": [
            {
                "section_id": "S2",
                "quote": "Either party may terminate this Agreement for convenience upon ninety (90) days' prior written notice",
            },
            {"section_id": "S9", "quote": "Termination fees are waived."},
        ],
    }
    client, _ = make_client(lambda kw: fake_response(payload))
    ans = QA(client).ask(from_text("msa.txt", CONTRACT), "Can we terminate without cause?")
    assert ans.supported
    assert len(ans.citations) == 1 and len(ans.dropped_citations) == 1


def test_qa_answerable_without_verified_citation_is_unsupported(make_client):
    payload = {
        "answerable": True,
        "answer": "Yes.",
        "citations": [{"section_id": "S1", "quote": "made up text here that is long enough"}],
    }
    client, _ = make_client(lambda kw: fake_response(payload))
    ans = QA(client).ask(from_text("msa.txt", CONTRACT), "Is there a non-compete?")
    assert ans.answerable and not ans.supported
