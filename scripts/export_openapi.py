"""Write the API's OpenAPI schema to docs/api/openapi.json (the committed API reference).

    .venv\\Scripts\\python.exe -m scripts.export_openapi

tests/test_api_contract.py fails when the live API and this file differ, so every API
change is deliberate: change the code, re-run this script, review the diff, commit both.
"""

import json
from pathlib import Path

from app.api.main import create_app
from app.config import PROJECT_ROOT

OPENAPI_FILE = PROJECT_ROOT / "docs" / "api" / "openapi.json"


def current_schema() -> dict:
    """The schema FastAPI generates from the code (no services or models are started)."""
    return create_app(services=None, static_dir=None).openapi()


def write(path: Path = OPENAPI_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    out = write()
    print(f"Wrote {out.relative_to(PROJECT_ROOT)} ({len(current_schema()['paths'])} paths).")
