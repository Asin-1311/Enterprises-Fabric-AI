"""FastAPI serves the built React UI at "/" without hiding the API or /docs."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import create_app


def test_built_ui_is_served_at_root(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<title>Enterprise Fabric AI</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    client = TestClient(create_app(services=None, static_dir=tmp_path))  # no lifespan: no models
    assert "Enterprise Fabric AI" in client.get("/").text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/v1/health").json() == {"status": "ok"}  # API still wins
    assert client.get("/docs").status_code == 200


def test_without_a_build_only_the_api_is_served(tmp_path: Path) -> None:
    client = TestClient(create_app(services=None, static_dir=tmp_path / "missing"))
    assert client.get("/").status_code == 404
    assert client.get("/api/v1/health").status_code == 200
