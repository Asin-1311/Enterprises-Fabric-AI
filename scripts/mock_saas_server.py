"""A local stand-in for Confluence, Jira and SharePoint (Microsoft Graph), serving the
synthetic fixtures in tests/fixtures/connectors/ (M12c).

No live tenant of any of these products was used in this project: the connectors are
tested against this server only, and the report says so.

- Confluence  /wiki/api/v2/...       needs HTTP Basic auth
- Jira        /rest/api/3/search/jql needs HTTP Basic auth
- Graph       /v1.0/sites/...        needs a Bearer token
Requests without credentials get 401, as the real services do. Every request is
recorded (`requests`), so a contract test can check what the connector sent.
Tests can replace a fixture while the server runs (`set_fixture`) to simulate an edit.

Run it for the demo (Ctrl+C to stop):
    .venv\\Scripts\\python.exe -m scripts.mock_saas_server --port 8765
"""

import argparse
import json
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "connectors"

# (path pattern, required auth, fixture name built from the match and the query)
_ROUTES: list[tuple[re.Pattern[str], str, Any]] = [
    (re.compile(r"^/wiki/api/v2/spaces$"), "Basic", lambda m, q: "confluence/spaces.json"),
    (re.compile(r"^/wiki/api/v2/spaces/(\w+)/pages$"), "Basic",
     lambda m, q: f"confluence/pages_{q.get('cursor', '1')}.json"),
    (re.compile(r"^/wiki/api/v2/pages/(\w+)/labels$"), "Basic",
     lambda m, q: f"confluence/labels_{m.group(1)}.json"),
    (re.compile(r"^/rest/api/3/search/jql$"), "Basic",
     lambda m, q: f"jira/search_{q.get('nextPageToken', '1')}.json"),
    (re.compile(r"^/v1\.0/sites/([^/]+)/drive/root/delta$"), "Bearer",
     lambda m, q: f"sharepoint/delta_{q.get('token', '1')}.json"),
    (re.compile(r"^/v1\.0/sites/([^/]+)/drive/items/([\w-]+)/content$"), "Bearer",
     lambda m, q: f"sharepoint/items/{m.group(2)}.md"),
]


@dataclass
class Recorded:
    path: str
    query: dict[str, str]
    authorization: str


class MockSaaS:
    """The server; `start()` runs it in a thread on 127.0.0.1 (port 0 = any free port)."""

    def __init__(self, port: int = 0, fixtures: Path = FIXTURES) -> None:
        self.fixtures, self.requests = fixtures, []
        self.overrides: dict[str, Any] = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def set_fixture(self, name: str, content: Any) -> None:
        """Serve `content` (a dict for JSON, a str for files) instead of the fixture file."""
        self.overrides[name] = content

    def _content(self, name: str) -> tuple[bytes, str] | None:
        if name in self.overrides:
            data = self.overrides[name]
        else:
            path = (self.fixtures / name).resolve()
            if self.fixtures.resolve() not in path.parents or not path.is_file():
                return None
            data = path.read_text(encoding="utf-8")
            data = json.loads(data) if name.endswith(".json") else data
        if isinstance(data, (dict, list)):
            text, kind = json.dumps(data), "application/json"
        else:
            text, kind = str(data), "text/markdown; charset=utf-8"
        return text.replace("{base}", self.base_url).encode("utf-8"), kind

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # keep test output quiet
                pass

            def _send(self, status: int, body: bytes, kind: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 (http.server naming)
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                auth = self.headers.get("Authorization", "")
                mock.requests.append(Recorded(parts.path, query, auth))
                for pattern, scheme, fixture in _ROUTES:
                    match = pattern.match(parts.path)
                    if match:
                        if not auth.startswith(scheme + " "):
                            return self._send(401, b'{"message": "Unauthorized"}')
                        found = mock._content(fixture(match, query))
                        if found is None:  # e.g. a page without a labels fixture
                            return self._send(200, b'{"results": [], "_links": {}}') \
                                if "labels" in parts.path else self._send(404, b"{}")
                        return self._send(200, *found)
                self._send(404, b'{"message": "Not found"}')

        return Handler

    def start(self) -> "MockSaaS":
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local mock of Confluence, Jira and Graph.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    mock = MockSaaS(args.port)
    print(f"Mock Confluence/Jira/SharePoint on {mock.base_url} (synthetic fixtures). Ctrl+C stops.")
    try:
        mock.server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        mock.server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
