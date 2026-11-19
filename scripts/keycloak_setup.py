"""Finish the Keycloak demo realm after it is imported (M13b).

    .venv\\Scripts\\python.exe -m scripts.keycloak_setup

Waits until Keycloak serves the `fabric` realm, then signs in as the bootstrap admin
(KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD from .env) and sets every synthetic user's
password to KEYCLOAK_DEMO_PASSWORD (from .env). Passwords are never in git: the realm file
holds none. Re-running sets the same password again.
"""

import sys
import time

import httpx

from app.config import env_reference

KEYCLOAK_URL = "http://127.0.0.1:8180"
REALM = "fabric"


def wait_for_realm(base: str = KEYCLOAK_URL, seconds: float = 180.0) -> None:
    deadline = time.monotonic() + seconds
    url = f"{base}/realms/{REALM}/.well-known/openid-configuration"
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    raise RuntimeError(f"Keycloak did not serve realm {REALM} at {base} within {seconds:.0f} s")


def admin_token(base: str = KEYCLOAK_URL) -> str:
    response = httpx.post(f"{base}/realms/master/protocol/openid-connect/token", timeout=10, data={
        "grant_type": "password", "client_id": "admin-cli",
        "username": env_reference("env:KEYCLOAK_ADMIN") or "admin",
        "password": env_reference("env:KEYCLOAK_ADMIN_PASSWORD") or ""})
    response.raise_for_status()
    return response.json()["access_token"]


def set_demo_passwords(base: str = KEYCLOAK_URL) -> int:
    password = env_reference("env:KEYCLOAK_DEMO_PASSWORD")
    if not password:
        raise RuntimeError("Set KEYCLOAK_DEMO_PASSWORD in .env (see .env.example)")
    headers = {"Authorization": f"Bearer {admin_token(base)}"}
    with httpx.Client(base_url=f"{base}/admin/realms/{REALM}", headers=headers, timeout=10) as http:
        users = http.get("/users", params={"max": 500}).raise_for_status().json()
        for user in users:
            http.put(f"/users/{user['id']}/reset-password", json={
                "type": "password", "value": password, "temporary": False}).raise_for_status()
    return len(users)


def main() -> int:
    wait_for_realm()
    count = set_demo_passwords()
    print(f"Keycloak realm {REALM}: demo password set for {count} synthetic users.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
