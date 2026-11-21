// The cited passage inside a source preview (M16b). The API sends the passage that supports
// the answer (`cited_text`) and a preview that contains it; the source panel shows the
// preview with that passage highlighted.

/** [before, passage, after], or null when there is no passage to highlight. */
export function highlightParts(
  preview: string,
  cited: string | null | undefined,
): [string, string, string] | null {
  if (!cited) return null;
  const at = preview.indexOf(cited);
  if (at < 0) return null;
  return [preview.slice(0, at), cited, preview.slice(at + cited.length)];
}
