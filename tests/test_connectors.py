"""M12c: the connector framework, the git, S3 and filesystem connectors, and the runner
(no servers: a real local Git repository, a fake S3 client, the lite indexes).

Covers: R-5-ing (one test per connector), R-7.1-c (git and S3 here; Confluence, Jira and
SharePoint in tests/test_connector_contracts.py).
"""

import io
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.adapters.lite.events import InProcessEventBus
from app.connectors import build_connector, config_problems
from app.connectors.base import (
    ConnectorError,
    RemoteItem,
    SnapshotConnector,
    banner_first,
    label_overrides,
    make_doc_id,
)
from app.connectors.filesystem import FilesystemConnector
from app.connectors.git import check_git_config
from app.connectors.runner import ConnectorRunner
from app.db.models import Chunk, Document, Source
from app.ingest.md_loader import SYNTHETIC_BANNER
from app.ingest.worker import IngestWorker
from scripts.make_synthetic_git_repo import git, make_repo, write_docs
from tests.helpers import make_indexer, write_manifest, write_people
from tests.test_agents import ENGAGEMENTS, USERS

DEFAULTS = {"source_type": "firm_policy", "access_level": "staff"}
DOC_A = "# Alpha procedure\n\n## 1. Scope\nAlpha applies to every quokkaword file.\n"
DOC_B = "# Beta procedure\n\n## 1. Steps\nBeta steps for wombatword files.\n"


# ------------------------------------------------------------------- shared base

class FakeConnector(SnapshotConnector):
    """A source whose listing the test controls; counts fetches."""

    def __init__(self, landing: Path, items: dict[str, tuple[str, str]]) -> None:
        super().__init__("fake", {"defaults": DEFAULTS}, landing)
        self.items, self.fetched = items, []  # key -> (version, text)

    def list_items(self) -> list[RemoteItem]:
        return [RemoteItem(key=k, version=v, labels=("access-manager",) if "mgr" in k else ())
                for k, (v, _) in self.items.items()]

    def fetch(self, item: RemoteItem) -> str | None:
        self.fetched.append(item.key)
        return None if item.key.endswith(".bin") else self.items[item.key][1]


def test_snapshot_poll_sends_only_what_changed(tmp_path: Path) -> None:
    """Covers: R-5-ing (new, changed, unchanged, removed and unsupported items)."""
    c = FakeConnector(tmp_path, {"a.md": ("v1", DOC_A), "mgr/b.md": ("v1", DOC_B), "x.bin": ("v1", "")})
    events, state = c.poll({})
    assert sorted(e.doc_id for e in events) == ["fake-a.md", "fake-mgr-b.md"]
    first = {e.doc_id: e for e in events}
    assert first["fake-mgr-b.md"].metadata["access_level"] == "manager"  # label override
    assert {"title": "Alpha procedure", "source_type": "firm_policy"}.items() <= \
        first["fake-a.md"].metadata.items()
    assert (tmp_path / "fake" / "fake-a.md.md").read_text(encoding="utf-8") == DOC_A
    assert first["fake-a.md"].uri == "landing:fake/fake-a.md.md"

    c.fetched.clear()
    assert c.poll(state)[0] == [] and c.fetched == []  # nothing changed: nothing fetched

    c.items["a.md"] = ("v2", DOC_A + "More.\n")
    del c.items["mgr/b.md"]
    events, state = c.poll(state)
    assert [(e.op, e.doc_id) for e in events] == [("upsert", "fake-a.md"), ("delete", "fake-mgr-b.md")]
    assert c.fetched == ["a.md"]


def test_doc_ids_labels_and_banner() -> None:
    assert make_doc_id("git-firm", "methodology/review notes.md") == "git-firm-methodology-review-notes.md"
    long = make_doc_id("s3-docs", "a/" * 80 + "x.md")
    assert len(long) <= 100 and long.startswith("s3-docs-")
    assert label_overrides(("access-partner", "engagement-ENG-A", "type-faq", "classification-restricted",
                            "access-root", "random")) == {
        "access_level": "partner", "engagement_id": "ENG-A", "source_type": "faq",
        "classification": "restricted"}
    moved = banner_first(f"# Title\n\n{SYNTHETIC_BANNER}\n\nText.\n")
    assert moved.splitlines()[0] == SYNTHETIC_BANNER and "# Title" in moved
    assert banner_first("# No banner\n") == "# No banner\n"  # never added


