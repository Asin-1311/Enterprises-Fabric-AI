"""Sign-in tests: passwords, sessions, registration, password reset (no models, no LLM)."""

import logging
import re
import threading
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.auth import RESET_SENT, SIGN_IN_FAILED, seed_demo_passwords, utc_now
from app.api.main import Services, create_app
from app.db.models import AuditLog, AuthSession, PasswordReset, User
from app.db.session import init_db, make_engine, make_session_factory
from app.security.passwords import hash_password, token_digest, verify_password
from tests.test_agents import _settings

DEMO_PASSWORD = "demo-pass-123"
RIYA = "riya.kapoor@example-associates.test"


@pytest.fixture()
def app_client(tmp_path: Path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'auth.db').as_posix()}")
    init_db(engine)
    sessions = make_session_factory(engine)
    with sessions() as s:
        s.add_all([User(user_id="S01", name="Riya Kapoor", role="staff", synthetic=True, email=RIYA),
                   User(user_id="P01", name="Vikram Sethi", role="partner", synthetic=True,
                        email="vikram.sethi@example-associates.test"),
                   User(user_id="X01", name="No Email", role="staff", synthetic=True)])
        s.commit()
    settings = _settings().model_copy(update={"demo_password": DEMO_PASSWORD,
                                              "eval_results_dir": tmp_path / "results"})
    assistant = SimpleNamespace(deps=SimpleNamespace(sessions=sessions))  # auth needs only the DB
    services = Services(settings, assistant, None, threading.RLock())  # type: ignore[arg-type]
    with TestClient(create_app(services, static_dir=None)) as client:
        client.sessions = sessions  # type: ignore[attr-defined]
        yield client


