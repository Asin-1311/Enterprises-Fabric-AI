"""Get a Keycloak access token for a synthetic user (tests and demo only; M13b).

    .venv\\Scripts\\python.exe -m scripts.keycloak_token S01
    curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/api/v1/auth/me

Uses the dev-only `fabric-cli` client's password grant with KEYCLOAK_DEMO_PASSWORD from
.env. The browser signs in with the authorization code flow instead (fabric-ui, M16).
"""

import sys

import httpx

from app.config import env_reference

KEYCLOAK_URL = "http://127.0.0.1:8180"


def get_token(user_id: str, base: str = KEYCLOAK_URL, client_id: str = "fabric-cli",
              password: str | None = None) -> str:
    response = httpx.post(f"{base}/realms/fabric/protocol/openid-connect/token", timeout=10, data={
        "grant_type": "password", "client_id": client_id, "scope": "openid",
        "username": user_id.lower(),
        "password": password if password is not None else (env_reference("env:KEYCLOAK_DEMO_PASSWORD") or "")})
    response.raise_for_status()
    return response.json()["access_token"]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m scripts.keycloak_token <user id, e.g. S01>")
        return 2
    print(get_token(args[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
