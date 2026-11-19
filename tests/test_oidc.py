"""M13b: OIDC sign-in with Keycloak-issued JWTs (full profile), tested with a local RSA key.

Covers: R-12-auth. The real Keycloak realm is tested in tests/integration/.
"""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.query import ClientMatcher
from app.api.main import Services, create_app
from app.api.oidc import Identity, OidcValidator, TokenRejected, identity_from_claims
from app.concurrency import ReadWriteLock
from app.db.models import AuditLog, User
from tests.helpers import make_indexer, make_searcher, write_doc, write_manifest, write_people
from tests.test_agents import DOCS, ENGAGEMENTS, USERS, FakeLLM, FakeReranker, _settings
from tests.test_api import with_emails

ISSUER = "http://keycloak.test/realms/fabric"
AUDIENCE = "fabric-api"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FixedKeys:
    """Stands in for the realm's JWKS endpoint: the public half of KEY."""

    def get_signing_key_from_jwt(self, token: str):
        return SimpleNamespace(key=KEY.public_key())


def token(user: str = "s1", roles: tuple[str, ...] = ("staff",), *, key=KEY, alg: str = "RS256",
          **claims) -> str:
    now = int(time.time())
    body = {"iss": ISSUER, "aud": AUDIENCE, "sub": f"uuid-{user}", "iat": now, "exp": now + 300,
            "preferred_username": user, "realm_access": {"roles": list(roles)}} | claims
    return jwt.encode({k: v for k, v in body.items() if v is not None}, key, algorithm=alg)


def validator() -> OidcValidator:
    return OidcValidator(ISSUER, AUDIENCE, leeway_seconds=5, keys=FixedKeys())


# ------------------------------------------------------------------- the validator

def test_a_valid_token_names_the_user_role_and_admin_flag() -> None:
    assert validator().validate(token("s1")) == Identity("S1", "staff", False, "uuid-s1")
    assert validator().validate(token("a1", ("staff", "admin", "offline_access"))) == \
        Identity("A1", "staff", True, "uuid-a1")
    assert validator().validate(token("p1", ("staff", "partner"))).role == "partner"  # highest


BAD_TOKENS = {
    "expired": lambda: token(exp=int(time.time()) - 60, iat=int(time.time()) - 400),
    "not yet valid": lambda: token(nbf=int(time.time()) + 600),
    "wrong audience": lambda: token(aud="account"),
    "wrong issuer": lambda: token(iss="http://evil.test/realms/fabric"),
    "no expiry": lambda: token(exp=None),
    "signed by another key": lambda: token(key=OTHER_KEY),
    "unsigned (alg none)": lambda: jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "x",
                                               "exp": int(time.time()) + 300, "iat": int(time.time()),
                                               "preferred_username": "p1",
                                               "realm_access": {"roles": ["partner"]}},
                                              None, algorithm="none"),
    "HMAC-signed (HS256 is not an allowed algorithm)": lambda: jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "x"},
                                                   "not-a-real-secret-but-long-enough-for-hs256",
                                                   algorithm="HS256"),
    "tampered payload": lambda: (lambda t: ".".join([t.split(".")[0], token("p1", ("partner",)).split(".")[1],
                                                     t.split(".")[2]]))(token("s1")),
    "no role": lambda: token(roles=("offline_access",)),
    "no user name": lambda: token(preferred_username=None),
    "garbage": lambda: "not.a.jwt",
}


@pytest.mark.parametrize("case", sorted(BAD_TOKENS))
def test_every_kind_of_bad_token_is_rejected(case: str) -> None:
    """Covers: R-12-auth."""
    with pytest.raises(TokenRejected):
        validator().validate(BAD_TOKENS[case]())


def test_identity_from_claims_needs_a_known_role() -> None:
    with pytest.raises(TokenRejected):
        identity_from_claims({"sub": "x", "preferred_username": "s1", "realm_access": {"roles": ["admin"]}})


