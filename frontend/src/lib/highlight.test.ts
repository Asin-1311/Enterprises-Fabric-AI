import { highlightParts } from "./highlight";

test("splits the preview around the cited passage", () => {
  expect(highlightParts("… before. Assemble within 45 days. After …", "Assemble within 45 days."))
    .toEqual(["… before. ", "Assemble within 45 days.", " After …"]);
});

test("no passage, or one not in the preview, gives nothing to highlight", () => {
  expect(highlightParts("Some text.", null)).toBeNull();
  expect(highlightParts("Some text.", "Other text.")).toBeNull();
});
