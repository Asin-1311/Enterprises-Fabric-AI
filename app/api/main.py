"""FastAPI backend (CLAUDE.md section 9). All routes are under /api/v1.

Run from the project root:
    .venv\\Scripts\\python.exe -m uvicorn app.api.main:app --port 8000
Interactive docs: http://127.0.0.1:8000/docs

One process holds the models, the single local Qdrant client, the assistant and (by
default) the folder watcher. They share one BM25 index and one readers-writer lock
(app/concurrency.py): any number of questions are answered at the same time, while a
re-index or watcher update waits for them and runs alone. (Phase 1 held a single lock
for the whole of every question; removed in M9, audit finding R-5-par.)

Streaming (POST /query, Server-Sent Events):
    event: progress       {"step": "retrieving", ...}  live, one per agent step
    event: draft_token    {"text": "..."}               the DRAFT, as the model writes it (M14b)
    event: claim_verdict  {"claim_id", "text", "citations", "verdict"}  one per claim,
                          verdict = supported | removed | flagged ("check this point")
    event: token          {"text": "..."}               the VERIFIED answer, in pieces
    event: final          FinalPayload                  status, confidence, citations, flags
    event: error          {"message": "..."}
The draft is shown as it is written, marked as being checked (CLAUDE.md sections 10 and
11; this replaces the M6 choice of sending verified text only). Verification may remove
sentences from it, so the client must replace the draft with the verified text: the
claim_verdict events say which sentences stay, and `final` carries the verified answer.
A retry after low confidence writes a new draft: each "synthesizing" progress event
starts one.

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
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query as Q, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sse_starlette.sse import EventSourceResponse

from app.agents.graph import AnswerResult, AuditAssistant
from app.agents.versions import ensure_active_versions
from app.api.auth import current_user, seed_demo_passwords, user_out
from app.api.admin import require_admin
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.webhooks import router as webhook_router
from app.api.schemas import (
    CitationOut,
    FeedbackOut,
    FeedbackRequest,
    FinalPayload,
    HealthOut,
    HistoryItem,
    QualityMetricsOut,
    QueryDetail,
    QueryRequest,
    ReindexOut,
    ReindexRequest,
    SignalOut,
    SignalRequest,
    SourceTypeStatus,
    TraceRow,
    UserOut,
)
from app.concurrency import ReadWriteLock
from app.config import PROJECT_ROOT, Settings, get_settings
from app.db.models import (
    Answer,
    Chunk,
    Citation,
    Document,
    Feedback,
    FeedbackSignal,
    Query,
    Source,
    Trace,
    User,
)
from app.db.session import write_audit_log
from app.quality_runs import QUALITY_TYPES, quality_runs
from app.ingest.indexer import Indexer, ManifestError
from app.ports import EventBus
from app.retrieval.dense import QdrantInUseError
from app.security.masking import mask_text

PREVIEW_CHARS = 700
log = logging.getLogger("uvicorn.error")

# Errors shown to clients are generic; the details stay in the server log under the same
# reference, so support can find them without exposing internals (audit finding, M9).
GENERIC_ERROR = ("Something went wrong while answering. Please try again; if it keeps "
                 "happening, quote reference {ref}.")


def error_reference() -> str:
    """A short random id that links a client-visible error to its server log entry."""
    return uuid.uuid4().hex[:8]


@dataclass
class Services:
    """Long-lived objects shared by all requests."""

    settings: Settings
    assistant: AuditAssistant
    indexer: Indexer | None
    lock: ReadWriteLock
    stop: Callable[[], None] = lambda: None
    bus: EventBus | None = None  # change events (M12a): the watcher and webhook publish here

    @property
    def sessions(self):
        return self.assistant.deps.sessions


def build_services(settings: Settings) -> Services:
    """Real services: models, indexes, LLM client, event bus, ingestion worker and the
    folder watcher."""
    from app.adapters import open_event_bus
    from app.agents.graph import build_assistant
    from app.ingest.indexer import build_indexer
    from app.ingest.watcher import DebouncedCorpusHandler, start_observer
    from app.ingest.worker import IngestWorker, start_in_background

    assistant = build_assistant(settings)
    searcher = assistant.deps.searcher
    # Share the searcher's backends, so a re-index is visible to the next search.
    indexer = build_indexer(settings, embedder=searcher.embedder, store=searcher.store,
                            bm25=searcher.bm25, sessions=searcher.sessions)
    lock = ReadWriteLock()
    indexer.lock = lock.writer  # index writes run alone; questions share the read side
    bus = open_event_bus(settings)
    stoppers: list[Callable[[], None]] = []
    if settings.api_run_worker:
        worker = IngestWorker(indexer, bus, max_attempts=settings.worker_max_attempts)
        thread, stop_worker = start_in_background(worker)
        stoppers.append(lambda: (stop_worker.set(), thread.join(timeout=10), worker.close()))
    if settings.api_watch_corpus:
        handler = DebouncedCorpusHandler(indexer, settings.corpus_dir, settings.manifest_path,
                                         settings.watcher_debounce_seconds, bus=bus)
        observer = start_observer(handler)
        stoppers.append(lambda: (observer.stop(), observer.join(timeout=5)))
    if settings.api_run_connectors:  # M12c: poll git, s3, confluence, jira, sharepoint sources
        from app.connectors.runner import ConnectorRunner
        from app.connectors.runner import start_in_background as start_runner

        runner_thread, stop_runner = start_runner(ConnectorRunner(indexer.sessions, bus, settings))
        stoppers.append(lambda: (stop_runner.set(), runner_thread.join(timeout=10)))

    def stop() -> None:
        for stopper in reversed(stoppers):
            stopper()
        bus.close()

    return Services(settings, assistant, indexer, lock, stop, bus)


# --------------------------------------------------------------------------- helpers

def _services(request: Request) -> Services:
    return request.app.state.services


def _preview(text: str) -> str:
    """Chunk text without its heading-path line, shortened for display."""
    body = text.split("\n", 1)[1] if "\n" in text else text
    body = " ".join(body.split())
    return body if len(body) <= PREVIEW_CHARS else body[:PREVIEW_CHARS].rsplit(" ", 1)[0] + " …"


def _citations(svc: Services, cited: list[tuple[str, int | None, int | None]]) -> list[CitationOut]:
    """Resolve (chunk id, span start, span end) to title, section, page, a preview and
    the supporting span."""
    if not cited:
        return []
    chunk_ids = [cid for cid, _, _ in cited]
    with svc.sessions() as s:
        superseded = {d for d in s.scalars(select(Document.supersedes)) if d}
        found = s.execute(
            select(Chunk, Document).join(Document, Document.doc_id == Chunk.doc_id)
            .where(Chunk.chunk_id.in_(chunk_ids) | Chunk.legacy_chunk_id.in_(chunk_ids))).all()
    # An answer written before M12b cites Phase 1 ids ("STD-CH06:0004"); they resolve to
    # the chunk that had that id and still has the same text (legacy_chunk_id).
    rows = {c.legacy_chunk_id: (c, d) for c, d in found if c.legacy_chunk_id}
    rows |= {c.chunk_id: (c, d) for c, d in found}
    out = []
    for cid, span_start, span_end in cited:
        if cid in rows:
            c, d = rows[cid]
            out.append(CitationOut(
                chunk_id=cid, doc_id=d.doc_id, title=d.title, section=c.section,
                section_title=c.section_title, page_start=c.page_start, page_end=c.page_end,
                superseded=d.doc_id in superseded, preview=_preview(c.text),
                span_start=span_start, span_end=span_end))
    return out


def _final(svc: Services, r: AnswerResult, seconds: float) -> FinalPayload:
    return FinalPayload(
        query_id=r.query_id, answer_id=r.answer_id, status=r.status, text=r.text,
        confidence=r.confidence, claims_supported=r.claims_supported,
        claims_factual=r.claims_factual, removed_claims=len(r.removed_claims),
        superseded_involved=r.superseded_involved,
        injection_flagged_chunks=len(r.injection_hits),
        citations=_citations(svc, [(c.chunk_id, c.span_start, c.span_end) for c in r.citations]),
        seconds_to_answer=round(seconds, 2),
        flagged_claims=[f["claim"] for f in r.flagged_claims], degraded=r.degraded)


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


@router.post("/query", response_class=EventSourceResponse, responses={200: {
    "description": "Server-Sent Events: `progress` (agent steps), `draft_token` (the draft as "
                   "it is written), `claim_verdict` (one per claim), `token` (the verified "
                   "answer in pieces), then `final` (a FinalPayload as JSON) or `error` "
                   "(generic message and a reference).",
    "content": {"text/event-stream": {"schema": {"type": "string"}}}}})
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
            with svc.lock.reading():  # many questions at once; never during an index write
                result = svc.assistant.ask(
                    req.text, user.user_id,
                    on_event=lambda step, payload: emit(
                        "claim_verdict" if step == "claim_verdict" else "progress",
                        payload if step == "claim_verdict" else {"step": step, **payload}),
                    on_token=lambda text: emit("draft_token", {"text": text}))
            emit("result", (result, time.perf_counter() - start))
        except Exception:  # noqa: BLE001 - details to the server log, generic text to the client
            ref = error_reference()
            log.exception("Query failed (reference %s, user %s)", ref, user.user_id)
            emit("error", {"message": GENERIC_ERROR.format(ref=ref), "reference": ref})
        finally:
            emit("done", None)

    threading.Thread(target=work, daemon=True).start()

    async def events():
        while True:
            kind, data = await queue.get()
            if kind in ("progress", "draft_token", "claim_verdict"):
                yield {"event": kind, "data": json.dumps(data, default=str)}
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
        cited = [(c.chunk_id, c.span_start, c.span_end) for c in s.scalars(select(Citation).where(
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
        cited = [(c.chunk_id, c.span_start, c.span_end) for c in s.scalars(
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
        citations=_citations(svc, cited), seconds_to_answer=None,
        flagged_claims=[f.get("claim", "") for f in (answer.flagged_claims or [])],
        degraded=bool(answer.degraded))


@router.get("/traces/{query_id}", response_model=list[TraceRow])
def get_traces(query_id: int, request: Request,
               viewer: User = Depends(current_user)) -> list[TraceRow]:
    """Agent-by-agent trace of a query. Partners see everything. Administrators who are not
    partners see how each step ran (model, tokens, timing, versions) but not its input or
    output: those contain document text and draft answers, and the admin flag grants no
    document access."""
    svc = _services(request)
    if viewer.role != "partner" and not viewer.is_admin:
        raise HTTPException(403, "traces are available to partners and administrators only")
    hide = viewer.role != "partner"
    with svc.sessions() as s:
        if s.get(Query, query_id) is None:
            raise HTTPException(404, "unknown query")
        rows = s.scalars(select(Trace).where(Trace.query_id == query_id).order_by(Trace.id)).all()
        return [TraceRow(agent=t.agent, model=t.model, effort=t.effort, tokens_in=t.tokens_in,
                         tokens_out=t.tokens_out, latency_ms=t.latency_ms,
                         input=None if hide else _load_json(t.input),
                         output=None if hide else _load_json(t.output),
                         prompt_version_id=t.prompt_version_id,
                         policy_version_id=t.policy_version_id, content_hidden=hide)
                for t in rows]


@router.post("/feedback", response_model=FeedbackOut)
def feedback(req: FeedbackRequest, request: Request, user: User = Depends(current_user)) -> dict:
    """Store helpful / not helpful plus an optional correction (masked). Only the person who
    asked may rate an answer. "Not helpful" within 24 hours counts against the sources and
    retrievers that produced the answer when the routing policy learns (M15b)."""
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


@router.post("/feedback/signals", response_model=SignalOut, status_code=201)
def feedback_signal(req: SignalRequest, request: Request,
                    user: User = Depends(current_user)) -> SignalOut:
    """Store an implicit signal (copy, citation click, follow-up, rephrase) about one of
    the user's own answers. Stored only; the learning loop uses them from M15."""
    with _services(request).sessions() as s:
        answer = s.get(Answer, req.answer_id)
        if answer is None or s.get(Query, answer.query_id).user_id != user.user_id:
            raise HTTPException(404, "unknown answer")
        row = FeedbackSignal(answer_id=req.answer_id, user_id=user.user_id, kind=req.kind)
        s.add(row)
        s.commit()
        return SignalOut(signal_id=row.id, stored=True)


