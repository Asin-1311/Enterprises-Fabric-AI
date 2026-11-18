"""Retrieval tests (CLAUDE.md section 13): RRF against hand-computed values, recency,
ACL at the retrieval layer (not the API), engagement isolation and superseded handling.
"""

from datetime import date
from pathlib import Path

import pytest
from qdrant_client import models

from app.db.models import Document
from app.retrieval.acl import (
    UnknownUserError,
    UserContext,
    allowed_chunk_ids,
    can_see,
    load_user,
    qdrant_filter,
)
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.hybrid import MODES
from app.retrieval.recency import adjustment, freshness
from tests.helpers import make_indexer, make_searcher, write_doc, write_manifest, write_people

AS_OF = date(2026, 11, 7)

# ------------------------------------------------------------------- RRF (hand values)


def test_rrf_matches_hand_computation() -> None:
    # k = 60. a: 1/61. b: 1/62 + 1/61. c: 1/63 + 1/62. d: 1/63.
    fused = dict(reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "d"]], k=60))
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["c"] == pytest.approx(1 / 63 + 1 / 62)
    assert fused["d"] == pytest.approx(1 / 63)
    order = [i for i, _ in reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "d"]], k=60)]
    assert order == ["b", "c", "a", "d"]


def test_rrf_small_k_and_edge_cases() -> None:
    assert dict(reciprocal_rank_fusion([["x"], ["y", "x"]], k=1)) == {
        "x": pytest.approx(1 / 2 + 1 / 3), "y": pytest.approx(1 / 2)}
    assert reciprocal_rank_fusion([], k=60) == []
    assert reciprocal_rank_fusion([[], []], k=60) == []
    # ties are broken by id so results are deterministic
    assert [i for i, _ in reciprocal_rank_fusion([["b"], ["a"]], k=60)] == ["a", "b"]


# --------------------------------------------------------------------------- recency


def test_freshness_values() -> None:
    assert freshness("2026-11-07", AS_OF, 365) == pytest.approx(1.0)
    assert freshness("2025-11-07", AS_OF, 365) == pytest.approx(0.5)
    assert freshness("2024-11-07", AS_OF, 365) == pytest.approx(0.25)  # 730 days = 2 half-lives
    assert freshness("2027-01-01", AS_OF, 365) == pytest.approx(1.0)  # future = today
    assert freshness(None, AS_OF, 365) == 0.0


def test_adjustment_values() -> None:
    common = dict(as_of=AS_OF, superseded_weight=0.5, recency_weight=0.2, half_life_days=365)
    assert adjustment(superseded=False, effective_date="2020-01-01", time_sensitive=False,
                      **common) == 1.0
    assert adjustment(superseded=True, effective_date="2020-01-01", time_sensitive=False,
                      **common) == 0.5
    assert adjustment(superseded=False, effective_date="2025-11-07", time_sensitive=True,
                      **common) == pytest.approx(1.1)  # 1 + 0.2 * 0.5
    assert adjustment(superseded=True, effective_date="2026-11-07", time_sensitive=True,
                      **common) == pytest.approx(0.6)  # 0.5 * (1 + 0.2 * 1)


# --------------------------------------------------------------- can_see (rule table)

STAFF_A = UserContext("S1", "staff", frozenset({"ENG-A"}))
MANAGER_NONE = UserContext("M2", "manager", frozenset())
PARTNER = UserContext("P2", "partner", frozenset())


@pytest.mark.parametrize(
    ("user", "level", "engagement", "expected"),
    [
        (STAFF_A, "staff", None, True),
        (STAFF_A, "manager", None, False),
        (STAFF_A, "partner", None, False),
        (STAFF_A, "staff", "ENG-A", True),
        (STAFF_A, "staff", "ENG-B", False),
        (STAFF_A, "manager", "ENG-A", False),  # on the team, but role too low
        (MANAGER_NONE, "manager", None, True),
        (MANAGER_NONE, "staff", "ENG-A", False),  # manager not on the team sees nothing
        (PARTNER, "partner", None, True),
        (PARTNER, "manager", "ENG-A", True),  # partners see every engagement
    ],
)
def test_can_see_rules(user: UserContext, level: str, engagement: str | None, expected: bool) -> None:
    assert can_see(user, level, engagement) is expected


# ------------------------------------------------------------ retrieval-layer fixture

