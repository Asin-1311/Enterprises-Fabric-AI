"""Learned routing: a Thompson-sampling bandit (spec 7.4 and 8; CLAUDE.md section 8,
dimension 3; Gap Register R-8-bandit, R-7.4; M15b).

Arms. For each question type (intent), an arm is one source type searched by one
retriever, e.g. ("circulars", "keyword"). Each arm keeps a Beta(alpha, beta) belief about
how often searching it leads to a good answer; with no data it is Beta(1, 1): unknown.

Allocation. Each retriever's candidate budget for a question (the plan's candidates per
retriever) is shared among the source types the router chose:
1. draw one sample from every arm's Beta distribution (Thompson sampling: arms that did
   well, or that are still uncertain, get high draws more often);
2. give each source type a share of the budget proportional to its draw;
3. a share below MIN_CANDIDATES is dropped (that source is not searched by that
   retriever this time), the budget is shared again among the rest.
The bandit never adds a source type the router did not choose, and every search still
runs the access filters: learning can change WHERE the system looks, never WHAT the user
may see (CLAUDE.md section 7: no learning can override access rules).

Replay (app/adaptive/replay.py) uses the posterior means instead of random draws, so a
policy's replay score is the same every time it is computed.
"""

import random
from collections.abc import Iterable
from dataclasses import dataclass, field

METHODS = ("dense", "keyword")
MIN_CANDIDATES = 2
PRIOR = (1.0, 1.0)


def arm_key(source_type: str, method: str) -> str:
    return f"{source_type}|{method}"


@dataclass
class BanditState:
    """Beta(alpha, beta) per intent and arm. Stored as a routing policy's `weights`."""

    arms: dict[str, dict[str, list[float]]] = field(default_factory=dict)

    def belief(self, intent: str, arm: str) -> tuple[float, float]:
        a, b = self.arms.get(intent, {}).get(arm, PRIOR)
        return float(a), float(b)

    def update(self, intent: str, arm: str, reward: float, weight: float = 1.0) -> None:
        """Add `weight` observations with success `reward` (0..1) to an arm."""
        a, b = self.belief(intent, arm)
        self.arms.setdefault(intent, {})[arm] = [a + weight * reward, b + weight * (1.0 - reward)]

    def to_json(self) -> dict:
        return {"kind": "bandit", "arms": {i: {k: [round(a, 4), round(b, 4)] for k, (a, b) in arms.items()}
                                           for i, arms in self.arms.items()}}

    @classmethod
    def from_json(cls, data: dict | None) -> "BanditState":
        if not data or data.get("kind") != "bandit":
            return cls()
        return cls({i: {k: [float(a), float(b)] for k, (a, b) in arms.items()}
                    for i, arms in (data.get("arms") or {}).items()})


def allocate(state: BanditState, intent: str, source_types: Iterable[str], budget: int,
             rng: random.Random | None = None, greedy: bool = False) -> dict[str, int]:
    """Candidates per arm ("<source>|<method>" -> count) for one question.

    `greedy` uses posterior means instead of samples (replay). The counts for each method
    add up to `budget` (or less, if every share falls below MIN_CANDIDATES)."""
    sources = list(dict.fromkeys(source_types))
    rng = rng or random.Random()
    out: dict[str, int] = {}
    for method in METHODS:
        draws = {}
        for st in sources:
            a, b = state.belief(intent, arm_key(st, method))
            draws[st] = a / (a + b) if greedy else rng.betavariate(a, b)
        kept = dict(draws)
        while kept:
            total = sum(kept.values()) or 1.0
            shares = {st: budget * d / total for st, d in kept.items()}
            small = [st for st, n in shares.items() if n < MIN_CANDIDATES]
            if not small or len(small) == len(kept):
                break
            for st in small:  # drop the weakest arms, share their budget among the rest
                kept.pop(st)
        if not kept:
            continue
        total = sum(kept.values()) or 1.0
        counts = {st: max(MIN_CANDIDATES, int(budget * d / total)) for st, d in kept.items()}
        # Give any remainder to the strongest arms so each method uses its whole budget.
        for st in sorted(kept, key=lambda s: -kept[s]):
            if sum(counts.values()) >= budget:
                break
            counts[st] += 1
        out.update({arm_key(st, method): n for st, n in counts.items()})
    return out
