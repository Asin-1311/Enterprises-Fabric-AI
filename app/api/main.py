"""FastAPI backend (CLAUDE.md section 9). All routes are under /api/v1.

Run from the project root:
    .venv\\Scripts\\python.exe -m uvicorn app.api.main:app --port 8000
Interactive docs: http://127.0.0.1:8000/docs

One process holds the models, the single local Qdrant client, the assistant and (by
default) the folder watcher. They share one BM25 index and one lock, so a re-index is
visible to the next search and two requests never use Qdrant at the same time.

Streaming (POST /query, Server-Sent Events):
    event: progress   {"step": "retrieving", ...}   live, one per agent step
    event: token      {"text": "..."}                the VERIFIED answer, in pieces
    event: final      FinalPayload                   status, confidence, citations
    event: error      {"message": "..."}
Only verified text is streamed: verification can remove sentences from the draft, so
the draft itself is never sent (author's choice, M6).

Sign-in (app/api/auth.py, added 2026-11-14 at the author's request): every endpoint
except /health and /auth/login|register|forgot-password|reset-password needs
`Authorization: Bearer <token>`, and the user always comes from that token, never from
the request body. This is email + password sign-in run by this API, not OIDC/JWT single
sign-on (still out of scope). Access rules on top of that: an answer can be read only by
its asker or a partner, traces and the user list are partner-only, and re-indexing is
partner-only.
"""

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import timezone
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query as Q, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sse_starlette.sse import EventSourceResponse

from app.agents.graph import AnswerResult, AuditAssistant
from app.api.auth import current_user, seed_demo_passwords, user_out
from app.api.auth import router as auth_router
from app.api.schemas import (
    CitationOut,
    FeedbackRequest,
    FinalPayload,
    HistoryItem,
    QueryDetail,
    QueryRequest,
    TraceRow,
    UserOut,
)
from app.config import PROJECT_ROOT, Settings, get_settings
from app.db.models import (
    Answer,
    Chunk,
    Citation,
    Document,
    Feedback,
    Query,
    Trace,
    User,
)
from app.db.session import write_audit_log
from app.ingest.indexer import Indexer, ManifestError
from app.retrieval.dense import QdrantInUseError
from app.security.masking import mask_text

PREVIEW_CHARS = 700


@dataclass
class Services:
    """Long-lived objects shared by all requests."""

    settings: Settings
    assistant: AuditAssistant
    indexer: Indexer | None
    lock: threading.RLock
    stop: Callable[[], None] = lambda: None

    @property
    def sessions(self):
        return self.assistant.deps.sessions


def build_services(settings: Settings) -> Services:
    """Real services: models, indexes, LLM client, and the folder watcher."""
    from app.agents.graph import build_assistant
    from app.ingest.indexer import build_indexer
    from app.ingest.watcher import DebouncedCorpusHandler, start_observer

    assistant = build_assistant(settings)
    searcher = assistant.deps.searcher
    indexer = build_indexer(settings, embedder=searcher.embedder,
                            qdrant_client=searcher.store.client, bm25=searcher.bm25)
    lock = threading.RLock()
    indexer.lock = lock  # watcher, re-index and queries take turns
    stop: Callable[[], None] = lambda: None
    if settings.api_watch_corpus:
        handler = DebouncedCorpusHandler(indexer, settings.corpus_dir, settings.manifest_path,
                                         settings.watcher_debounce_seconds)
        observer = start_observer(handler)

        def stop() -> None:
            observer.stop()
            observer.join(timeout=5)
    return Services(settings, assistant, indexer, lock, stop)


# --------------------------------------------------------------------------- helpers

def _services(request: Request) -> Services:
    return request.app.state.services


def _preview(text: str) -> str:
    """Chunk text without its heading-path line, shortened for display."""
    body = text.split("\n", 1)[1] if "\n" in text else text
    body = " ".join(body.split())
    return body if len(body) <= PREVIEW_CHARS else body[:PREVIEW_CHARS].rsplit(" ", 1)[0] + " …"


