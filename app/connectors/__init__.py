"""Connectors: one per kind of source system (CLAUDE.md section 6; M12c).

| type        | module        | how changes are found                                  |
|-------------|---------------|--------------------------------------------------------|
| filesystem  | filesystem.py | the folder watcher (push); poll = reconcile with manifest |
| webhook     | (app/api/webhooks.py) | the source pushes signed changes            |
| git         | git.py        | poll: Markdown files on a branch, by blob hash          |
| s3          | s3.py         | poll: Markdown objects in a bucket, by ETag             |
| confluence  | confluence.py | poll: pages of a space, by version number               |
| jira        | jira.py       | poll: issues of a project, by `updated`                 |
| sharepoint  | sharepoint.py | poll: Markdown files in a document library, by cTag     |
| dms         | dms.py        | push: Debezium change data capture from PostgreSQL (M12d) |

`build_connector` creates the connector of a registered source; app/connectors/runner.py
polls every active source of a polling type and publishes the events.
"""

from pathlib import Path
from typing import Any

from app.connectors.base import ConnectorError, SnapshotConnector

POLLING_TYPES = ("git", "s3", "confluence", "jira", "sharepoint")


def build_connector(source_id: str, source_type: str, config: dict[str, Any], *,
                    landing_dir: Path, work_dir: Path, project_root: Path,
                    **overrides: Any) -> SnapshotConnector:
    """The connector for one registered polling source. `overrides` lets tests pass a fake
    S3 client (`client=`) or an HTTP transport (`transport=`)."""
    if source_type == "git":
        from app.connectors.git import GitConnector

        return GitConnector(source_id, config, landing_dir, work_dir, project_root)
    if source_type == "s3":
        from app.connectors.s3 import S3Connector

        return S3Connector(source_id, config, landing_dir, client=overrides.get("client"))
    if source_type in ("confluence", "jira", "sharepoint"):
        from app.connectors.confluence import ConfluenceConnector
        from app.connectors.jira import JiraConnector
        from app.connectors.sharepoint import SharePointConnector

        cls = {"confluence": ConfluenceConnector, "jira": JiraConnector,
               "sharepoint": SharePointConnector}[source_type]
        return cls(source_id, config, landing_dir, transport=overrides.get("transport"))
    raise ConnectorError(f"no polling connector for source type '{source_type}'")


def config_problems(source_type: str, config: dict[str, Any], project_root: Path) -> list[str]:
    """Problems with a polling source's settings, checked when it is registered
    (POST /sources): the metadata defaults every document needs, and settings that would be
    unsafe (a git url outside data/sources/, a non-https API, a malformed project key)."""
    if source_type == "dms":
        from app.connectors.dms import check_dms_config

        return check_dms_config(config)
    if source_type not in POLLING_TYPES:
        return []
    from app.connectors.base import ACCESS_LEVELS
    from app.ingest.manifest import CLASSIFICATIONS, SOURCE_TYPES

    problems: list[str] = []
    defaults = config.get("defaults")
    if not isinstance(defaults, dict):
        problems.append('connector_config needs "defaults" with source_type and access_level')
        defaults = {}
    if defaults.get("source_type") not in SOURCE_TYPES:
        problems.append(f"defaults.source_type must be one of {', '.join(SOURCE_TYPES)}")
    if defaults.get("access_level") not in ACCESS_LEVELS:
        problems.append(f"defaults.access_level must be one of {', '.join(ACCESS_LEVELS)}")
    if defaults.get("classification") not in (None, *CLASSIFICATIONS):
        problems.append(f"defaults.classification must be one of {', '.join(CLASSIFICATIONS)}")
    if source_type == "git":
        from app.connectors.git import check_git_config

        problems += check_git_config(config, project_root)
    if source_type in ("confluence", "jira", "sharepoint"):
        from app.connectors.http import ALLOWED_BASE_URL_PREFIXES

        if not str(config.get("base_url", "")).startswith(ALLOWED_BASE_URL_PREFIXES):
            problems.append("base_url must start with https:// (or be a local test server)")
    if source_type == "jira":
        from app.connectors.jira import _PROJECT

        if not _PROJECT.match(str(config.get("project", ""))):
            problems.append("project must be a Jira project key such as AUDQ")
    return problems
