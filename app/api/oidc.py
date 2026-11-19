"""OIDC sign-in for the full profile: Keycloak-issued JWTs (spec sections 6 and 12;
CLAUDE.md section 10; Gap Register R-12-auth; M13b).

Every request carries `Authorization: Bearer <access token>` issued by the Keycloak realm
`fabric` (deploy/keycloak/realm.json). `OidcValidator.validate` accepts a token only if:
- it is signed by one of the realm's current keys (fetched from its JWKS URL and cached),
  with an asymmetric algorithm (RS256/ES256 ...; "none" and HMAC are refused, so a token
  cannot be forged with the public key as an HMAC secret);
- the issuer is the configured realm, the audience includes `fabric-api`, and it is not
  expired (exp), not used before its time (nbf) and was issued (iat), with a small clock
  leeway;
- it names a user (`preferred_username`, which is the synthetic user id) and exactly the
  roles the realm grants (`realm_access.roles`).
Anything else is rejected, and the client only ever sees a generic 401.

What the token decides, and what the database decides:
- the user's role (staff < manager < partner: the highest realm role) and the `admin`
  flag come from Keycloak, the identity provider. The users table mirrors them: a
  difference is written back and recorded in audit_log (`identity_synced`);
- engagement membership stays in the database (data/engagements.csv), because access to
  client files is an engagement-team decision, not an identity attribute;
- a valid token for a user the application does not know gets 403: accounts are not
  created from tokens.
"""

from dataclasses import dataclass
from typing import Any, Protocol

import jwt

from app.retrieval.acl import ROLE_LEVELS

ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384"]
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]


class TokenRejected(Exception):
    """The token is not acceptable (the reason is logged, never sent to the client)."""


class IdentityProviderUnavailable(Exception):
    """The realm's signing keys could not be fetched: answer 503, not 401."""


@dataclass(frozen=True)
class Identity:
    user_id: str
    role: str
    is_admin: bool
    subject: str


class KeyResolver(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


def identity_from_claims(claims: dict[str, Any]) -> Identity:
    """The user id, role and admin flag a validated token states."""
    user_id = str(claims.get("user_id") or claims.get("preferred_username") or "").strip().upper()
    if not user_id:
        raise TokenRejected("no user name in the token")
    roles = set((claims.get("realm_access") or {}).get("roles") or [])
    granted = [r for r in ROLE_LEVELS if r in roles]
    if not granted:
        raise TokenRejected(f"{user_id}: no staff/manager/partner role in the token")
    role = max(granted, key=lambda r: ROLE_LEVELS[r])
    return Identity(user_id=user_id, role=role, is_admin="admin" in roles,
                    subject=str(claims["sub"]))


class OidcValidator:
    def __init__(self, issuer: str, audience: str, jwks_url: str | None = None,
                 leeway_seconds: int = 30, keys: KeyResolver | None = None) -> None:
        self.issuer, self.audience, self.leeway = issuer.rstrip("/"), audience, leeway_seconds
        self.keys = keys or jwt.PyJWKClient(
            jwks_url or f"{self.issuer}/protocol/openid-connect/certs",
            cache_keys=True, lifespan=300, timeout=5)

    def validate(self, token: str) -> Identity:
        """The identity in a valid token; raises TokenRejected or IdentityProviderUnavailable."""
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
            if algorithm not in ALLOWED_ALGORITHMS:
                raise TokenRejected(f"algorithm {algorithm!r} is not allowed")
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=ALLOWED_ALGORITHMS,
                                audience=self.audience, issuer=self.issuer,
                                leeway=self.leeway, options={"require": REQUIRED_CLAIMS})
        except jwt.PyJWKClientConnectionError as exc:
            raise IdentityProviderUnavailable(str(exc)) from None
        except jwt.PyJWTError as exc:
            raise TokenRejected(f"{type(exc).__name__}: {exc}") from None
        return identity_from_claims(claims)