# ---------------------------------------------------------------------- the API

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("oidc")
    (root / "data").mkdir()
    write_people(root, with_emails(USERS), ENGAGEMENTS)
    rows = []
    for doc_id, rel, st, level, eng, eff, sup, body in DOCS:
        write_doc(root, rel, body)
        rows.append(dict(doc_id=doc_id, path=f"data/corpus/{rel}", source_type=st, title=doc_id,
                         version="1", effective_date=eff, supersedes=sup, access_level=level,
                         engagement_id=eng, synthetic="true"))
    write_manifest(root, rows)
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
    settings = _settings().model_copy(update={"auth_mode": "oidc", "oidc_issuer": ISSUER,
                                              "auth_rate_per_minute": 1000})
    assistant = AuditAssistant(AgentDeps(settings=settings, llm=FakeLLM(),
                                         searcher=make_searcher(indexer, top_k=20),
                                         reranker=FakeReranker(), sessions=indexer.sessions,
                                         clients=clients))
    with TestClient(create_app(Services(settings, assistant, indexer, ReadWriteLock()))) as c:
        c.app.state.oidc_validator = validator()
        c.indexer = indexer  # type: ignore[attr-defined]
        yield c


def bearer(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def test_invalid_expired_and_wrong_audience_tokens_are_rejected_and_registration_is_off(client) -> None:
    """Covers: R-12-auth. Through the API: only a valid realm token signs in; every other
    token gets the same generic 401; the email/password and registration routes do not
    exist when Keycloak is used, and registration is off by default in lite (tests/test_api.py)."""
    me = client.get("/api/v1/auth/me", headers=bearer(token("s1")))
    assert me.status_code == 200 and me.json()["user_id"] == "S1"
    for case in ("expired", "wrong audience", "signed by another key", "tampered payload", "garbage"):
        r = client.get("/api/v1/auth/me", headers=bearer(BAD_TOKENS[case]()))
        assert r.status_code == 401, case
        assert r.json()["detail"] == "Your sign-in is not valid. Sign in again."  # nothing more
    assert client.get("/api/v1/history", headers=bearer(BAD_TOKENS["expired"]())).status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401
    for path, body in (("/api/v1/auth/login", {"email": "s1@test.example", "password": "x" * 12}),
                       ("/api/v1/auth/register", {"name": "New", "email": "n@example-associates.test",
                                                  "password": "y" * 12}),
                       ("/api/v1/auth/forgot-password", {"email": "s1@test.example"})):
        assert client.post(path, json=body).status_code == 404, path
    config = client.get("/api/v1/auth/config").json()
    assert config == {"self_registration": False, "mode": "oidc", "oidc_issuer": ISSUER,
                      "oidc_client_id": "fabric-ui"}


def test_role_and_admin_flag_follow_the_identity_provider(client) -> None:
    """Keycloak decides role and admin: the users table follows, with an audit row;
    engagement membership stays in the database."""
    r = client.get("/api/v1/auth/me", headers=bearer(token("s2", ("manager", "admin"))))
    assert r.status_code == 200
    assert (r.json()["role"], r.json()["is_admin"]) == ("manager", True)
    with client.indexer.sessions() as s:
        assert (s.get(User, "S2").role, s.get(User, "S2").is_admin) == ("manager", True)
        synced = [a.target for a in s.scalars(select(AuditLog).where(AuditLog.action == "identity_synced"))]
    assert any(t.startswith("S2: role staff -> manager") for t in synced)
    back = client.get("/api/v1/auth/me", headers=bearer(token("s2", ("staff",)))).json()
    assert (back["role"], back["is_admin"]) == ("staff", False)


def test_a_valid_token_for_an_unknown_user_is_refused(client) -> None:
    r = client.get("/api/v1/auth/me", headers=bearer(token("x99", ("partner",))))
    assert r.status_code == 403
    with client.indexer.sessions() as s:
        assert s.get(User, "X99") is None  # never created from a token


def test_a_question_works_with_a_token(client) -> None:
    r = client.post("/api/v1/query", json={"text": "Within how many days must the file be assembled?"},
                    headers=bearer(token("s1")))
    assert r.status_code == 200 and "event: final" in r.text
