"""A small JSON-over-HTTPS client shared by the SaaS connectors (Confluence, Jira,
SharePoint; M12c). Uses httpx, which the project already depends on.

- One base URL per source; a "next page" link is followed only if it points at that same
  host, so a token is never sent anywhere else.
- Credentials come from "env:NAME" references: basic auth (Atlassian: account email + API
  token) or a bearer token (Microsoft Graph).
- Any failure becomes a ConnectorError; the runner logs it and tries again next poll.
"""

from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.connectors.base import ConnectorError, resolve_secret

TIMEOUT_SECONDS = 20.0
# https, or a server on this machine (the mock SaaS server used by tests and the demo).
ALLOWED_BASE_URL_PREFIXES = ("https://", "http://127.0.0.1:", "http://localhost:")


class JsonClient:
    def __init__(self, base_url: str, config: dict[str, Any], auth: str,
                 transport: httpx.BaseTransport | None = None) -> None:
        if not base_url.startswith(ALLOWED_BASE_URL_PREFIXES):
            raise ConnectorError("base_url must be https:// (or a local test server)")
        self.base_url = base_url.rstrip("/")
        headers = {"Accept": "application/json"}
        basic = None
        token = resolve_secret(config.get("token"))
        if auth == "basic":
            basic = (str(config.get("user", "")), token or "")
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=self.base_url, headers=headers, auth=basic,
                                 timeout=TIMEOUT_SECONDS, follow_redirects=True,
                                 transport=transport)

    def _url(self, path_or_url: str) -> str:
        url = urljoin(self.base_url + "/", path_or_url)
        base = urlsplit(self.base_url)
        target = urlsplit(url)
        if (target.scheme, target.netloc) != (base.scheme, base.netloc):
            raise ConnectorError(f"refusing to follow a link to another host: {target.netloc}")
        return url

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        try:
            response = self.http.get(self._url(path_or_url), params=params)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"GET {path_or_url} failed: {exc}") from None
        if response.status_code >= 400:
            raise ConnectorError(f"GET {path_or_url}: HTTP {response.status_code}")
        return response

    def get_json(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            data = self.get(path_or_url, params).json()
        except ValueError:
            raise ConnectorError(f"GET {path_or_url}: the response is not JSON") from None
        if not isinstance(data, dict):
            raise ConnectorError(f"GET {path_or_url}: expected a JSON object")
        return data

    def close(self) -> None:
        self.http.close()