def login(client: TestClient, email: str = RIYA, password: str = DEMO_PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def audit_rows(client) -> list[AuditLog]:
    with client.sessions() as s:
        return list(s.scalars(select(AuditLog).order_by(AuditLog.id)))


# --------------------------------------------------------------------------- passwords

def test_password_hash_is_salted_scrypt_and_verifies() -> None:
    a, b = hash_password("correct horse"), hash_password("correct horse")
    assert a.startswith("scrypt$16384$8$1$") and a != b  # random salt each time
    assert "correct horse" not in a
    assert verify_password("correct horse", a) and not verify_password("wrong horse", a)
    assert not verify_password("anything", None) and not verify_password("x", "garbage")


# --------------------------------------------------------------------------- sign-in

def test_demo_users_get_the_demo_password_at_startup(app_client) -> None:
    r = login(app_client)
    assert r.status_code == 200
    body = r.json()
    assert body["user"] == {"user_id": "S01", "name": "Riya Kapoor", "role": "staff",
                            "email": RIYA, "engagements": []}
    assert app_client.get("/api/v1/auth/me", headers=bearer(body["token"])).json()["user_id"] == "S01"
    with app_client.sessions() as s:
        assert s.get(User, "X01").password_hash is None  # no email: nothing to sign in with
        assert s.get(AuthSession, token_digest(body["token"])) is not None  # digest stored ...
        assert s.get(AuthSession, body["token"]) is None  # ... never the token itself


def test_email_is_case_and_space_insensitive(app_client) -> None:
    assert login(app_client, email="  Riya.Kapoor@Example-Associates.TEST ").status_code == 200


def test_wrong_password_and_unknown_email_get_the_same_answer(app_client) -> None:
    wrong, unknown = login(app_client, password="nope-nope"), login(app_client, email="who@x.test")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == SIGN_IN_FAILED
    actions = [(a.actor, a.action) for a in audit_rows(app_client)]
    assert ("S01", "sign_in_failed") in actions and ("unknown", "sign_in_failed") in actions
    logged = " ".join(f"{a.actor} {a.action} {a.target}" for a in audit_rows(app_client))
    assert "@" not in logged and "nope-nope" not in logged  # no emails or passwords logged


def test_protected_endpoints_need_a_valid_token(app_client) -> None:
    assert app_client.get("/api/v1/sources/status").status_code == 401
    assert app_client.get("/api/v1/sources/status", headers=bearer("made-up")).status_code == 401
    assert app_client.post("/api/v1/query", json={"text": "hi"}).status_code == 401
    assert app_client.get("/api/v1/health").status_code == 200  # public


def test_expired_session_and_logout_stop_the_token(app_client) -> None:
    token = login(app_client).json()["token"]
    assert app_client.post("/api/v1/auth/logout", headers=bearer(token)).json() == {"signed_out": True}
    assert app_client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401

    token = login(app_client).json()["token"]
    with app_client.sessions() as s:
        s.get(AuthSession, token_digest(token)).expires_at = utc_now() - timedelta(seconds=1)
        s.commit()
    r = app_client.get("/api/v1/auth/me", headers=bearer(token))
    assert r.status_code == 401 and "expired" in r.json()["detail"]


def test_user_list_is_partner_only(app_client) -> None:
    staff = login(app_client).json()["token"]
    partner = login(app_client, email="vikram.sethi@example-associates.test").json()["token"]
    assert app_client.get("/api/v1/users", headers=bearer(staff)).status_code == 403
    ids = [u["user_id"] for u in app_client.get("/api/v1/users", headers=bearer(partner)).json()]
    assert ids == ["P01", "S01", "X01"]


# --------------------------------------------------------------------------- registration

def test_register_creates_a_signed_in_staff_account_with_no_engagements(app_client) -> None:
    r = app_client.post("/api/v1/auth/register", json={
        "name": "  Asha   Verma ", "email": "Asha@New.test", "password": "long-enough-1"})
    assert r.status_code == 201
    user = r.json()["user"]
    assert user == {"user_id": "U0001", "name": "Asha Verma", "role": "staff",
                    "email": "asha@new.test", "engagements": []}
    assert app_client.get("/api/v1/auth/me", headers=bearer(r.json()["token"])).status_code == 200
    with app_client.sessions() as s:
        row = s.get(User, "U0001")
        assert row.synthetic is False and "long-enough-1" not in row.password_hash
    assert ("U0001", "account_created") in [(a.actor, a.action) for a in audit_rows(app_client)]
    second = app_client.post("/api/v1/auth/register", json={
        "name": "Second Person", "email": "second@new.test", "password": "long-enough-2"})
    assert second.json()["user"]["user_id"] == "U0002"


def test_register_cannot_choose_a_role_or_reuse_an_email(app_client) -> None:
    base = {"name": "Eve Example", "email": "eve@new.test", "password": "long-enough-1"}
    assert app_client.post("/api/v1/auth/register", json={**base, "role": "partner"}).status_code == 422
    assert app_client.post("/api/v1/auth/register", json={**base, "password": "short"}).status_code == 422
    assert app_client.post("/api/v1/auth/register", json={**base, "email": "not-an-email"}).status_code == 422
    assert app_client.post("/api/v1/auth/register", json={**base, "email": RIYA}).status_code == 409
    with app_client.sessions() as s:
        assert s.scalars(select(User).where(User.email == "eve@new.test")).first() is None


# --------------------------------------------------------------------------- password reset

def _reset_link(caplog) -> str:
    match = re.search(r"\?reset=([\w-]+)", caplog.text)
    assert match, "reset link not written to the console"
    return match.group(1)


def test_forgot_password_does_not_reveal_whether_an_account_exists(app_client, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="uvicorn.error")
    unknown = app_client.post("/api/v1/auth/forgot-password", json={"email": "nobody@x.test"})
    assert unknown.json() == {"message": RESET_SENT} and "?reset=" not in caplog.text
    known = app_client.post("/api/v1/auth/forgot-password", json={"email": RIYA})
    assert known.json() == unknown.json()
    assert "user S01" in caplog.text and RIYA not in caplog.text  # id, not email, in the console


def test_reset_link_works_once_and_signs_out_everywhere(app_client, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="uvicorn.error")
    old_session = login(app_client).json()["token"]
    app_client.post("/api/v1/auth/forgot-password", json={"email": RIYA})
    token = _reset_link(caplog)

    assert app_client.post("/api/v1/auth/reset-password",
                           json={"token": token, "password": "short"}).status_code == 422
    r = app_client.post("/api/v1/auth/reset-password", json={"token": token, "password": "brand-new-pass"})
    assert r.json() == {"reset": True}
    assert app_client.get("/api/v1/auth/me", headers=bearer(old_session)).status_code == 401
    assert login(app_client).status_code == 401  # old password gone
    assert login(app_client, password="brand-new-pass").status_code == 200
    again = app_client.post("/api/v1/auth/reset-password", json={"token": token, "password": "another-pass"})
    assert again.status_code == 400  # one use only

    # A restart must not put the demo password back over the user's own password.
    assert seed_demo_passwords(app_client.sessions, DEMO_PASSWORD) == (0, 0)
    assert login(app_client, password="brand-new-pass").status_code == 200


def test_expired_reset_link_is_refused(app_client, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="uvicorn.error")
    app_client.post("/api/v1/auth/forgot-password", json={"email": RIYA})
    token = _reset_link(caplog)
    with app_client.sessions() as s:
        s.get(PasswordReset, token_digest(token)).expires_at = utc_now() - timedelta(seconds=1)
        s.commit()
    r = app_client.post("/api/v1/auth/reset-password", json={"token": token, "password": "brand-new-pass"})
    assert r.status_code == 400 and login(app_client).status_code == 200  # password unchanged
