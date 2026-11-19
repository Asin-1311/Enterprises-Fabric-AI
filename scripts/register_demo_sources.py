"""Register one demo source of each polling type (M12c).

    .venv\\Scripts\\python.exe -m scripts.register_demo_sources [--mock-url http://127.0.0.1:8765]

| id                  | type        | backend                                                    |
|---------------------|-------------|------------------------------------------------------------|
| git-firm-methodology| git         | data/sources/firm-methodology-repo (scripts/make_synthetic_git_repo.py) |
| s3-firm-documents   | s3          | MinIO bucket firm-documents (full profile; scripts/seed_minio.py) |
| conf-aud            | confluence  | the mock server (scripts/mock_saas_server.py), NOT a live tenant |
| jira-audq           | jira        | the mock server, NOT a live tenant                         |
| sp-policies         | sharepoint  | the mock server, NOT a live tenant                         |

Each source is checked by the same rules as POST /sources (settings, defaults, no stored
secrets) and its registration is written to audit_log. A source that already exists is
left alone. The connector runner (inside the API in lite, or `python -m
app.connectors.runner`) then picks them up. The mock-server sources need
`python -m scripts.mock_saas_server` running, and the credentials named below in .env
(any value works against the mock server):
    DEMO_ATLASSIAN_TOKEN=...   DEMO_GRAPH_TOKEN=...
"""

import argparse
import sys
from datetime import datetime, timezone

from app.api.admin import secret_problems
from app.config import PROJECT_ROOT, get_settings
from app.connectors import config_problems
from app.db.models import Source
from app.db.session import write_audit_log

STAFF_POLICY = {"source_type": "firm_policy", "access_level": "staff"}


def demo_sources(mock_url: str) -> list[dict]:
    atlassian = {"base_url": mock_url, "user": "kb-reader@example-associates.test",
                 "token": "env:DEMO_ATLASSIAN_TOKEN"}
    return [
        {"id": "git-firm-methodology", "type": "git", "owner": "M01", "config": {
            "url": "data/sources/firm-methodology-repo", "branch": "main", "defaults": STAFF_POLICY}},
        {"id": "s3-firm-documents", "type": "s3", "owner": "M01", "config": {
            "bucket": "firm-documents", "prefix": "firm/", "endpoint_url": "http://127.0.0.1:9000",
            "access_key": "env:MINIO_ROOT_USER", "secret_key": "env:MINIO_ROOT_PASSWORD",
            "defaults": STAFF_POLICY}},
        {"id": "conf-aud", "type": "confluence", "owner": "M01",
         "config": atlassian | {"space": "AUD", "defaults": STAFF_POLICY}},
        {"id": "jira-audq", "type": "jira", "owner": "M02",
         "config": atlassian | {"project": "AUDQ",
                                "defaults": {"source_type": "faq", "access_level": "staff"}}},
        {"id": "sp-policies", "type": "sharepoint", "owner": "P02", "config": {
            "base_url": mock_url, "site": "example.sharepoint.com,1111,2222",
            "token": "env:DEMO_GRAPH_TOKEN", "defaults": STAFF_POLICY}},
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register the demo connector sources.")
    parser.add_argument("--mock-url", default="http://127.0.0.1:8765")
    parser.add_argument("--only", nargs="*", help="register only these source ids")
    args = parser.parse_args(argv)

    from app.adapters import open_metadata_store

    metadata = open_metadata_store(get_settings())
    metadata.initialise()
    with metadata.sessions() as s:
        for spec in demo_sources(args.mock_url):
            if args.only and spec["id"] not in args.only:
                continue
            if s.get(Source, spec["id"]) is not None:
                print(f"{spec['id']}: already registered")
                continue
            problems = config_problems(spec["type"], spec["config"], PROJECT_ROOT) + \
                secret_problems(spec["config"])
            if problems:
                print(f"{spec['id']}: NOT registered: {problems}")
                continue
            s.add(Source(id=spec["id"], type=spec["type"], connector_config=spec["config"],
                         owner=spec["owner"], classification="public_internal", status="active",
                         created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            s.flush()
            write_audit_log(s, "script:register_demo_sources", "source_registered",
                            f"{spec['id']} ({spec['type']}, public_internal)")
            print(f"{spec['id']}: registered ({spec['type']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
