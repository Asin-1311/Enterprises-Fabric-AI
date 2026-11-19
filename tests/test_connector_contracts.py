"""M12c: contract tests for the Confluence, Jira and SharePoint connectors.

Each connector is run against scripts/mock_saas_server.py, which serves the SYNTHETIC
fixtures in tests/fixtures/connectors/ in the documented response shapes of Confluence
Cloud REST v2, Jira Cloud REST v3 and Microsoft Graph v1.0. The tests check both sides of
the contract: the requests the connector sends (paths, parameters, paging, credentials)
and how it reads the responses. No live tenant was used (CLAUDE.md section 6).

Covers: R-7.1-c, R-13.3-contract, R-5-ing.
"""

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.adapters.lite.events import InProcessEventBus
from app.connectors import build_connector
from app.connectors.base import ConnectorError
from app.connectors.runner import ConnectorRunner
from app.db.models import Document, Source
from app.ingest.md_loader import SYNTHETIC_BANNER
from app.ingest.worker import IngestWorker
from scripts.mock_saas_server import FIXTURES, MockSaaS
from tests.helpers import make_indexer, write_manifest, write_people
from tests.test_agents import ENGAGEMENTS, USERS

SITE = "example.sharepoint.com,1111,2222"
DEFAULTS = {"source_type": "firm_policy", "access_level": "staff"}


@pytest.fixture()
def mock(monkeypatch):
    monkeypatch.setenv("TEST_ATLASSIAN_TOKEN", "atl-token")
    monkeypatch.setenv("TEST_GRAPH_TOKEN", "graph-token")
    server = MockSaaS().start()
    yield server
    server.stop()


def configs(base_url: str) -> dict[str, tuple[str, dict]]:
    atlassian = {"base_url": base_url, "user": "kb-reader@example-associates.test",
                 "token": "env:TEST_ATLASSIAN_TOKEN"}
    return {
        "conf-aud": ("confluence", atlassian | {"space": "AUD", "defaults": DEFAULTS}),
        "jira-audq": ("jira", atlassian | {"project": "AUDQ",
                                           "defaults": {"source_type": "faq", "access_level": "staff"}}),
        "sp-policies": ("sharepoint", {"base_url": base_url, "site": SITE, "token": "env:TEST_GRAPH_TOKEN",
                                       "defaults": DEFAULTS}),
    }


def connector(mock: MockSaaS, source_id: str, tmp_path: Path):
    kind, cfg = configs(mock.base_url)[source_id]
    return build_connector(source_id, kind, cfg, landing_dir=tmp_path, work_dir=tmp_path,
                           project_root=tmp_path)


def _landing(tmp_path: Path, event) -> str:
    return (tmp_path / event.uri.removeprefix("landing:")).read_text(encoding="utf-8")


# -------------------------------------------------------------------- Confluence

def test_confluence_contract(mock, tmp_path: Path) -> None:
    """Covers: R-7.1-c, R-13.3-contract (Confluence Cloud REST v2 fixtures)."""
    events, state = connector(mock, "conf-aud", tmp_path).poll({})

    sent = [(r.path, r.query) for r in mock.requests]
    assert sent[0] == ("/wiki/api/v2/spaces", {"keys": "AUD"})
    assert sent[1] == ("/wiki/api/v2/spaces/98001/pages", {"body-format": "storage", "limit": "50"})
    assert ("/wiki/api/v2/spaces/98001/pages",
            {"cursor": "c2", "limit": "50", "body-format": "storage"}) in sent  # followed `next`
    expected = "Basic " + base64.b64encode(b"kb-reader@example-associates.test:atl-token").decode()
    assert {r.authorization for r in mock.requests} == {expected}

    by_id = {e.doc_id: e for e in events}
    assert set(by_id) == {"conf-aud-page-1001", "conf-aud-page-1002", "conf-aud-page-1003"}  # archived skipped
    page = by_id["conf-aud-page-1001"]
    assert page.metadata["title"] == "Engagement file naming convention"
    assert page.metadata["version"] == "3" and page.metadata["effective_date"] == "2026-10-02"
    assert by_id["conf-aud-page-1002"].metadata["access_level"] == "manager"   # label
    assert by_id["conf-aud-page-1003"].metadata["source_type"] == "calendar"   # label
    text = _landing(tmp_path, page)
    assert text.splitlines()[0] == SYNTHETIC_BANNER
    assert "## 2. Pattern" in text and "| INV | Inventory |" in text  # headings, table kept

    # An edited page (new version number) is the only one fetched and sent again.
    data = json.loads((FIXTURES / "confluence" / "pages_1.json").read_text(encoding="utf-8"))
    data["results"][1]["version"]["number"] = 2
    mock.set_fixture("confluence/pages_1.json", data)
    events, _ = connector(mock, "conf-aud", tmp_path).poll(state)
    assert [e.doc_id for e in events] == ["conf-aud-page-1002"]


# -------------------------------------------------------------------------- Jira