# --------------------------------------------------------------------------- git

def _repo(root: Path, name: str = "repo") -> Path:
    repo = root / "data" / "sources" / name
    make_repo(repo, [{"docs/a.md": DOC_A, "docs/b.md": DOC_B, "notes.txt": "not a document\n"}])
    return repo


def _git_connector(root: Path, repo: Path, **config) -> SnapshotConnector:
    cfg = {"url": repo.relative_to(root).as_posix(), "branch": "main", "defaults": DEFAULTS} | config
    return build_connector("git-firm", "git", cfg, landing_dir=root / "data" / "landing",
                           work_dir=root / "data" / "connectors", project_root=root)


def test_git_connector_reads_new_commits_by_blob_hash(tmp_path: Path) -> None:
    """Covers: R-5-ing, R-7.1-c (git): Markdown files only; a commit re-sends only the files
    whose content changed; a removed file becomes a delete."""
    repo = _repo(tmp_path)
    events, state = _git_connector(tmp_path, repo).poll({})
    assert sorted(e.doc_id for e in events) == ["git-firm-docs-a.md", "git-firm-docs-b.md"]
    meta = events[0].metadata
    assert len(meta["version"]) == 12 and meta["effective_date"] == datetime.now().date().isoformat()

    write_docs(repo, {"docs/a.md": DOC_A + "\n## 2. New\nAdded section.\n"})
    (repo / "docs" / "b.md").unlink()
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "-m", "edit a, remove b")
    events, state = _git_connector(tmp_path, repo).poll(state)  # a new connector: state carries over
    assert [(e.op, e.doc_id) for e in events] == [("upsert", "git-firm-docs-a.md"),
                                                  ("delete", "git-firm-docs-b.md")]
    assert _git_connector(tmp_path, repo).poll(state)[0] == []


def test_git_settings_that_could_run_commands_or_read_other_folders_are_refused(tmp_path: Path) -> None:
    def problems(**cfg):
        return check_git_config({"branch": "main"} | cfg, tmp_path)

    assert problems(url="data/sources/repo") == []
    assert problems(url="https://git.example.test/firm/docs.git") == []
    for bad in ("ext::sh -c touch% /tmp/x", "-uhack", "../outside", "C:/Windows",
                "data/corpus", "https://user:token@git.example.test/x.git"):
        assert problems(url=bad), bad
    assert problems(url="data/sources/repo", branch="--upload-pack=x")
    assert problems(url="data/sources/repo", path="../..")
    with pytest.raises(ConnectorError):
        _git_connector(tmp_path, tmp_path / "data" / "sources" / "none", url="ext::sh -c id")


def test_a_missing_repository_is_a_connector_error(tmp_path: Path) -> None:
    (tmp_path / "data" / "sources").mkdir(parents=True)
    with pytest.raises(ConnectorError):
        _git_connector(tmp_path, tmp_path / "data" / "sources" / "missing").poll({})


# ---------------------------------------------------------------------------- s3

class FakeS3:
    """The two boto3 calls the S3 connector makes, over an in-memory bucket."""

    def __init__(self, objects: dict[str, str]) -> None:
        self.objects = objects

    def get_paginator(self, name: str):
        assert name == "list_objects_v2"
        objects = self.objects

        class Pages:
            def paginate(self, Bucket: str, Prefix: str):  # noqa: N803 (boto3 names)
                keys = sorted(k for k in objects if k.startswith(Prefix))
                for start in range(0, len(keys), 2):  # two per page: paging is exercised
                    yield {"Contents": [{"Key": k, "ETag": f'"{hash(objects[k]) & 0xFFFF:x}"',
                                         "LastModified": datetime(2026, 11, 1, tzinfo=timezone.utc)}
                                        for k in keys[start:start + 2]]}
        return Pages()

    def get_object(self, Bucket: str, Key: str):  # noqa: N803
        return {"Body": io.BytesIO(self.objects[Key].encode("utf-8"))}


