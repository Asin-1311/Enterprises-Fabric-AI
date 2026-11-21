import { citationIdFromHref, numberCitations, pageLabel } from "./citations";

test("numbers citations by first appearance and reuses numbers", () => {
  const { markdown, order } = numberCitations("45 days [POL-DOC-02:0003]. SA 230 [STD-CH06:0012]. Again [POL-DOC-02:0003].");
  expect(order).toEqual(["POL-DOC-02:0003", "STD-CH06:0012"]);
  expect(markdown).toBe(
    "45 days [1](#cite-POL-DOC-02%3A0003). SA 230 [2](#cite-STD-CH06%3A0012). Again [1](#cite-POL-DOC-02%3A0003).",
  );
});

test("numbers stable chunk ids (M12b) alongside Phase 1 ids", () => {
  const { markdown, order } = numberCitations(
    "60 days [STD-CH06:1.9:1]. FAQ [FAQ-01:q-3f9a2c:2]. Old [POL-DOC-02:0003]. Again [STD-CH06:1.9:1].",
  );
  expect(order).toEqual(["STD-CH06:1.9:1", "FAQ-01:q-3f9a2c:2", "POL-DOC-02:0003"]);
  expect(markdown).toContain("Again [1](#cite-STD-CH06%3A1.9%3A1).");
  expect(citationIdFromHref("#cite-FAQ-01%3Aq-3f9a2c%3A2")).toBe("FAQ-01:q-3f9a2c:2");
});

test("leaves ordinary brackets alone", () => {
  expect(numberCitations("See [note] and [A:12].").markdown).toBe("See [note] and [A:12].");
});

test("href round trip and page labels", () => {
  expect(citationIdFromHref("#cite-CAF-P3-04%3A0012")).toBe("CAF-P3-04:0012");
  expect(citationIdFromHref("https://example.com")).toBeNull();
  expect(pageLabel(9, 9)).toBe("p. 9");
  expect(pageLabel(107, 125)).toBe("pp. 107–125");
  expect(pageLabel(null, null)).toBe("");
});
