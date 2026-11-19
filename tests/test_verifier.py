"""Unit tests for claim extraction, verdict application and confidence (no LLM)."""

import pytest

from app.agents.verifier import apply_verdicts, build_prompt, extract_claims

ANSWER = """**Under the firm policy, the file must be assembled within 45 days.** [POL-2:0003]

**Firm vs. ICAI**
- SA 230 says ordinarily not more than 60 days, e.g. after the report date. [STD-6:0012]
- The firm deadline is stricter. [POL-2:0003] It applies from 2025. [POL-2:0007]

**Other points**
- The evidence does not state a penalty.
- Files are kept for ten years. [STD-6:0012]"""


def _verdicts(**kw) -> dict[int, dict]:
    """kw like c1="s" (supported factual), c2="u" (unsupported), c3="n" (non-factual ok)."""
    out = {}
    for key, v in kw.items():
        cid = int(key[1:])
        out[cid] = {"id": cid, "type": "non_factual" if v == "n" else "factual",
                    "verdict": "unsupported" if v == "u" else "supported", "reason": ""}
    return out


def test_claims_are_sentences_with_their_citations() -> None:
    _, claims = extract_claims(ANSWER)
    texts = [c.text for c in claims]
    assert len(claims) == 6  # headings are not claims; "vs." and "e.g." do not split
    assert texts[1].startswith("SA 230 says ordinarily not more than 60 days, e.g. after")
    assert claims[0].citations == ["POL-2:0003"]
    assert claims[2].citations == ["POL-2:0003"] and claims[3].citations == ["POL-2:0007"]
    assert claims[4].citations == []  # uncited (framing) sentence


def test_unsupported_claims_removed_and_confidence_computed() -> None:
    lines, claims = extract_claims(ANSWER)
    v = apply_verdicts(lines, claims, _verdicts(c1="s", c2="s", c3="s", c4="s", c5="n", c6="u"))
    assert "ten years" not in v.text
    assert v.factual == 5 and v.supported == 4  # the non-factual claim is not counted
    assert v.confidence == pytest.approx(4 / 5)
    assert v.cited_ids == ["POL-2:0003", "STD-6:0012", "POL-2:0007"]
    assert "**Other points**" in v.text  # heading kept: one line remains under it


def test_heading_dropped_when_everything_under_it_is_removed() -> None:
    lines, claims = extract_claims(ANSWER)
    v = apply_verdicts(lines, claims, _verdicts(c1="s", c2="s", c3="s", c4="s", c5="u", c6="u"))
    assert "**Other points**" not in v.text and "penalty" not in v.text
    assert v.text.rstrip().endswith("[POL-2:0007]")


def test_partially_removed_line_keeps_the_supported_sentence() -> None:
    lines, claims = extract_claims(ANSWER)
    v = apply_verdicts(lines, claims, _verdicts(c1="s", c2="s", c3="s", c4="u", c5="n", c6="s"))
    assert "- The firm deadline is stricter. [POL-2:0003]" in v.text
    assert "applies from 2025" not in v.text and "POL-2:0007" not in v.cited_ids


def test_missing_verdict_counts_as_unsupported() -> None:
    lines, claims = extract_claims(ANSWER)
    v = apply_verdicts(lines, claims, _verdicts(c1="s", c2="s", c3="s"))
    assert v.factual == 6 and v.supported == 3 and v.confidence == pytest.approx(0.5)


UNCITED = "The firm deadline is 45 days [P:0001]. The old rule was 60 days. Teams escalate on day 45."


def _v(cid: int, verdict: str, cites: list[str] | None = None, kind: str = "factual") -> dict:
    return {"id": cid, "type": kind, "verdict": verdict, "supporting_cites": cites or [], "reason": ""}


def test_uncited_claim_with_a_valid_supporting_chunk_is_repaired() -> None:
    lines, claims = extract_claims(UNCITED)
    v = apply_verdicts(lines, claims, {1: _v(1, "supported"), 2: _v(2, "supported", ["P:0002"]),
                                       3: _v(3, "supported", ["P:0001"])},
                       allowed_cites={"P:0001", "P:0002"})
    assert v.text == ("The firm deadline is 45 days [P:0001]. The old rule was 60 days. [P:0002] "
                      "Teams escalate on day 45. [P:0001]")
    assert v.confidence == 1.0 and len(v.repaired) == 2
    assert v.cited_ids == ["P:0001", "P:0002"]


def test_repair_only_with_a_chunk_the_synthesiser_was_given() -> None:
    lines, claims = extract_claims(UNCITED)
    v = apply_verdicts(lines, claims, {1: _v(1, "supported"), 2: _v(2, "supported", ["X:9999"]),
                                       3: _v(3, "supported")},  # no source named at all
                       allowed_cites={"P:0001"})
    assert "60 days" not in v.text and "escalate" not in v.text
    assert v.repaired == [] and v.confidence == pytest.approx(1 / 3)


def test_abbreviations_and_mid_sentence_citations_do_not_split() -> None:
    _, claims = extract_claims("See Circular No. 5 and SA 230 [A:0001] for the rule. Next point [B:0002].")
    assert [c.text for c in claims] == ["See Circular No. 5 and SA 230 [A:0001] for the rule.",
                                        "Next point [B:0002]."]


def test_sentences_split_after_stable_citation_ids_too() -> None:
    """M12b: a citation ending a sentence ends the claim for both id forms."""
    _, claims = extract_claims("Assemble within 60 days. [STD-CH06:1.9:1] It applies to all "
                               "files [POL-2:2:12]. Old form. [POL-2:0003] Then more [A:0:1].")
    assert [c.citations for c in claims] == [["STD-CH06:1.9:1"], ["POL-2:2:12"],
                                             ["POL-2:0003"], ["A:0:1"]]


def test_no_factual_claims_means_zero_confidence() -> None:
    lines, claims = extract_claims("The evidence does not say.")
    assert apply_verdicts(lines, claims, _verdicts(c1="n")).confidence == 0.0
    assert apply_verdicts(*extract_claims(""), {}).confidence == 0.0


def test_prompt_contains_only_cited_chunks_in_full() -> None:
    from types import SimpleNamespace

    _, claims = extract_claims(ANSWER)
    chunks = {cid: SimpleNamespace(text=f"FULL TEXT OF {cid}", title="t", classification="public_internal")
              for cid in ("POL-2:0003", "STD-6:0012", "POL-2:0007", "OTHER:0001")}
    prompt = build_prompt("q", claims, chunks)
    assert "FULL TEXT OF STD-6:0012" in prompt and "OTHER:0001" not in prompt
    assert "[claim 5] cites: NONE" in prompt
    assert "[POL-2:0003]" not in prompt.split("Cited documents:")[0]  # ids listed, not inline
