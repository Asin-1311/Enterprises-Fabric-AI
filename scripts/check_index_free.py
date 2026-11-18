"""Exit 0 if the local Qdrant index can be opened, else print why and exit 1.

Used by scripts/run_demo.bat before starting the API, because local Qdrant allows only one
program at a time (indexer, API/demo, scripts/ask.py, evaluation).
"""

import sys

from app.config import get_settings
from app.retrieval.dense import QdrantInUseError, open_qdrant


def main() -> int:
    try:
        open_qdrant(get_settings().qdrant_path).close()
    except QdrantInUseError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