@router.post("/documents/reindex", response_model=ReindexOut)
def reindex(req: ReindexRequest, request: Request, user: User = Depends(require_admin)) -> ReindexOut:
    """Re-index one document, one source, or everything (administrators; logged).

    A document or a source is rebuilt from scratch even if unchanged, and nothing else is
    touched. `all` brings every index in line with the manifest (unchanged files skipped)."""
    svc = _services(request)
    if svc.indexer is None:
        raise HTTPException(503, "indexer not available")
    scope, target = (("document", req.doc_id) if req.doc_id is not None else
                     ("source", req.source_id) if req.source_id is not None else ("all", None))
    with svc.sessions() as s:
        if scope == "source" and s.get(Source, target) is None:
            raise HTTPException(404, "unknown source")
        write_audit_log(s, user.user_id, "manual_reindex_requested", f"{scope}: {target or 'all'}")
    try:
        with svc.lock.writing():
            if scope == "document":
                report = svc.indexer.reindex_document(target, actor=user.user_id)
            elif scope == "source":
                report = svc.indexer.reindex_source(target, actor=user.user_id)
            else:
                report = svc.indexer.sync(actor=user.user_id)
    except KeyError:
        raise HTTPException(404, "unknown document") from None
    except ManifestError as exc:
        raise HTTPException(422, {"manifest_errors": exc.errors}) from exc
    return ReindexOut(scope=scope, target=target, indexed=report.indexed,
                      metadata_updated=report.metadata_updated,
                      unchanged=len(report.unchanged), removed=report.removed)


