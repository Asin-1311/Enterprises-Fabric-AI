"""API contract tests (CLAUDE.md section 13.3; Gap Register R-13.3-contract, API part).

1. The committed OpenAPI file (docs/api/openapi.json) equals what the code generates, so
   the API reference is always current and every API change is deliberate.
2. Every endpoint declares a typed success response (JSON schema, or an event stream).
3. Every endpoint is called here with valid input; its response must be a success and
   must match the response model the endpoint declares. A new endpoint without a case
   here fails the test.

Covers: R-13.3-contract (API part; the connector contracts are in tests/test_connector_contracts.py).
"""

import json
from datetime import datetime, timedelta

from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from sqlalchemy import select

from app.adapters.lite.events import InProcessEventBus
from app.api.auth import utc_now
from app.api.webhooks import signature
from app.api.schemas import FinalPayload
from app.db.models import PasswordReset, Source, User
from app.security.passwords import token_digest
from scripts.export_openapi import OPENAPI_FILE, current_schema
from tests.test_api import PASSWORD, QUESTION, as_user, ask, client, parse_sse  # noqa: F401


def test_committed_openapi_matches_the_code() -> None:
    """Covers: R-13.3-contract."""
    committed = json.loads(OPENAPI_FILE.read_text(encoding="utf-8"))
    assert committed == current_schema(), (
        "The API changed: run `python -m scripts.export_openapi`, review and commit "
        "docs/api/openapi.json.")


def test_every_endpoint_declares_a_typed_success_response() -> None:
    """Covers: R-13.3-contract."""
    for path, operations in current_schema()["paths"].items():
        for method, op in operations.items():
            ok = next(code for code in op["responses"] if code.startswith("2"))
            content = op["responses"][ok].get("content", {})
            if (method, path) == ("post", "/api/v1/query"):
                assert "text/event-stream" in content
            else:
                schema = content.get("application/json", {}).get("schema")
                assert schema, f"{method.upper()} {path} has no typed response"


def _api_routes(app_or_router, prefix: str = "") -> dict[tuple[str, str], APIRoute]:
    """Every API route with its method. Recent FastAPI versions keep an included router as
    one entry wrapping the original router, so walk into those as well."""
    found: dict[tuple[str, str], APIRoute] = {}
    for r in app_or_router.routes:
        if isinstance(r, APIRoute):
            found.update({(method, prefix + r.path): r for method in r.methods})
        elif hasattr(r, "original_router"):
            inner_prefix = prefix + (getattr(getattr(r, "include_context", None), "prefix", "") or "")
            found.update(_api_routes(r.original_router, inner_prefix))
    return found


