"""OpenTelemetry traces (CLAUDE.md section 14; Gap Register R-14, R-6-stack; M17a).

One trace per question: a root span `query`, a child span per agent step, and under a
step one `llm` span per model call. Every span carries the query id, the agent, model,
effort, tokens, and the prompt and routing-policy versions.

Spans are exported over OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set. In the
observability profile that endpoint is Langfuse, which shows the model-call spans as
"generations" (the `langfuse.observation.type` and `gen_ai.*` attributes: model, tokens),
with the prompt and routing-policy versions in their metadata: Langfuse's LLM call log
comes from this same pipeline, no second SDK.

What leaves the process: the masked input and output of model-calling steps (as written
to the `traces` table, CLAUDE.md section 12), cut to OTEL_CONTENT_MAX_CHARS, and only when
OTEL_INCLUDE_CONTENT is on. Never the content of a call to the private model: restricted
text stays with the private model and the local `traces` table.
"""

import base64
import json
import time
from collections.abc import Sequence
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from app.config import Settings, env_reference

_TRACER_NAME = "enterprise-fabric-ai"
_provider: TracerProvider | None = None


def setup_tracing(settings: Settings, exporter: SpanExporter | None = None) -> TracerProvider | None:
    """Install the tracer provider once per process. Without an endpoint (and no test
    exporter) tracing stays a no-op."""
    global _provider
    if _provider is not None:
        return _provider
    if exporter is None and not settings.otel_exporter_otlp_endpoint:
        return None
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint,
                                    headers=_headers(settings))
    _provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name}))
    _provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_provider)
    return _provider


def tracing_enabled() -> bool:
    """True once a tracer provider is installed (setup_tracing found an endpoint)."""
    return _provider is not None


def _headers(settings: Settings) -> dict[str, str]:
    """Langfuse authenticates OTLP with Basic auth: public key and secret key (from .env)."""
    public = env_reference(settings.langfuse_public_key_ref)
    secret = env_reference(settings.langfuse_secret_key_ref)
    if public and secret:
        token = base64.b64encode(f"{public}:{secret}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return {}


def tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + " …"


class QuerySpan:
    """The root span of one question; agent steps hang under it, also when LangGraph runs
    them in other threads (the parent is passed explicitly, not taken from a thread)."""

    def __init__(self, query_id: int, user_role: str | None = None) -> None:
        self.span = tracer().start_span("query", attributes={
            "efa.query_id": query_id, **({"efa.user_role": user_role} if user_role else {})})
        self.context = trace.set_span_in_context(self.span)

    def step(self, agent: str, *, latency_ms: float | None, llm: Sequence, versions: dict[str, Any],
             step_input: Any = None, step_output: Any = None, include_content: bool = False,
             content_max_chars: int = 4000) -> None:
        """Record one finished agent step (and its model calls) under the query span."""
        end = time.time_ns()
        start = end - int((latency_ms or 0) * 1_000_000)
        attrs = {"efa.agent": agent, **{f"efa.{k}": v for k, v in versions.items() if v is not None}}
        step_span = tracer().start_span(f"agent.{agent}", context=self.context, start_time=start,
                                        attributes=attrs)
        step_ctx = trace.set_span_in_context(step_span)
        for r in llm:
            private = str(r.model).startswith("ollama:")
            call_end = end
            call_start = call_end - int(r.latency_ms * 1_000_000)
            call_attrs = {
                **attrs, "langfuse.observation.type": "generation",
                "gen_ai.system": "ollama" if private else "anthropic",
                "gen_ai.request.model": r.model, "gen_ai.response.model": r.model,
                "gen_ai.usage.input_tokens": r.tokens_in, "gen_ai.usage.output_tokens": r.tokens_out,
                "efa.effort": r.effort,
            }
            # The prompt version travels as efa.prompt_version_id (Langfuse shows it under
            # metadata): langfuse.observation.prompt.* would refer to a prompt kept IN
            # Langfuse, and Langfuse rejects the span when that prompt does not exist there
            # (found in M17a). Our prompts live in our own registry (prompt_versions).
            if include_content and not private:
                call_attrs["langfuse.observation.input"] = _clip(step_input, content_max_chars)
                call_attrs["langfuse.observation.output"] = _clip(step_output, content_max_chars)
            call = tracer().start_span(f"llm.{agent}", context=step_ctx, start_time=call_start,
                                       attributes=call_attrs)
            call.end(end_time=call_end)
        step_span.end(end_time=end)

    def end(self, status: str) -> None:
        self.span.set_attribute("efa.status", status)
        self.span.end()


def shutdown() -> None:
    """Flush spans (tests and process exit)."""
    if _provider is not None:
        _provider.force_flush()


__all__ = ["QuerySpan", "setup_tracing", "shutdown", "tracer", "tracing_enabled", "otel_context"]