@router.get("/sources/status", response_model=list[SourceTypeStatus],
            dependencies=[Depends(current_user)])
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


def _window_bound(value: str | None, name: str, end_of_day: bool) -> datetime | None:
    """Parse `from`/`to`: a date (YYYY-MM-DD) or a date-time (YYYY-MM-DDTHH:MM[:SS])."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"'{name}' must be a date (YYYY-MM-DD) or date-time") from None
    if end_of_day and len(value) == 10:  # a bare date includes the whole day
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed.replace(tzinfo=None)


@router.get("/metrics/quality", response_model=QualityMetricsOut,
            dependencies=[Depends(current_user)])
def metrics_quality(request: Request, from_: str | None = Q(None, alias="from"),
                    to: str | None = Q(None)) -> dict:
    """Retrieval and answer-quality metrics, exactly as saved in eval/results/.

    Only `retrieval` and `answers` runs count (calibration runs and known-invalid runs are
    left out). With `from`/`to`, every such run started in that window is returned, newest
    first; without them, the latest run of each type. Never computes or estimates."""
    start = _window_bound(from_, "from", end_of_day=False)
    end = _window_bound(to, "to", end_of_day=True)
    if start and end and start > end:
        raise HTTPException(422, "'from' is after 'to'")
    runs = quality_runs(_services(request).settings.eval_results_dir, start, end)
    if not (start or end):  # latest of each type
        runs = [next(r for r in runs if r.type == t) for t in QUALITY_TYPES
                if any(r.type == t for r in runs)]
    if not runs:
        raise HTTPException(404, "no evaluation results in this window")
    return {"from": start.isoformat() if start else None, "to": end.isoformat() if end else None,
            "runs": [{"run": r.name, "type": r.type, "started_at": r.started_at.isoformat(),
                      "metrics": json.loads((r.path / "metrics.json").read_text(encoding="utf-8"))}
                     for r in runs]}


@router.get("/users", response_model=list[UserOut])
def users(request: Request, viewer: User = Depends(current_user)) -> list[UserOut]:
    """All users with role, email and engagements. Partners only (emails are personal data)."""
    if viewer.role != "partner":
        raise HTTPException(403, "the user list is available to partners only")
    with _services(request).sessions() as s:
        return [user_out(s, u) for u in s.scalars(select(User).order_by(User.user_id))]


@router.get("/health", response_model=HealthOut)
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
        ensure_active_versions(svc.sessions)  # routing policy v1 and prompt versions exist
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
    app.include_router(admin_router)
    app.include_router(webhook_router)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """Any unhandled error: generic message to the client, details to the log."""
        ref = error_reference()
        log.error("Unhandled error on %s %s (reference %s)", request.method, request.url.path,
                  ref, exc_info=exc)
        return JSONResponse(status_code=500,
                            content={"detail": GENERIC_ERROR.format(ref=ref), "reference": ref})

    if static_dir is not None and (static_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    return app


app = create_app()