DOCS = [
    # doc_id, rel path, source_type, level, engagement, date, supersedes, body
    ("POL-OLD", "firm_policy/old.md", "firm_policy", "staff", "", "2022-04-01", "",
     "# File Policy\n\n## 1. Assembly\nAssemble the final audit file within sixty days of the report."),
    ("POL-NEW", "firm_policy/new.md", "firm_policy", "staff", "", "2025-04-01", "POL-OLD",
     "# File Policy\n\n## 1. Assembly\nAssemble the audit file within forty-five days."),
    ("MGR-1", "firm_policy/mgr.md", "firm_policy", "manager", "", "2025-04-01", "",
     "# Materiality\n\n## 1. Benchmark\nMateriality benchmark managerword five percent."),
    ("PTR-1", "circulars/ptr.md", "circulars", "partner", "", "2026-02-01", "",
     "# Fee review\n\n## 1. Trigger\nFee dependence partnerword fifteen percent."),
    ("ENG-A-1", "engagement/a/plan.md", "engagement", "staff", "ENG-A", "2026-01-12", "",
     "# Alpha Fictional Ltd planning memo\n\n## 1. Risks\nAlpha Fictional Ltd alphaword inventory risk."),
    ("ENG-A-2", "engagement/a/review.md", "engagement", "manager", "ENG-A", "2026-05-18", "",
     "# Alpha Fictional Ltd review notes\n\n## 1. Notes\nAlpha Fictional Ltd alphamgrword shortage."),
    ("ENG-B-1", "engagement/b/plan.md", "engagement", "staff", "ENG-B", "2026-03-20", "",
     "# Beta Fictional Bank planning memo\n\n## 1. Risks\nBeta Fictional Bank betaword NPA risk."),
    ("CAL-OLD", "calendar/old.md", "calendar", "staff", "", "2024-04-01", "",
     "# Calendar\n\n## 1. Dates\nTax audit report due date calendar deadline filing."),
    ("CAL-NEW", "calendar/new.md", "calendar", "staff", "", "2026-04-01", "",
     "# Calendar\n\n## 1. Dates\nTax audit report due date calendar."),
]

USERS = ("user_id,name,role,synthetic\nS1,Staff A,staff,true\nS2,Staff B,staff,true\n"
         "M1,Manager A,manager,true\nM2,Manager none,manager,true\n"
         "P1,Partner A,partner,true\nP2,Partner other,partner,true\n")
ENGAGEMENTS = ("engagement_id,client_name,industry,fy,partner_id,member_ids\n"
               "ENG-A,Alpha Fictional Ltd,manufacturing,2025-26,P1,S1;M1\n"
               "ENG-B,Beta Fictional Bank,banking,2025-26,P1,S2\n")

PROBES = [
    "materiality benchmark managerword", "fee dependence partnerword fifteen percent",
    "Alpha Fictional Ltd planning memo alphaword", "Alpha Fictional Ltd review notes alphamgrword",
    "Beta Fictional Bank betaword NPA", "audit file assembly days", "tax audit report due date",
]


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory):
    root: Path = tmp_path_factory.mktemp("retrieval")
    (root / "data").mkdir()
    write_people(root, USERS, ENGAGEMENTS)
    rows = []
    for doc_id, rel, st, level, eng, eff, sup, body in DOCS:
        write_doc(root, rel, body)
        rows.append(dict(doc_id=doc_id, path=f"data/corpus/{rel}", source_type=st, title=doc_id,
                         version="1", effective_date=eff, supersedes=sup, access_level=level,
                         engagement_id=eng, synthetic="true"))
    write_manifest(root, rows)
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    meta = {d[0]: (d[3], d[4] or None) for d in DOCS}
    return indexer, make_searcher(indexer, top_k=20), meta


def _user(env, user_id: str) -> UserContext:
    indexer, _, _ = env
    with indexer.sessions() as s:
        return load_user(s, user_id)


def test_load_user_reads_role_and_engagements(env) -> None:
    assert _user(env, "S1") == UserContext("S1", "staff", frozenset({"ENG-A"}))
    assert _user(env, "P1").engagement_ids == {"ENG-A", "ENG-B"}  # partner of both
    assert _user(env, "M2").engagement_ids == frozenset()
    with pytest.raises(UnknownUserError):
        _user(env, "NOBODY")


@pytest.mark.parametrize("user_id", ["S1", "S2", "M1", "M2", "P1", "P2"])
@pytest.mark.parametrize("mode", MODES)
def test_no_result_ever_violates_acl(env, user_id: str, mode: str) -> None:
    """Every probe, every mode: every returned chunk passes the access rule."""
    _, searcher, meta = env
    user = _user(env, user_id)
    for probe in PROBES:
        for r in searcher.search(probe, user, mode=mode, as_of=AS_OF):
            level, eng = meta[r.doc_id]
            assert can_see(user, level, eng), f"{user_id} got {r.doc_id} for {probe!r}"


def test_staff_never_gets_manager_or_partner_chunks(env) -> None:
    _, searcher, meta = env
    for user_id in ("S1", "S2"):
        user = _user(env, user_id)
        for probe in PROBES:
            levels = {meta[r.doc_id][0] for r in searcher.search(probe, user, as_of=AS_OF)}
            assert levels <= {"staff"}, (user_id, probe, levels)


