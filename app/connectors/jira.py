"""Jira connector: the issues of one project, e.g. the firm's technical-query log
(CLAUDE.md section 6, M12c).

connector_config: {"base_url": "https://<site>.atlassian.net", "project": "<key>",
                   "user": "<account email>", "token": "env:NAME", "defaults": {...}}

Written against the documented Jira Cloud REST API v3 issue search and tested only against
recorded SYNTHETIC responses served by a local mock server (tests/fixtures/connectors/):
no live Jira tenant was used.

- GET /rest/api/3/search/jql?jql=project = "<key>" ORDER BY key
      &fields=summary,description,updated,labels&maxResults=50
  follows `nextPageToken` until `isLast`.

Each issue becomes one document: "# <KEY>: <summary>" followed by its description
(Atlassian Document Format, converted to Markdown). The `updated` timestamp is the
version token; labels can override access (see app/connectors/base.py).
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.connectors.base import ConnectorError, RemoteItem, SnapshotConnector
from app.connectors.http import JsonClient
from app.connectors.markup import adf_to_markdown

PAGE_SIZE = 50
_PROJECT = re.compile(r"^[A-Z][A-Z0-9_]{0,19}$")


def _when(text: str | None) -> datetime | None:
    try:  # Jira writes "2026-11-10T09:30:00.000+0530"
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f%z") if text else None
    except ValueError:
        return None


class JiraConnector(SnapshotConnector):
    def __init__(self, source_id: str, config: dict[str, Any], landing_dir: Path,
                 transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(source_id, config, landing_dir)
        self.project = str(config["project"])
        if not _PROJECT.match(self.project):  # it goes into a JQL query: no free text
            raise ConnectorError(f"invalid Jira project key '{self.project}'")
        self.api = JsonClient(str(config["base_url"]), config, auth="basic", transport=transport)

    def list_items(self) -> list[RemoteItem]:
        items: list[RemoteItem] = []
        params: dict[str, Any] = {"jql": f'project = "{self.project}" ORDER BY key',
                                  "fields": "summary,description,updated,labels",
                                  "maxResults": PAGE_SIZE}
        while True:
            data = self.api.get_json("/rest/api/3/search/jql", params)
            for issue in data.get("issues") or []:
                fields = issue.get("fields") or {}
                if not fields.get("updated"):
                    raise ConnectorError(f"issue {issue.get('key')} has no 'updated' field")
                items.append(RemoteItem(
                    key=str(issue["key"]), version=str(fields["updated"]),
                    title=f"{issue['key']}: {fields.get('summary', '')}".strip(),
                    modified=_when(fields.get("updated")),
                    labels=tuple(str(lb) for lb in fields.get("labels") or []),
                    extra={"description": fields.get("description")}))
            if data.get("isLast", True) or not data.get("nextPageToken"):
                return items
            params = {**params, "nextPageToken": data["nextPageToken"]}

    def fetch(self, item: RemoteItem) -> str | None:
        body = adf_to_markdown((item.extra or {}).get("description"))
        return f"# {item.title}\n\n{body}"
