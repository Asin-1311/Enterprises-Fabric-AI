import { stripCitations } from "./citations";
import { draftSegments } from "./draft";
import type { ClaimVerdict } from "../types";

const DRAFT = "Assemble within 45 days [POL-DOC-02:2:1]. SA 230 allows 60 days [STD-CH06:1.9:1]. Old rule applies [X-1:1:1].";
const v = (claim_id: number, text: string, verdict: ClaimVerdict["verdict"]): ClaimVerdict =>
  ({ claim_id, text, citations: [], verdict });

test("before any verdict the whole draft is pending", () => {
  expect(draftSegments(DRAFT, [])).toEqual([{ text: DRAFT, state: "pending" }]);
});

test("each verdict marks its own sentence, in order; the rest stays pending", () => {
  const segments = draftSegments(DRAFT, [
    v(1, "Assemble within 45 days [POL-DOC-02:2:1].", "supported"),
    v(2, "SA 230 allows 60 days [STD-CH06:1.9:1].", "flagged"),
  ]);
  expect(segments.map((s) => s.state)).toEqual(["supported", "pending", "flagged", "pending"]);
  expect(segments.map((s) => s.text).join("")).toBe(DRAFT); // nothing lost or duplicated
});

test("a removed claim is its own segment, and an unknown claim changes nothing", () => {
  const segments = draftSegments(DRAFT, [
    v(1, "Not in the draft.", "supported"),
    v(3, "Old rule applies [X-1:1:1].", "removed"),
  ]);
  expect(segments.find((s) => s.state === "removed")?.text).toBe("Old rule applies [X-1:1:1].");
  expect(segments.filter((s) => s.state === "supported")).toEqual([]);
});

test("citation markers are taken out for display", () => {
  expect(stripCitations("Within 45 days [POL-DOC-02:2:1] and [CAF-P3-04:0012].")).toBe("Within 45 days and.");
});