def test_engagement_isolation_even_when_naming_the_client(env) -> None:
    _, searcher, _ = env
    probe = "Alpha Fictional Ltd planning memo alphaword"
    s2 = {r.doc_id for r in searcher.search(probe, _user(env, "S2"), as_of=AS_OF)}
    assert not {"ENG-A-1", "ENG-A-2"} & s2  # staff on ENG-B only
    s1 = {r.doc_id for r in searcher.search(probe, _user(env, "S1"), as_of=AS_OF)}
    assert "ENG-A-1" in s1 and "ENG-A-2" not in s1  # on team, but review notes are manager-level
    m2 = {r.doc_id for r in searcher.search(probe, _user(env, "M2"), as_of=AS_OF)}
    assert not {"ENG-A-1", "ENG-A-2", "ENG-B-1"} & m2  # manager on no team: nothing
    m1 = {r.doc_id for r in searcher.search("Alpha review notes alphamgrword", _user(env, "M1"),
                                            as_of=AS_OF)}
    assert "ENG-A-2" in m1
    p2 = {r.doc_id for r in searcher.search("Alpha review notes alphamgrword", _user(env, "P2"),
                                            as_of=AS_OF)}
    assert "ENG-A-2" in p2  # partner not on the team still sees it


def test_three_acl_expressions_agree(env) -> None:
    """Qdrant filter, BM25 candidate set and can_see() select the same documents."""
    indexer, _, meta = env
    for user_id in ("S1", "S2", "M1", "M2", "P1", "P2"):
        user = _user(env, user_id)
        expected = {d for d, (lvl, eng) in meta.items() if can_see(user, lvl, eng)}
        with indexer.sessions() as s:
            bm25_docs = {cid.split(":")[0] for cid in allowed_chunk_ids(s, user)}
        points, _ = indexer.store.client.scroll("chunks", scroll_filter=qdrant_filter(user),
                                                limit=1000, with_payload=True)
        qdrant_docs = {p.payload["doc_id"] for p in points}
        assert bm25_docs == expected == qdrant_docs, user_id


def test_superseded_document_is_down_weighted_not_removed(env) -> None:
    _, searcher, _ = env
    user = _user(env, "S1")
    query = "assemble the final audit file within sixty days"  # wording of the OLD policy
    raw = [r.doc_id for r in searcher.search(query, user, mode="hybrid_raw", as_of=AS_OF)]
    assert raw.index("POL-OLD") < raw.index("POL-NEW")  # pure text match prefers the old one
    adjusted = searcher.search(query, user, mode="hybrid", as_of=AS_OF)
    order = [r.doc_id for r in adjusted]
    assert order.index("POL-NEW") < order.index("POL-OLD")  # current version now first
    old = next(r for r in adjusted if r.doc_id == "POL-OLD")
    assert old.superseded is True  # still available, flagged for the synthesiser


def test_recency_boost_only_for_time_sensitive_questions(env) -> None:
    _, searcher, _ = env
    user = _user(env, "S1")
    query = "tax audit report due date calendar deadline filing"  # wording of the OLD calendar
    plain = [r.doc_id for r in searcher.search(query, user, source_types=["calendar"], as_of=AS_OF)]
    assert plain[0] == "CAL-OLD"
    timely = [r.doc_id for r in searcher.search(query, user, source_types=["calendar"],
                                                time_sensitive=True, as_of=AS_OF)]
    assert timely[0] == "CAL-NEW"


def test_source_type_routing_filter(env) -> None:
    _, searcher, _ = env
    results = searcher.search("audit file", _user(env, "P1"), source_types=["calendar"], as_of=AS_OF)
    assert results and {r.source_type for r in results} == {"calendar"}


def test_sub_queries_are_fused(env) -> None:
    _, searcher, _ = env
    docs = {r.doc_id for r in searcher.search(
        ["audit file assembly days", "tax audit report due date"], _user(env, "S1"), as_of=AS_OF)}
    assert {"POL-NEW", "CAL-NEW"} <= docs


def test_defence_in_depth_drops_a_leaked_vector(env) -> None:
    """If Qdrant ever returned a forbidden point (e.g. stale payload), it is still dropped."""
    indexer, _, _ = env
    leaky_store = type(indexer.store).__new__(type(indexer.store))
    leaky_store.__dict__.update(indexer.store.__dict__)
    leaky_store.search = lambda vector, flt, limit: [("PTR-1:0001", 0.99)]  # ignores the filter
    searcher = make_searcher(indexer)
    searcher.store = leaky_store
    results = searcher.search("fee dependence", _user(env, "S1"), mode="dense", as_of=AS_OF)
    assert "PTR-1" not in {r.doc_id for r in results}


def test_payload_filter_is_a_real_qdrant_filter(env) -> None:
    flt = qdrant_filter(_user(env, "S1"), ["engagement"])
    assert isinstance(flt, models.Filter)
    keys = [c.key for c in flt.must]
    assert keys == ["access_level", "engagement_id", "source_type"]
    assert qdrant_filter(_user(env, "P1")).must[0].key == "access_level"
    assert len(qdrant_filter(_user(env, "P1")).must) == 1  # partners: no engagement condition


def test_documents_in_fixture_are_all_indexed(env) -> None:
    indexer, _, _ = env
    with indexer.sessions() as s:
        assert {d.doc_id for d in s.query(Document)} == {d[0] for d in DOCS}