def test_s3_connector_lists_markdown_objects_by_etag(tmp_path: Path) -> None:
    """Covers: R-5-ing, R-7.1-c (S3, with a fake client; MinIO in the integration job)."""
    s3 = FakeS3({"firm/a.md": DOC_A, "firm/b.md": DOC_B, "firm/c.pdf": "%PDF", "other/x.md": DOC_A})
    cfg = {"bucket": "firm-documents", "prefix": "firm/", "defaults": DEFAULTS}
    c = build_connector("s3-firm", "s3", cfg, landing_dir=tmp_path, work_dir=tmp_path,
                        project_root=tmp_path, client=s3)
    events, state = c.poll({})
    assert sorted(e.doc_id for e in events) == ["s3-firm-firm-a.md", "s3-firm-firm-b.md"]
    assert events[0].metadata["effective_date"] == "2026-11-01"
    s3.objects["firm/b.md"] = DOC_B + "Changed.\n"
    assert [e.doc_id for e in c.poll(state)[0]] == ["s3-firm-firm-b.md"]


# -------------------------------------------------------------------- filesystem

def test_filesystem_connector_reconciles_its_folder_only(tmp_path: Path) -> None:
    """Covers: R-5-ing (filesystem, through the Connector port)."""
    from tests.helpers import write_doc

    (tmp_path / "data").mkdir()
    write_people(tmp_path, USERS, ENGAGEMENTS)
    write_doc(tmp_path, "firm_policy/p.md", DOC_A)
    write_doc(tmp_path, "faq/f.md", DOC_B)
    write_manifest(tmp_path, [
        dict(doc_id="POL-1", path="data/corpus/firm_policy/p.md", source_type="firm_policy",
             title="P", version="1", effective_date="2026-01-01", access_level="staff", synthetic="true"),
        dict(doc_id="FAQ-1", path="data/corpus/faq/f.md", source_type="faq", title="F",
             version="1", effective_date="2026-01-01", access_level="staff", synthetic="true")])
    indexer = make_indexer(tmp_path)
    events, _ = FilesystemConnector("fs-firm_policy", indexer).poll({})
    assert [(e.op, e.doc_id) for e in events] == [("upsert", "POL-1")]


# ------------------------------------------------------- runner, end to end (lite)

@pytest.fixture()
def world(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    write_people(root, USERS, ENGAGEMENTS)
    write_manifest(root, [])
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    bus = InProcessEventBus()
    worker = IngestWorker(indexer, bus)
    settings = SimpleNamespace(connector_poll_seconds=0.05)

    def factory(source: Source) -> SnapshotConnector:
        if source.id.startswith("down-"):  # stands for a server that cannot be reached
            raise ConnectorError("cannot reach the server")
        return build_connector(source.id, source.type, source.connector_config,
                               landing_dir=indexer.landing_dir, work_dir=root / "data" / "connectors",
                               project_root=root)

    runner = ConnectorRunner(indexer.sessions, bus, settings, factory=factory)
    return SimpleNamespace(root=root, indexer=indexer, bus=bus, worker=worker, runner=runner)


def _register(indexer, source_id: str, type_: str, config: dict, **kw) -> None:
    with indexer.sessions() as s:
        s.add(Source(id=source_id, type=type_, connector_config=config,
                     classification=kw.get("classification", "public_internal"),
                     owner=kw.get("owner"), status=kw.get("status", "active"),
                     created_at=datetime(2026, 11, 19)))
        s.commit()


def test_git_source_flows_into_every_index_with_enrichment(world) -> None:
    """Covers: R-7.1-c, R-5-ing: a git source registered like any other source is polled,
    its documents are enriched (owner and classification from the source) and indexed in
    SQLite, Qdrant and BM25; a file removed from the repository is removed everywhere."""
    repo = _repo(world.root)
    write_docs(repo, {"docs/c.md": "# Gamma\n\n## 1. Note\nManager-only gammaword note.\n"})
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "-m", "add c")
    cfg = {"url": "data/sources/repo", "branch": "main", "defaults": DEFAULTS,
           "path": "docs"}
    _register(world.indexer, "git-firm", "git", cfg, owner="P1")
    assert world.runner.poll_once() == {"git-firm": 3}
    outcomes = world.worker.drain()
    assert [o for _, o in outcomes] == ["indexed"] * 3

    with world.indexer.sessions() as s:
        docs = {d.doc_id: d for d in s.scalars(select(Document).where(Document.source_id == "git-firm"))}
        assert set(docs) == {"git-firm-docs-a.md", "git-firm-docs-b.md", "git-firm-docs-c.md"}
        assert {d.owner for d in docs.values()} == {"P1"}
        assert docs["git-firm-docs-a.md"].title == "Alpha procedure"
        ids = set(s.scalars(select(Chunk.chunk_id).where(Chunk.doc_id == "git-firm-docs-a.md")))
    assert ids and world.indexer.store.chunk_ids("git-firm-docs-a.md") == ids
    assert [c for c, _ in world.indexer.bm25.search("quokkaword")][0].startswith("git-firm-docs-a.md:")
    payload = world.indexer.store.payloads("git-firm-docs-a.md")[0]
    assert payload["source_id"] == "git-firm" and payload["classification"] == "public_internal"

    # Nothing changed: the next round publishes nothing; the state records the poll.
    assert world.runner.poll_once() == {"git-firm": 0}
    with world.indexer.sessions() as s:
        state = s.get(Source, "git-firm").sync_state
    assert state["last_error"] is None and state["last_changes"] == 0 and len(state["items"]) == 3

    # Removing a file in the repository removes the document everywhere.
    (repo / "docs" / "b.md").unlink()
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "-m", "remove b")
    assert world.runner.poll_once() == {"git-firm": 1}
    assert [o for _, o in world.worker.drain()] == ["removed"]
    assert world.indexer.store.count("git-firm-docs-b.md") == 0
    assert not (world.indexer.landing_dir / "git-firm" / "git-firm-docs-b.md.md").exists()


