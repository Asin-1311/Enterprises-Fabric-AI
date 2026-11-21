"""Git connector: a repository of firm documents (CLAUDE.md section 6, M12c).

connector_config: {"url": <https URL, or a folder under data/sources/>, "branch": "main",
                   "path": <optional sub-folder>, "defaults": {...}}

How it works
- The connector keeps a bare mirror of that one branch in data/connectors/<source>/ and
  fetches the branch on every poll (`git` command line; no extra Python package).
- Listing: every Markdown file on the branch with its blob hash (`git ls-tree`). A blob
  hash changes exactly when the file's content changes, so it is the version token: a new
  commit that does not touch a file causes no work for that file.
- The effective date of a file defaults to the date of the last commit that changed it.

Safety: only local folders under data/sources/ and https URLs are accepted, and git is
told to allow only the `file` and `https` transports (GIT_ALLOW_PROTOCOL), so a source URL
can never run a command (git's `ext::` transport) or read an arbitrary folder.
"""

import os
import re
import subprocess
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from app.connectors.base import ConnectorError, RemoteItem, SnapshotConnector

_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
GIT_TIMEOUT_SECONDS = 60


def check_git_config(config: dict[str, Any], project_root: Path) -> list[str]:
    """Problems with a git source's settings (used when it is registered)."""
    problems = []
    url, branch = str(config.get("url", "")), str(config.get("branch", ""))
    if url.startswith("https://") and "@" in url.split("/", 3)[2]:
        problems.append("a git url must not contain credentials (user:token@...)")
    if url and not url.startswith("https://"):
        sources_dir = (project_root / "data" / "sources").resolve()
        if url.startswith("-") or sources_dir not in (project_root / url).resolve().parents:
            problems.append("a git url must be https://... or a folder under data/sources/")
    if branch and (not _BRANCH.match(branch) or ".." in branch):
        problems.append(f"invalid branch name '{branch}'")
    path = str(config.get("path", ""))
    if path and (path.startswith(("/", "-")) or ".." in Path(path).parts):
        problems.append("path must be a sub-folder of the repository")
    return problems


class GitConnector(SnapshotConnector):
    def __init__(self, source_id: str, config: dict[str, Any], landing_dir: Path,
                 work_dir: Path, project_root: Path) -> None:
        super().__init__(source_id, config, landing_dir)
        problems = check_git_config(config, project_root)
        if problems:
            raise ConnectorError("; ".join(problems))
        url = str(config["url"])
        self.url = url if url.startswith("https://") else str((project_root / url).resolve())
        self.branch, self.subpath = str(config["branch"]), str(config.get("path", "")).strip("/")
        self.mirror = work_dir / source_id / "mirror.git"

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        env = {**os.environ, "GIT_ALLOW_PROTOCOL": "file:https", "GIT_TERMINAL_PROMPT": "0"}
        try:
            done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                                  timeout=GIT_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConnectorError(f"git failed: {exc}") from None
        if done.returncode != 0:
            raise ConnectorError(f"git {args[0]} failed: "
                                 f"{done.stderr.decode('utf-8', 'replace').strip()[:300]}")
        return done.stdout.decode("utf-8", "replace")

    def _update_mirror(self) -> None:
        ref = f"+refs/heads/{self.branch}:refs/heads/{self.branch}"
        if not (self.mirror / "HEAD").exists():
            self.mirror.parent.mkdir(parents=True, exist_ok=True)
            self._git("clone", "--quiet", "--bare", "--single-branch", "--branch", self.branch,
                      "--", self.url, str(self.mirror))
        else:
            self._git("fetch", "--quiet", "--prune", "origin", ref, cwd=self.mirror)

    def list_items(self) -> list[RemoteItem]:
        self._update_mirror()
        args = ["ls-tree", "-r", "--full-tree", self.branch]
        if self.subpath:
            args += ["--", self.subpath]
        items = []
        for line in self._git(*args, cwd=self.mirror).splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob" and path.lower().endswith(self.suffixes):
                items.append(RemoteItem(key=path, version=parts[2]))
        return items

    def fetch(self, item: RemoteItem) -> str | None:
        return self._git("cat-file", "blob", item.version, cwd=self.mirror)

    def metadata(self, item: RemoteItem, text: str) -> dict[str, Any]:
        stamp = self._git("log", "-1", "--format=%cI", self.branch, "--", item.key,
                          cwd=self.mirror).strip()
        modified = datetime.fromisoformat(stamp) if stamp else None
        meta = super().metadata(replace(item, modified=modified), text)
        meta["version"] = item.version[:12]  # the blob hash, shortened as git shows it
        return meta
