"""SharePoint connector: Markdown files in a site's document library, through Microsoft
Graph (CLAUDE.md section 6, M12c).

connector_config: {"base_url": "https://graph.microsoft.com", "site": "<site id>",
                   "token": "env:NAME", "defaults": {...}}

Written against the documented Microsoft Graph v1.0 drive API and tested only against
recorded SYNTHETIC responses served by a local mock server (tests/fixtures/connectors/):
no live SharePoint tenant was used. Obtaining the bearer token (an Entra ID app
registration and its client-credentials flow) is outside this project; the token is read
from .env.

- GET /v1.0/sites/<site>/drive/root/delta        -> every item in the library; follows
  `@odata.nextLink`. (Each poll lists from the start: the snapshot comparison finds the
  changes, so the delta link is not kept.)
- GET /v1.0/sites/<site>/drive/items/<id>/content -> the file's bytes

An item's id is its key (a rename keeps the same document); its cTag, which changes only
when the content changes, is the version token.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.connectors.base import RemoteItem, SnapshotConnector
from app.connectors.http import JsonClient


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


class SharePointConnector(SnapshotConnector):
    def __init__(self, source_id: str, config: dict[str, Any], landing_dir: Path,
                 transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(source_id, config, landing_dir)
        self.site = str(config["site"])
        self.api = JsonClient(str(config["base_url"]), config, auth="bearer", transport=transport)

    def list_items(self) -> list[RemoteItem]:
        items: list[RemoteItem] = []
        url: str | None = f"/v1.0/sites/{self.site}/drive/root/delta"
        while url:
            data = self.api.get_json(url)
            for entry in data.get("value") or []:
                name = str(entry.get("name", ""))
                if "file" not in entry or "deleted" in entry or not name.lower().endswith(self.suffixes):
                    continue
                items.append(RemoteItem(
                    key=str(entry["id"]), version=str(entry.get("cTag") or entry["eTag"]),
                    title=None, modified=_when(entry.get("lastModifiedDateTime")),
                    extra={"name": name}))
            url = data.get("@odata.nextLink")
        return items

    def fetch(self, item: RemoteItem) -> str | None:
        response = self.api.get(f"/v1.0/sites/{self.site}/drive/items/{item.key}/content")
        return response.content.decode("utf-8", "replace")