def test_every_endpoint_answers_valid_input_with_its_declared_shape(client, tmp_path,
                                                                    monkeypatch) -> None:
    """Covers: R-13.3-contract."""
    staff, partner, admin = as_user(client, "S1"), as_user(client, "P1"), as_user(client, "A1")
    services = client.app.state.services
    # The webhook needs an event bus, a webhook source with its secret, and a landing folder
    # (a temporary one, so the test never writes into data/landing/).
    monkeypatch.setattr(services, "bus", InProcessEventBus())
    monkeypatch.setattr(services, "settings",
                        services.settings.model_copy(update={"landing_dir": tmp_path / "landing"}))
    monkeypatch.setenv("CONTRACT_HOOK_SECRET", "contract-secret")
    with services.sessions() as s:
        if s.get(Source, "contract-hook") is None:
            s.add(Source(id="contract-hook", type="webhook",
                         connector_config={"secret": "env:CONTRACT_HOOK_SECRET"},
                         classification="public_internal", status="active",
                         created_at=datetime(2026, 1, 1)))
            s.commit()

    def webhook(c):
        raw = json.dumps({"doc_id": "CONTRACT-HOOK-1", "title": "Pushed", "source_type": "faq",
                          "access_level": "staff", "effective_date": "2026-11-01",
                          "content": "# Q\n\nA pushed answer."}).encode()
        return c.post("/api/v1/webhooks/documents", content=raw, headers={
            "Content-Type": "application/json", "X-Source-Id": "contract-hook",
            "X-Signature-256": signature("contract-secret", raw)})

    first = ask(client, QUESTION, "S1")[-1][1]
    qid, answer_id = first["query_id"], first["answer_id"]
    run = client.results_dir / "20260101-000000_retrieval"
    run.mkdir(parents=True, exist_ok=True)
    (run / "metrics.json").write_text(json.dumps({"recall@5": 0.5}), encoding="utf-8")

    def register(c):
        original = services.settings
        services.settings = original.model_copy(update={"allow_self_registration": True})
        try:
            return c.post("/api/v1/auth/register", json={
                "name": "Contract Case", "email": "contract.case@example-associates.test",
                "password": "contract-pass-1"})
        finally:
            services.settings = original

    def logout(c):
        token = c.post("/api/v1/auth/login", json={"email": "s2@test.example",
                                                    "password": PASSWORD}).json()["token"]
        return c.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})

    def reset_password(c):
        with services.sessions() as s:  # a link for the account the register case created
            user = s.scalars(select(User).where(User.email == "contract.case@example-associates.test")).one()
            s.add(PasswordReset(token_digest=token_digest("contract-reset-token"), user_id=user.user_id,
                                expires_at=utc_now() + timedelta(minutes=5)))
            s.commit()
        return c.post("/api/v1/auth/reset-password",
                      json={"token": "contract-reset-token", "password": "contract-pass-2"})

    cases = {  # (method, path) -> call; order matters where one case needs another
        ("GET", "/api/v1/health"): lambda c: c.get("/api/v1/health"),
        ("GET", "/api/v1/auth/config"): lambda c: c.get("/api/v1/auth/config"),
        ("POST", "/api/v1/auth/login"): lambda c: c.post("/api/v1/auth/login", json={
            "email": "s1@test.example", "password": PASSWORD}),
        ("POST", "/api/v1/auth/register"): register,
        ("POST", "/api/v1/auth/reset-password"): reset_password,
        ("GET", "/api/v1/auth/me"): lambda c: c.get("/api/v1/auth/me", headers=staff),
        ("POST", "/api/v1/auth/logout"): logout,
        ("POST", "/api/v1/auth/forgot-password"): lambda c: c.post(
            "/api/v1/auth/forgot-password", json={"email": "s1@test.example"}),
        ("POST", "/api/v1/query"): lambda c: c.post("/api/v1/query", json={"text": QUESTION},
                                                    headers=staff),
        ("GET", "/api/v1/query/{query_id}"): lambda c: c.get(f"/api/v1/query/{qid}", headers=staff),
        ("GET", "/api/v1/history"): lambda c: c.get("/api/v1/history", headers=staff),
        ("GET", "/api/v1/history/{query_id}"): lambda c: c.get(f"/api/v1/history/{qid}", headers=staff),
        ("GET", "/api/v1/traces/{query_id}"): lambda c: c.get(f"/api/v1/traces/{qid}", headers=admin),
        ("POST", "/api/v1/feedback"): lambda c: c.post("/api/v1/feedback", headers=staff, json={
            "answer_id": answer_id, "rating": "helpful"}),
        ("POST", "/api/v1/feedback/signals"): lambda c: c.post(
            "/api/v1/feedback/signals", headers=staff, json={"answer_id": answer_id, "kind": "copy"}),
        ("POST", "/api/v1/documents/reindex"): lambda c: c.post(
            "/api/v1/documents/reindex", headers=admin, json={"doc_id": "POL-NEW"}),
        ("GET", "/api/v1/sources/status"): lambda c: c.get("/api/v1/sources/status", headers=staff),
        ("POST", "/api/v1/sources"): lambda c: c.post("/api/v1/sources", headers=admin, json={
            "id": "contract-s3", "type": "s3", "connector_config": {
                "bucket": "firm-documents",
                "defaults": {"source_type": "firm_policy", "access_level": "staff"}}}),
        ("GET", "/api/v1/sources/{source_id}/status"): lambda c: c.get(
            "/api/v1/sources/fs-firm_policy/status", headers=admin),
        ("PUT", "/api/v1/routing-policy"): lambda c: c.put(
            "/api/v1/routing-policy", headers=admin, json={"version": 1}),
        ("GET", "/api/v1/metrics/quality"): lambda c: c.get("/api/v1/metrics/quality", headers=staff),
        ("GET", "/api/v1/admin/prompts"): lambda c: c.get("/api/v1/admin/prompts", headers=admin),
        ("POST", "/api/v1/admin/prompts"): lambda c: c.post("/api/v1/admin/prompts", headers=admin, json={
            "agent": "query_understanding", "template": "Classify the audit question."}),
        ("POST", "/api/v1/admin/prompts/{version_id}/activate"): lambda c: c.post(
            "/api/v1/admin/prompts/{}/activate".format(next(
                p["id"] for p in c.get("/api/v1/admin/prompts?agent=synthesis", headers=admin).json()
                if p["is_active"])), headers=admin),
        ("GET", "/api/v1/users"): lambda c: c.get("/api/v1/users", headers=partner),
        ("POST", "/api/v1/webhooks/documents"): webhook,
    }

    routes = _api_routes(client.app)
    assert set(cases) == set(routes), (
        f"endpoints without a contract case: {sorted(set(routes) - set(cases))}; "
        f"cases for missing endpoints: {sorted(set(cases) - set(routes))}")
    for key, call in cases.items():
        response = call(client)
        assert 200 <= response.status_code < 300, (key, response.status_code, response.text[:300])
        if key == ("POST", "/api/v1/query"):
            events = parse_sse(response.text)
            assert events[-1][0] == "final"
            TypeAdapter(FinalPayload).validate_python(events[-1][1])
            continue
        TypeAdapter(routes[key].response_model).validate_python(response.json())
