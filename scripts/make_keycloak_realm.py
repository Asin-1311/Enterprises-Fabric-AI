"""Write the SYNTHETIC Keycloak realm for the full profile (M13b).

    .venv\\Scripts\\python.exe -m scripts.make_keycloak_realm

Writes deploy/keycloak/realm.json from data/users.csv, so the realm and the application's
users can never drift apart. Keycloak imports it on first start (deploy/compose.yaml).

Realm `fabric`:
- realm roles staff, manager, partner (one per user, from users.csv) and admin (users
  with is_admin=true; grants the admin console, NO extra document access);
- users: username = the synthetic user id in lower case (Keycloak lower-cases usernames;
  the API compares ids case-insensitively), name and email from users.csv. NO passwords:
  scripts/keycloak_setup.py sets them from KEYCLOAK_DEMO_PASSWORD in .env;
- clients:
  - fabric-api: the API, only an audience (no sign-in flows of its own);
  - fabric-ui: the browser app, public, authorization code with PKCE (S256); used by the
    React sign-in in M16;
  - fabric-cli: for tests and the demo token script only, with the password grant
    (direct access grants). The password grant is discouraged for real users; a
    production realm would not have this client.
  Tokens for fabric-ui and fabric-cli carry `aud: fabric-api` (an audience mapper), and
  the realm's roles in `realm_access.roles`.
- brute-force protection: 10 failures lock the account for up to 15 minutes, the same
  limits as the lite sign-in (CLAUDE.md section 10); access tokens live 5 minutes.
"""

import csv
import json
import sys

from app.config import PROJECT_ROOT

USERS = PROJECT_ROOT / "data" / "users.csv"
REALM = PROJECT_ROOT / "deploy" / "keycloak" / "realm.json"
LOCAL_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000",
                 "http://localhost:8000"]


def audience_mapper() -> dict:
    return {"name": "audience fabric-api", "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper", "consentRequired": False,
            "config": {"included.client.audience": "fabric-api", "access.token.claim": "true",
                       "id.token.claim": "false", "introspection.token.claim": "true"}}


def realm(users: list[dict[str, str]]) -> dict:
    return {
        "realm": "fabric",
        "displayName": "Example & Associates (SYNTHETIC demo realm)",
        "enabled": True,
        "sslRequired": "external",   # plain http only from this machine / private networks
        "registrationAllowed": False,  # accounts are created by the firm, not by users
        "resetPasswordAllowed": False,
        "bruteForceProtected": True,
        "failureFactor": 10,
        "waitIncrementSeconds": 60,
        "maxFailureWaitSeconds": 900,
        "accessTokenLifespan": 300,
        "roles": {"realm": [
            {"name": "staff", "description": "Article assistant / staff level"},
            {"name": "manager", "description": "Engagement manager level"},
            {"name": "partner", "description": "Partner level: every engagement"},
            {"name": "admin", "description": "Admin console; no extra document access"},
        ]},
        "clients": [
            {"clientId": "fabric-api", "name": "Enterprise Fabric AI API", "enabled": True,
             "publicClient": False, "bearerOnly": False, "standardFlowEnabled": False,
             "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False},
            {"clientId": "fabric-ui", "name": "Enterprise Fabric AI (browser)", "enabled": True,
             "publicClient": True, "standardFlowEnabled": True, "implicitFlowEnabled": False,
             "directAccessGrantsEnabled": False,
             "redirectUris": [f"{o}/*" for o in LOCAL_ORIGINS], "webOrigins": LOCAL_ORIGINS,
             "attributes": {"pkce.code.challenge.method": "S256",
                            "post.logout.redirect.uris": "+"},
             "protocolMappers": [audience_mapper()]},
            {"clientId": "fabric-cli", "name": "Tests and demo token script (dev only)",
             "enabled": True, "publicClient": True, "standardFlowEnabled": False,
             "directAccessGrantsEnabled": True, "protocolMappers": [audience_mapper()]},
        ],
        "users": [
            {"username": u["user_id"].lower(), "enabled": True, "emailVerified": True,
             "email": u["email"], "firstName": u["name"].split(" ", 1)[0],
             "lastName": u["name"].split(" ", 1)[-1],
             "realmRoles": [u["role"], *(["admin"] if u.get("is_admin") == "true" else [])]}
            for u in users
        ],
    }


def main() -> int:
    with USERS.open(newline="", encoding="utf-8") as f:
        users = list(csv.DictReader(f))
    REALM.parent.mkdir(parents=True, exist_ok=True)
    REALM.write_text(json.dumps(realm(users), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {REALM.relative_to(PROJECT_ROOT)} ({len(users)} users, no passwords).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