def _citations(svc: Services, chunk_ids: list[str]) -> list[CitationOut]:
    """Resolve chunk ids to title, section, page and a preview."""
    if not chunk_ids:
        return []
    with svc.sessions() as s:
        superseded = {d for d in s.scalars(select(Document.supersedes)) if d}
        rows = {c.chunk_id: (c, d) for c, d in s.execute(
            select(Chunk, Document).join(Document, Document.doc_id == Chunk.doc_id)
            .where(Chunk.chunk_id.in_(chunk_ids))).all()}
    out = []
    for cid in chunk_ids:
        if cid in rows:
            c, d = rows[cid]
            out.append(CitationOut(
                chunk_id=cid, doc_id=d.doc_id, title=d.title, section=c.section,
                section_title=c.section_title, page_start=c.page_start, page_end=c.page_end,
                superseded=d.doc_id in superseded, preview=_preview(c.text)))
    return out


def _final(svc: Services, r: AnswerResult, seconds: float) -> FinalPayload:
    return FinalPayload(
        query_id=r.query_id, answer_id=r.answer_id, status=r.status, text=r.text,
        confidence=r.confidence, claims_supported=r.claims_supported,
        claims_factual=r.claims_factual, removed_claims=len(r.removed_claims),
        superseded_involved=r.superseded_involved,
        injection_flagged_chunks=len(r.injection_hits),
        citations=_citations(svc, [c.chunk_id for c in r.citations]),
        seconds_to_answer=round(seconds, 2))


def stream_pieces(text: str) -> list[str]:
    """Split the verified answer into word-sized pieces for the token events."""
    return re.findall(r"\S+\s*|\s+", text)


def _load_json(text: str | None) -> object:
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return text


# ---------------------------------------------------------------------------- routes

router = APIRouter(prefix="/api/v1")


@router.post("/query")
async def query(req: QueryRequest, request: Request,
                user: User = Depends(current_user)) -> EventSourceResponse:
    """Answer a question, streaming progress, then the verified answer, then citations."""
    svc = _services(request)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def emit(kind: str, data: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (kind, data))

    def work() -> None:
        start = time.perf_counter()
        try:
            with svc.lock:
                result = svc.assistant.ask(
                    req.text, user.user_id,
                    on_event=lambda step, payload: emit("progress", {"step": step, **payload}))
            emit("result", (result, time.perf_counter() - start))
        except Exception as exc:  # noqa: BLE001 - reported to the client as an SSE error
            emit("error", {"message": f"{type(exc).__name__}: {exc}"})
        finally:
            emit("done", None)

    threading.Thread(target=work, daemon=True).start()

    async def events():
        while True:
            kind, data = await queue.get()
            if kind == "progress":
                yield {"event": "progress", "data": json.dumps(data, default=str)}
            elif kind == "result":
                result, seconds = data
                for piece in stream_pieces(result.text):
                    yield {"event": "token", "data": json.dumps({"text": piece})}
                yield {"event": "final", "data": _final(svc, result, seconds).model_dump_json()}
            elif kind == "error":
                yield {"event": "error", "data": json.dumps(data)}
            else:  # done
                break

    return EventSourceResponse(events())


@router.get("/query/{query_id}", response_model=QueryDetail)
def get_query(query_id: int, request: Request,
              viewer: User = Depends(current_user)) -> QueryDetail:
    """The completed answer, its citations and its trace (asker or partner only)."""
    svc = _services(request)
    with svc.sessions() as s:
        q = s.get(Query, query_id)
        if q is None:
            raise HTTPException(404, "unknown query")
        if q.user_id != viewer.user_id and viewer.role != "partner":
            raise HTTPException(403, "only the person who asked, or a partner, may view this")
        answer = s.scalars(select(Answer).where(Answer.query_id == query_id)).first()
        cited = [c.chunk_id for c in s.scalars(select(Citation).where(
            Citation.answer_id == answer.id).order_by(Citation.id))] if answer else []
        traces = s.scalars(select(Trace).where(Trace.query_id == query_id).order_by(Trace.id)).all()
        trace = [TraceRow(agent=t.agent, model=t.model, effort=t.effort, tokens_in=t.tokens_in,
                          tokens_out=t.tokens_out, latency_ms=t.latency_ms,
                          input=_load_json(t.input), output=_load_json(t.output)) for t in traces]
        detail = dict(query_id=q.id, user_id=q.user_id, question=q.text, intent=q.intent,
                      status=answer.status if answer else None,
                      answer=answer.text if answer else None,
                      confidence=answer.confidence if answer else None)
    return QueryDetail(**detail, citations=_citations(svc, cited), trace=trace)