def test_runner_isolates_failures_skips_paused_sources_and_saves_state_after_publishing(world) -> None:
    _repo(world.root)
    cfg = {"url": "data/sources/repo", "branch": "main", "defaults": DEFAULTS}
    _register(world.indexer, "down-jira", "jira", {})
    _register(world.indexer, "git-firm", "git", cfg)
    _register(world.indexer, "git-paused", "git", cfg, status="paused")
    _register(world.indexer, "fs-extra", "filesystem", {"path": "data/corpus/x"})

    # One unreachable source does not stop the others; paused and push sources are not polled.
    assert world.runner.poll_once() == {"down-jira": None, "git-firm": 2}
    with world.indexer.sessions() as s:
        assert s.get(Source, "down-jira").sync_state["last_error"] == "cannot reach the server"
        assert s.get(Source, "git-paused").sync_state is None

    # Publishing fails: the state is not saved, so the same changes are found again.
    write_docs(world.root / "data" / "sources" / "repo", {"docs/a.md": DOC_A + "Edited.\n"})
    git(world.root / "data" / "sources" / "repo", "commit", "--quiet", "-am", "edit")
    real_publish = world.bus.publish
    world.bus.publish = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bus down"))
    assert world.runner.poll_source("git-firm") is None
    world.bus.publish = real_publish
    assert world.runner.poll_source("git-firm") == 1
    with world.indexer.sessions() as s:
        assert s.get(Source, "git-firm").sync_state["last_error"] is None


def test_registering_a_polling_source_checks_its_settings(tmp_path: Path) -> None:
    ok = {"url": "data/sources/repo", "branch": "main", "defaults": DEFAULTS}
    assert config_problems("git", ok, tmp_path) == []
    assert config_problems("git", {**ok, "defaults": None}, tmp_path)
    assert config_problems("s3", {"bucket": "b", "defaults": {"source_type": "x", "access_level": "staff"}},
                           tmp_path)
    assert config_problems("jira", {"base_url": "http://jira.example.test", "project": "AUDQ",
                                    "defaults": DEFAULTS}, tmp_path)  # not https
    assert config_problems("jira", {"base_url": "https://x.atlassian.net", "project": 'A" OR 1=1',
                                    "defaults": DEFAULTS}, tmp_path)  # goes into JQL: refused
    assert config_problems("filesystem", {"path": "x"}, tmp_path) == []  # not a polling type



def test_env_references_resolve_from_the_environment_or_the_env_file(monkeypatch) -> None:
    """M12c fix: an "env:NAME" secret (webhook, connectors) is found in .env as well as in
    the process environment; before, only an exported variable was seen."""
    import app.config as config

    monkeypatch.setattr(config, "_dotenv_values", lambda: {"ONLY_IN_DOTENV": "from-file",
                                                           "IN_BOTH": "from-file"})
    monkeypatch.setenv("IN_BOTH", "from-environment")
    monkeypatch.delenv("ONLY_IN_DOTENV", raising=False)
    assert config.env_reference("env:ONLY_IN_DOTENV") == "from-file"
    assert config.env_reference("env:IN_BOTH") == "from-environment"  # the environment wins
    assert config.env_reference("env:MISSING") is None
    assert config.env_reference("ONLY_IN_DOTENV") is None  # not a reference: never resolved
