// Turn the assistant's inline citations into numbered markers. Two id forms are accepted:
// the stable "[STD-CH06:1.9:1]" (M12b) and the Phase 1 "[CAF-P3-04:0012]", which answers
// written before M12b contain (the API resolves those through legacy_chunk_id).
//
// Each distinct cited chunk gets a number in order of first appearance. The text is rewritten
// to Markdown links with a "#cite-<id>" target, which the answer renderer shows as small
// clickable chips that open the source panel.

const CITATION = /\[([A-Za-z0-9][A-Za-z0-9_.-]*:(?:\d{4}|[A-Za-z0-9][A-Za-z0-9.-]*:\d+))\]/g;

export interface NumberedText {
  markdown: string;
  order: string[]; // chunk ids in the order they are numbered (1-based in the UI)
}

export function numberCitations(text: string): NumberedText {
  const order: string[] = [];
  const markdown = text.replace(CITATION, (_match, id: string) => {
    let n = order.indexOf(id) + 1;
    if (n === 0) {
      order.push(id);
      n = order.length;
    }
    return `[${n}](#cite-${encodeURIComponent(id)})`;
  });
  return { markdown, order };
}

export function citationIdFromHref(href: string | undefined): string | null {
  return href && href.startsWith("#cite-") ? decodeURIComponent(href.slice(6)) : null;
}

export function pageLabel(start: number | null, end: number | null): string {
  if (!start) return "";
  return end && end !== start ? `pp. ${start}–${end}` : `p. ${start}`;
}
