"""Register (or update) the Debezium connector for the synthetic DMS (M12d).

    .venv\\Scripts\\python.exe -m scripts.register_debezium_connector

Sends deploy/debezium/dms-connector.json to the Kafka Connect REST API (127.0.0.1:8083,
the `debezium` service in deploy/compose.yaml) as connector `dms-documents`, then waits
until the connector and its task report RUNNING. Idempotent: running it again applies the
same settings. The file holds no secret: the database user and password are read from
the container's environment (${env:DMS_DB_USER}, ${env:DMS_DB_PASSWORD}).
"""

import json
import sys
import time
from pathlib import Path

import httpx

CONNECT_URL = "http://127.0.0.1:8083"
NAME = "dms-documents"
CONFIG = Path(__file__).resolve().parents[1] / "deploy" / "debezium" / "dms-connector.json"


def register(connect_url: str = CONNECT_URL, wait_seconds: float = 60.0) -> dict:
    """PUT the connector config and wait for RUNNING. Returns the final status."""
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    with httpx.Client(base_url=connect_url, timeout=30) as http:
        http.put(f"/connectors/{NAME}/config", json=config).raise_for_status()
        deadline = time.monotonic() + wait_seconds
        status: dict = {}
        while time.monotonic() < deadline:
            response = http.get(f"/connectors/{NAME}/status")
            if response.status_code == 200:
                status = response.json()
                tasks = status.get("tasks") or []
                if status["connector"]["state"] == "RUNNING" and tasks \
                        and all(t["state"] == "RUNNING" for t in tasks):
                    return status
                if any(t["state"] == "FAILED" for t in tasks):
                    break
            time.sleep(2)
    raise RuntimeError(f"connector {NAME} is not running: {json.dumps(status)[:1500]}")


def main() -> int:
    status = register()
    print(f"Debezium connector {NAME}: {status['connector']['state']} "
          f"({len(status['tasks'])} task(s) running).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
