"""Which evaluation run folders count as quality evidence, and when they ran.

Used by GET /api/v1/metrics/quality and scripts/make_report.py, so both apply the same
rules. Run folders are named `<YYYYMMDD-HHMMSS>_<type>` by the evaluation scripts.

- Quality runs are the `retrieval` and `answers` types. Calibration runs (judge versus
  human labels) are about the judge, not about the system, so they are left out.
- A few runs are known to be invalid and are never used (reasons in docs/NOTES.md).
- Nothing here reads or computes metric values; it only selects folders.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

QUALITY_TYPES = ("retrieval", "answers")

# Runs that must never be used as evidence, with the reason (see docs/NOTES.md).
EXCLUDED_RUNS = {
    "20261107-125802_retrieval": "flawed superseded-ordering metric (NOTES.md, M3)",
}

# Values changed by hand in a run's files after the run, each one documented in
# docs/NOTES.md (M9). Key: (run, question id, system); value: (what the run wrote, what the
# edited file now says). The report uses the run's own untouched files (metrics.json,
# per_question.csv) and lists these edits; any OTHER disagreement makes it refuse the run.
KNOWN_EDITS: dict[tuple[str, str, str], tuple[str, str]] = {
    ("20261113-101102_answers", "q001", "full"): ("partially_correct", "correct"),
}

_RUN_NAME = re.compile(r"^(\d{8}-\d{6})_([a-z_]+)$")


@dataclass(frozen=True)
class RunFolder:
    path: Path
    started_at: datetime  # local time, as written in the folder name
    type: str

    @property
    def name(self) -> str:
        return self.path.name


def parse_run(path: Path) -> RunFolder | None:
    """The run's start time and type, or None if the folder is not a run."""
    m = _RUN_NAME.match(path.name)
    if not m or not (path / "metrics.json").is_file():
        return None
    return RunFolder(path, datetime.strptime(m.group(1), "%Y%m%d-%H%M%S"), m.group(2))


def quality_runs(results_dir: Path, start: datetime | None = None,
                 end: datetime | None = None) -> list[RunFolder]:
    """Quality runs (retrieval, answers) started within [start, end], newest first.
    Excluded runs are never returned."""
    runs = []
    if results_dir.is_dir():
        for folder in results_dir.iterdir():
            run = parse_run(folder)
            if (run is None or run.type not in QUALITY_TYPES or run.name in EXCLUDED_RUNS
                    or (start and run.started_at < start) or (end and run.started_at > end)):
                continue
            runs.append(run)
    return sorted(runs, key=lambda r: r.name, reverse=True)
