"""Confluence connector: the pages of one space (CLAUDE.md section 6, M12c).

connector_config: {"base_url": "https://<site>.atlassian.net", "space": "<space key>",
                   "user": "<account email>", "token": "env:NAME", "defaults": {...}}

Written against the documented Confluence Cloud REST API v2 and tested only against
recorded SYNTHETIC responses served by a local mock server (tests/fixtures/connectors/):
no live Confluence tenant was used.

- GET /wiki/api/v2/spaces?keys=<space>                      -> the space id
- GET /wiki/api/v2/spaces/<id>/pages?body-format=storage    -> pages with their body;
  follows `_links.next` (cursor paging)
- GET /wiki/api/v2/pages/<page id>/labels                   -> labels (access overrides)

A page's version number is its version token. The body (XHTML "storage" format) becomes
Markdown; the page title becomes the document title.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.connectors.base import ConnectorError, RemoteItem, SnapshotConnector
from app.connectors.http import JsonClient
from app.connectors.markup import html_to_markdown

PAGE_SIZE = 50


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


class ConfluenceConnector(SnapshotConnector):
    def __init__(self, source_id: str, config: dict[str, Any], landing_dir: Path,
                 transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(source_id, config, landing_dir)
        self.space = str(config["space"])
        self.api = JsonClient(str(config["base_url"]), config, auth="basic", transport=transport)

    def _space_id(self) -> str:
        found = self.api.get_json("/wiki/api/v2/spaces", {"keys": self.space}).get("results") or []
        if not found:
            raise ConnectorError(f"Confluence space '{self.space}' not found")
        return str(found[0]["id"])

    def list_items(self) -> list[RemoteItem]:
        items: list[RemoteItem] = []
        url: str | None = f"/wiki/api/v2/spaces/{self._space_id()}/pages"
        params: dict[str, Any] | None = {"body-format": "storage", "limit": PAGE_SIZE}
        while url:
            data = self.api.get_json(url, params)
            for page in data.get("results") or []:
                if page.get("status", "current") != "current":
                    continue
                labels = self.api.get_json(f"/wiki/api/v2/pages/{page['id']}/labels")
                items.append(RemoteItem(
                    key=f"page-{page['id']}", version=str(page["version"]["number"]),
                    title=page.get("title"), modified=_when(page["version"].get("createdAt")),
                    labels=tuple(str(lb.get("name", "")) for lb in labels.get("results") or []),
                    extra={"html": ((page.get("body") or {}).get("storage") or {}).get("value", "")}))
            url, params = (data.get("_links") or {}).get("next"), None  # next has the cursor
        return items

    def fetch(self, item: RemoteItem) -> str | None:
        return html_to_markdown(str((item.extra or {}).get("html", "")))
