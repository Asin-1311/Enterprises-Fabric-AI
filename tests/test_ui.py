"""UI tests: the SSE parser, and the Streamlit app rendered headless against a fake API."""

import json

import httpx
import pytest

from ui.streamlit_app import iter_sse


def test_iter_sse_parses_events() -> None:
    lines = ["event: progress", 'data: {"step": "retrieving"}', "",
             ": ping comment", "event: token", 'data: {"text": "Hello "}', "",
             "event: final", 'data: {"status": "answered"}']
    assert list(iter_sse(lines)) == [("progress", {"step": "retrieving"}),
                                     ("token", {"text": "Hello "}),
                                     ("final", {"status": "answered"})]


def test_app_signs_in_then_shows_the_user_with_fake_api(monkeypatch: pytest.MonkeyPatch) -> None:
    from streamlit.testing.v1 import AppTest

    session = {"token": "tok-1", "expires_at": "2026-11-14T20:00:00Z",
               "user": {"user_id": "S01", "name": "Riya Kapoor", "role": "staff",
                        "email": "riya.kapoor@example-associates.test",
                        "engagements": ["Northwind Textiles Private Limited"]}}
    sent = []

    def fake_post(url, **kwargs):
        sent.append((url, kwargs.get("json")))
        assert url.endswith("/auth/login")
        return httpx.Response(200, content=json.dumps(session), request=httpx.Request("POST", url),
                              headers={"content-type": "application/json"})

    monkeypatch.setattr(httpx, "post", fake_post)
    from app.config import PROJECT_ROOT

    app_path = str(PROJECT_ROOT / "ui" / "streamlit_app.py")
    at = AppTest.from_file(app_path, default_timeout=30).run()
    assert not at.exception
    assert at.title[0].value == "Enterprise Fabric AI"
    assert [t.label for t in at.text_input] == ["Email", "Password"]  # sign-in comes first

    at.text_input[0].input("riya.kapoor@example-associates.test")
    at.text_input[1].input("demo-pass-123")
    at.button[0].click().run()
    assert not at.exception
    assert sent == [(sent[0][0], {"email": "riya.kapoor@example-associates.test",
                                  "password": "demo-pass-123"})]
    assert any("Riya Kapoor" in m.value for m in at.sidebar.markdown)
    assert any("Northwind" in m.value for m in at.sidebar.markdown)