@router.get("/history", response_model=list[HistoryItem])
def history(request: Request, limit: int = Q(50, ge=1, le=200),
            user: User = Depends(current_user)) -> list[HistoryItem]:
    """The signed-in user's OWN past questions that have an answer, newest first.
    Personal to each user: partners do not see other people's history here."""
    with _services(request).sessions() as s:
        rows = s.execute(
            select(Query.id, Query.text, Query.created_at, Answer.status)
            .join(Answer, Answer.query_id == Query.id)
            .where(Query.user_id == user.user_id)
            .order_by(Query.created_at.desc(), Query.id.desc())
            .limit(limit)).all()
    return [HistoryItem(query_id=qid, question=text, status=status,
                        asked_at=asked if asked.tzinfo else asked.replace(tzinfo=timezone.utc))
            for qid, text, asked, status in rows]


_RETRIEVAL_STEPS = ("retrieval", "followup_retrieval", "verification_retry")


@router.get("/history/{query_id}", response_model=FinalPayload)
def history_answer(query_id: int, request: Request,
                   user: User = Depends(current_user)) -> FinalPayload:
    """Reopen one of the user's past answers, in the same shape as the streamed `final`
    event. Built only from what was stored at the time: the answer row, its citations
    and the agents' trace rows (claim counts from verification, flags from retrieval)."""
    svc = _services(request)
    with svc.sessions() as s:
        q = s.get(Query, query_id)
        answer = s.scalars(select(Answer).where(Answer.query_id == query_id)).first()
        if q is None or answer is None or q.user_id != user.user_id:
            raise HTTPException(404, "unknown query")  # same reply whether or not it exists
        cited = [c.chunk_id for c in s.scalars(
            select(Citation).where(Citation.answer_id == answer.id).order_by(Citation.id))]
        traces = s.scalars(select(Trace).where(Trace.query_id == query_id).order_by(Trace.id)).all()
    outputs: dict[str, dict] = {}  # agent -> its LAST output (a retry can verify twice)
    flagged: set[str] = set()
    for t in traces:
        out = _load_json(t.output)
        if isinstance(out, dict):
            outputs[t.agent] = out
            if t.agent in _RETRIEVAL_STEPS:
                flagged.update(out.get("injection_hits") or {})
    verification = outputs.get("verification", {})
    synthesis = outputs.get("synthesis", {})
    return FinalPayload(
        query_id=query_id, answer_id=answer.id, status=answer.status, text=answer.text,
        confidence=answer.confidence,
        claims_supported=int(verification.get("supported") or 0),
        claims_factual=int(verification.get("factual") or 0),
        removed_claims=len(verification.get("removed") or []),
        superseded_involved=bool(synthesis.get("superseded_involved")) and answer.status == "answered",
        injection_flagged_chunks=len(flagged),
        citations=_citations(svc, cited), seconds_to_answer=None)


@router.get("/traces/{query_id}", response_model=list[TraceRow])
def get_traces(query_id: int, request: Request,
               viewer: User = Depends(current_user)) -> list[TraceRow]:
    """Agent-by-agent trace of a query. Partners only."""
    svc = _services(request)
    if viewer.role != "partner":
        raise HTTPException(403, "traces are available to partners only")
    with svc.sessions() as s:
        if s.get(Query, query_id) is None:
            raise HTTPException(404, "unknown query")
        rows = s.scalars(select(Trace).where(Trace.query_id == query_id).order_by(Trace.id)).all()
        return [TraceRow(agent=t.agent, model=t.model, effort=t.effort, tokens_in=t.tokens_in,
                         tokens_out=t.tokens_out, latency_ms=t.latency_ms,
                         input=_load_json(t.input), output=_load_json(t.output)) for t in rows]


@router.post("/feedback")
def feedback(req: FeedbackRequest, request: Request, user: User = Depends(current_user)) -> dict:
    """Store helpful / not helpful plus an optional correction (masked). Stored only.
    Only the person who asked may rate an answer."""
    svc = _services(request)
    with svc.sessions() as s:
        answer = s.get(Answer, req.answer_id)
        if answer is None or s.get(Query, answer.query_id).user_id != user.user_id:
            raise HTTPException(404, "unknown answer")
        row = Feedback(answer_id=req.answer_id, user_id=user.user_id, rating=req.rating,
                       correction=mask_text(req.correction) if req.correction else None)
        s.add(row)
        s.commit()
        return {"feedback_id": row.id, "stored": True}