def test_jira_contract(mock, tmp_path: Path) -> None:
    """Covers: R-7.1-c, R-13.3-contract (Jira Cloud REST v3 issue search fixtures)."""
    events, _ = connector(mock, "jira-audq", tmp_path).poll({})

    first, second = mock.requests[0], mock.requests[1]
    assert first.path == "/rest/api/3/search/jql"
    assert first.query == {"jql": 'project = "AUDQ" ORDER BY key',
                           "fields": "summary,description,updated,labels", "maxResults": "50"}
    assert second.query["nextPageToken"] == "p2"  # token paging until isLast
    assert all(r.authorization.startswith("Basic ") for r in mock.requests)

    by_id = {e.doc_id: e for e in events}
    assert set(by_id) == {"jira-audq-AUDQ-1", "jira-audq-AUDQ-2"}
    one = by_id["jira-audq-AUDQ-1"]
    assert one.metadata["title"].startswith("AUDQ-1: Which template")
    assert one.metadata["source_type"] == "faq" and one.metadata["effective_date"] == "2026-10-20"
    assert by_id["jira-audq-AUDQ-2"].metadata["access_level"] == "manager"
    text = _landing(tmp_path, one)
    assert text.splitlines()[0] == SYNTHETIC_BANNER
    assert "## Answer from the technical desk" in text and "bank branch file index template" in text


# -------------------------------------------------------------------- SharePoint

def test_sharepoint_contract(mock, tmp_path: Path) -> None:
    """Covers: R-7.1-c, R-13.3-contract (Microsoft Graph v1.0 drive delta fixtures)."""
    events, _ = connector(mock, "sp-policies", tmp_path).poll({})

    paths = [r.path for r in mock.requests]
    assert paths[0] == f"/v1.0/sites/{SITE}/drive/root/delta"
    assert mock.requests[1].query == {"token": "p2"}  # followed @odata.nextLink
    assert f"/v1.0/sites/{SITE}/drive/items/01AAA/content" in paths
    assert {r.authorization for r in mock.requests} == {"Bearer graph-token"}

    # Folders, the .docx file and the deleted entry are not documents.
    assert sorted(e.doc_id for e in events) == ["sp-policies-01AAA", "sp-policies-01BBB"]
    by_id = {e.doc_id: e for e in events}
    assert by_id["sp-policies-01AAA"].metadata["title"] == "Independence declaration process"
    assert by_id["sp-policies-01BBB"].metadata["effective_date"] == "2026-10-12"
    assert "## 2. Changes during the year" in _landing(tmp_path, by_id["sp-policies-01AAA"])


# --------------------------------------------------------- failures and safety

def test_missing_credentials_and_foreign_links_are_refused(mock, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TEST_GRAPH_TOKEN")
    with pytest.raises(ConnectorError, match="401"):  # the server refuses: no bearer token
        connector(mock, "sp-policies", tmp_path).poll({})
    monkeypatch.setenv("TEST_GRAPH_TOKEN", "graph-token")
    # A next-page link to another host is never followed (the token would go with it).
    mock.set_fixture("sharepoint/delta_1.json", {"value": [],
                                                 "@odata.nextLink": "https://attacker.example/steal"})
    with pytest.raises(ConnectorError, match="another host"):
        connector(mock, "sp-policies", tmp_path).poll({})
    with pytest.raises(ConnectorError):
        build_connector("x", "confluence", {"base_url": "http://intranet.example", "space": "A"},
                        landing_dir=tmp_path, work_dir=tmp_path, project_root=tmp_path)


# ----------------------------------------------- end to end: worker and indexes

def test_confluence_jira_and_sharepoint_fixtures_flow_into_every_index(mock, tmp_path: Path) -> None:
    """Covers: R-7.1-c, R-5-ing. Registered sources of all three types are polled by the
    runner; the worker enriches and indexes every document in SQLite, Qdrant and BM25 with
    the source's owner and classification, and the label-raised access levels hold."""
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    write_people(root, USERS, ENGAGEMENTS)
    write_manifest(root, [])
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        for sid, (kind, cfg) in configs(mock.base_url).items():
            s.add(Source(id=sid, type=kind, connector_config=cfg, owner="M2",
                         classification="public_internal", status="active"))
        s.commit()
    bus = InProcessEventBus()
    worker = IngestWorker(indexer, bus)
    runner = ConnectorRunner(indexer.sessions, bus, SimpleNamespace(), factory=lambda src: build_connector(
        src.id, src.type, src.connector_config, landing_dir=indexer.landing_dir,
        work_dir=root / "data" / "connectors", project_root=root))

    assert runner.poll_once() == {"conf-aud": 3, "jira-audq": 2, "sp-policies": 2}
    outcomes = [o for _, o in worker.drain()]
    assert outcomes == ["indexed"] * 7

    with indexer.sessions() as s:
        docs = {d.doc_id: d for d in s.scalars(select(Document))}
    assert len(docs) == 7 and {d.owner for d in docs.values()} == {"M2"}
    assert docs["conf-aud-page-1002"].access_level == "manager"
    assert docs["conf-aud-page-1002"].classification == "confidential"  # floor above staff
    assert docs["jira-audq-AUDQ-1"].source_type == "faq"
    for doc_id in docs:
        assert indexer.store.count(doc_id) > 0
        assert {p["source_id"] for p in indexer.store.payloads(doc_id)} == {docs[doc_id].source_id}
    assert [c for c, _ in indexer.bm25.search("archive checklist")][0].startswith("jira-audq-AUDQ-2:")
    assert runner.poll_once() == {"conf-aud": 0, "jira-audq": 0, "sp-policies": 0}
