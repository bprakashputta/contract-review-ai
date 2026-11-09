from fastapi.testclient import TestClient

from contract_review_ai.api import create_app
from contract_review_ai.config import Settings
from contract_review_ai.evaluation.metrics import CategoryCounts, jaccard, paired_bootstrap
from contract_review_ai.store import Store
from tests.conftest import CONTRACT, fake_response
from tests.test_llm_pipeline import extraction_payload


def _app(make_client, tmp_path, cap=5.0):
    def responder(kw):
        if "Question:" in kw["messages"][0]["content"]:
            return fake_response(
                {
                    "answerable": True,
                    "answer": "Ohio law.",
                    "citations": [
                        {
                            "section_id": "S5",
                            "quote": "This Agreement shall be governed by the laws of the State of Ohio.",
                        }
                    ],
                }
            )
        return fake_response(
            extraction_payload(
                governing_law=["This Agreement shall be governed by the laws of the State of Ohio."]
            )
        )

    client, _ = make_client(responder, cap=cap)
    settings = Settings(anthropic_api_key="test", db_path=tmp_path / "t.db", data_dir=tmp_path)
    return TestClient(create_app(settings, client=client, store=Store(tmp_path / "t.db")))


def test_upload_review_ask_roundtrip(make_client, tmp_path):
    with _app(make_client, tmp_path) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        assert c.get("/readyz").json()["ready"] is True
        r = c.post("/api/documents", files={"file": ("msa.txt", CONTRACT.encode(), "text/plain")})
        assert r.status_code == 201
        doc_id = r.json()["id"]
        rep = c.post(f"/api/documents/{doc_id}/review").json()
        gl = next(x for x in rep["clauses"] if x["category"] == "governing_law")
        assert gl["found"] and gl["quotes"][0]["status"] == "exact"
        ans = c.post(f"/api/documents/{doc_id}/ask", json={"question": "Which law governs?"}).json()
        assert ans["supported"] and ans["citations"][0]["section_id"]
        full = c.get(f"/api/documents/{doc_id}").json()
        assert full["review"] and len(full["answers"]) == 1
        assert c.get("/api/documents").json()[0]["id"] == doc_id


def test_errors(make_client, tmp_path):
    with _app(make_client, tmp_path, cap=0.0) as c:
        assert c.post("/api/documents", files={"file": ("x.xlsx", b"x" * 500)}).status_code == 415
        assert c.post("/api/documents", files={"file": ("x.txt", b"tiny")}).status_code == 422
        assert c.get("/api/documents/nope").status_code == 404
        doc_id = c.post("/api/documents", files={"file": ("m.txt", CONTRACT.encode())}).json()["id"]
        r = c.post(f"/api/documents/{doc_id}/review")
        assert r.status_code == 429 and "spend cap" in r.json()["detail"]


def test_span_metrics():
    assert jaccard([(0, 10)], [(5, 15)]) == 5 / 15
    assert jaccard([(0, 10), (5, 12)], [(0, 12)]) == 1.0
    c = CategoryCounts()
    c.add([(0, 5)], [(3, 9)], True)  # TP with overlap
    c.add([], [(0, 3)], False)  # FN
    c.add([(0, 1)], [], True)  # FP
    s = c.summary()
    assert (s["tp"], s["fp"], s["fn"]) == (1, 1, 1)
    assert s["span_hit_rate"] == 1.0


def test_paired_bootstrap_detects_a_clear_improvement():
    a = [(1, 1, 1)] * 50
    b = [(2, 0, 0)] * 50
    bs = paired_bootstrap(a, b, iters=500)
    assert bs["delta"] > 0 and bs["ci_low"] > 0