@router.post("/documents/reindex")
def reindex(request: Request, user: User = Depends(current_user)) -> dict:
    """Manual full re-index (unchanged files are skipped). Partners only; logged."""
    svc = _services(request)
    if user.role != "partner":
        raise HTTPException(403, "re-indexing is restricted to partners")
    if svc.indexer is None:
        raise HTTPException(503, "indexer not available")
    with svc.sessions() as s:
        write_audit_log(s, user.user_id, "manual_reindex_requested", "all")
    try:
        with svc.lock:
            report = svc.indexer.sync(actor=user.user_id)
    except ManifestError as exc:
        raise HTTPException(422, {"manifest_errors": exc.errors}) from exc
    return {"indexed": report.indexed, "metadata_updated": report.metadata_updated,
            "unchanged": len(report.unchanged), "removed": report.removed}


@router.get("/sources/status", dependencies=[Depends(current_user)])
def sources_status(request: Request) -> list[dict]:
    """Document and chunk counts, and last indexed time, per source type."""
    svc = _services(request)
    with svc.sessions() as s:
        docs = s.execute(select(Document.source_type, func.count(Document.doc_id),
                                func.max(Document.indexed_at))
                         .group_by(Document.source_type)).all()
        chunks = dict(s.execute(select(Document.source_type, func.count(Chunk.chunk_id))
                                .join(Chunk, Chunk.doc_id == Document.doc_id)
                                .group_by(Document.source_type)).all())
    return [{"source_type": st, "documents": n, "chunks": chunks.get(st, 0),
             "last_indexed_at": last.isoformat() if last else None} for st, n, last in docs]


@router.get("/metrics/quality", dependencies=[Depends(current_user)])
def metrics_quality(request: Request) -> dict:
    """The latest metrics.json from eval/results/. Never computes or estimates numbers."""
    results = _services(request).settings.eval_results_dir
    runs = sorted(p for p in results.glob("*/metrics.json")) if results.exists() else []
    if not runs:
        raise HTTPException(404, "no evaluation results yet")
    latest = runs[-1]
    return {"run": latest.parent.name,
            "metrics": json.loads(latest.read_text(encoding="utf-8"))}


@router.get("/users", response_model=list[UserOut])
def users(request: Request, viewer: User = Depends(current_user)) -> list[UserOut]:
    """All users with role, email and engagements. Partners only (emails are personal data)."""
    if viewer.role != "partner":
        raise HTTPException(403, "the user list is available to partners only")
    with _services(request).sessions() as s:
        return [user_out(s, u) for u in s.scalars(select(User).order_by(User.user_id))]


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ------------------------------------------------------------------------- app factory

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


def create_app(services: Services | None = None, static_dir: Path | None = FRONTEND_DIST) -> FastAPI:
    """App factory. Tests pass fake `services`; otherwise real ones are built at start-up.

    If the React UI has been built (`npm run build` in frontend/), its files are served at
    "/" so the whole demo runs from http://127.0.0.1:8000. API routes and /docs take
    precedence because they are registered first."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            app.state.services = services or await asyncio.to_thread(build_services, get_settings())
        except QdrantInUseError as exc:
            logging.getLogger("uvicorn.error").error("\n\n*** %s ***\n", exc)
            raise
        svc: Services = app.state.services
        if svc.indexer is not None:
            svc.indexer.load_reference_data()  # picks up users.csv emails without a re-index
        seeded, missing = seed_demo_passwords(svc.sessions, svc.settings.demo_password)
        log = logging.getLogger("uvicorn.error")
        if seeded:
            log.info("Demo password set for %d demo user(s).", seeded)
        if missing:
            log.warning("\n*** %d demo user(s) cannot sign in yet: set DEMO_PASSWORD in .env "
                        "and restart the API. ***\n", missing)
        try:
            yield
        finally:
            app.state.services.stop()

    app = FastAPI(title="Enterprise Fabric AI API", version="0.7.0", lifespan=lifespan,
                  description="Synthetic demo. Sign in with POST /api/v1/auth/login, then send "
                              "`Authorization: Bearer <token>`.")
    app.include_router(auth_router)
    app.include_router(router)
    if static_dir is not None and (static_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    return app


app = create_app()
