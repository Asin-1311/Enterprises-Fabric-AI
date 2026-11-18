"""The 'index already in use' situation gives a clear error, not a raw traceback."""

from pathlib import Path

import pytest

from app.retrieval.dense import QdrantInUseError, open_qdrant


def test_second_open_reports_index_in_use(tmp_path: Path) -> None:
    first = open_qdrant(tmp_path / "qdrant")
    try:
        with pytest.raises(QdrantInUseError, match="already in use by another program"):
            open_qdrant(tmp_path / "qdrant")
    finally:
        first.close()
    open_qdrant(tmp_path / "qdrant").close()  # free again once the first one is closed
