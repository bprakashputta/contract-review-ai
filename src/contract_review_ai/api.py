"""FastAPI service: upload, review, ask; serves the single-page UI."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .categories import CATEGORIES
from .config import Settings, get_settings
from .llm import BudgetExceeded, LLMClient, build_client
from .logging_setup import log, setup_logging
from .parsing import UnsupportedFileType, parse_document
from .pipeline import Reviewer
from .playbook import Playbook, load_playbook
from .qa import QA
from .store import Store

logger = logging.getLogger("contract_review_ai.api")
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=500)


def create_app(
    settings: Settings | None = None,
    client: LLMClient | None = None,
    store: Store | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        app.state.client = client or build_client(settings)
        app.state.store = store or Store(settings.db_path)
        app.state.playbook = load_playbook(settings.playbook_path)
        app.state.reviewer = Reviewer(
            app.state.client,
            app.state.playbook,
            model=settings.model,
            prefilter_min_tokens=settings.prefilter_min_tokens,
            prefilter_k=settings.prefilter_k,
            prompt_name="extract.v2",
        )
        app.state.qa = QA(app.state.client, model=settings.model)
        log(
            logger,
            "startup",
            model=settings.model,
            spend_cap=settings.spend_cap_usd,
            spent=round(app.state.client.ledger.total, 4),
        )
        yield

    app = FastAPI(title="contract-review-ai", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def access_log(request: Request, call_next):  # noqa: ANN001, ANN202
        t0 = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/api") or request.url.path.startswith("/ready"):
            log(
                logger,
                "http",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                ms=round((time.perf_counter() - t0) * 1000, 1),
            )
        return response

    @app.exception_handler(BudgetExceeded)
    async def budget_handler(_: Request, exc: BudgetExceeded) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request) -> JSONResponse:
        st = request.app.state
        checks = {
            "db": st.store.ping(),
            "api_key": bool(settings.anthropic_api_key),
            "budget_left": st.client.ledger.total < st.client.ledger.cap,
        }
        ok = all(checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={"ready": ok, **checks})

    @app.get("/api/meta")
    def meta(request: Request) -> dict:
        st = request.app.state
        pb: Playbook = st.playbook
        return {
            "model": settings.model,
            "categories": [{"key": c.key, "label": c.label} for c in CATEGORIES],
            "playbook": pb.model_dump(),
            "spend": {
                "total_usd": round(st.client.ledger.total, 4),
                "cap_usd": st.client.ledger.cap,
            },
        }

    @app.get("/api/documents")
    def list_documents(request: Request) -> list[dict]:
        return request.app.state.store.list_documents()

    @app.post("/api/documents", status_code=201)
    async def upload(request: Request, file: UploadFile = File(...)) -> dict:
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "file larger than 15 MB")
        try:
            doc = parse_document(file.filename or "upload.txt", data)
        except UnsupportedFileType as e:
            raise HTTPException(415, str(e)) from e
        except Exception as e:  # malformed PDF/DOCX
            raise HTTPException(422, f"could not read the file: {e}") from e
        if len(doc.text.strip()) < 200:
            raise HTTPException(422, "no extractable text (scanned PDFs need OCR first)")
        doc_id = request.app.state.store.add_document(doc)
        return {
            "id": doc_id,
            "filename": doc.filename,
            "chars": len(doc.text),
            "sections": len(doc.sections),
        }

    def _doc(request: Request, doc_id: str):  # noqa: ANN202
        doc = request.app.state.store.get_document(doc_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        return doc

    @app.get("/api/documents/{doc_id}")
    def get_document(request: Request, doc_id: str) -> dict:
        st = request.app.state
        doc = _doc(request, doc_id)
        review = st.store.latest_review(doc_id)
        return {
            "id": doc_id,
            "document": doc.model_dump(),
            "review": review.model_dump() if review else None,
            "answers": [a.model_dump() for a in st.store.answers(doc_id)],
        }

    @app.post("/api/documents/{doc_id}/review")
    def review(
        request: Request,
        doc_id: str,
        mode: Literal["auto", "full", "prefilter"] = "auto",
    ) -> dict:
        st = request.app.state
        doc = _doc(request, doc_id)
        report = st.reviewer.review(doc, mode=mode)
        st.store.add_review(doc_id, report)
        return {**report.model_dump(), "cost_usd": report.cost_usd}

    @app.post("/api/documents/{doc_id}/ask")
    def ask(request: Request, doc_id: str, body: AskBody) -> dict:
        st = request.app.state
        doc = _doc(request, doc_id)
        answer = st.qa.ask(doc, body.question)
        st.store.add_answer(doc_id, answer)
        return answer.model_dump()

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        "contract_review_ai.api:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        access_log=False,
    )
