"""Rolling p95 of full-answer time (CLAUDE.md section 8, dimension 4; M15a).

Every answered question adds its full-answer time (question received -> final event) to
a window of the last LATENCY_WINDOW_SIZE answers. The p95 of that window tells the
degradation rule (app/adaptive/degradation.py) whether the system is under load.

Where the window lives (one LatencyWindow port, app/ports):
- lite: in memory (app/adapters/lite/latency.py), per API process;
- full: a Redis list (app/adapters/full/latency.py), shared by every API worker, so
  they see the same load.
`p95()` is None until LATENCY_MIN_SAMPLES answers are known: a handful of slow first
answers (models loading) must not switch degradation on.
"""

import math
from collections.abc import Sequence


def p95(values: Sequence[float]) -> float:
    """The 95th percentile by the nearest-rank method (no interpolation)."""
    ordered = sorted(values)
    return ordered[max(math.ceil(0.95 * len(ordered)) - 1, 0)]
