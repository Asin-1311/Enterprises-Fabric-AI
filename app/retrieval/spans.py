"""Where in a chunk's text the supporting sentences are (citation spans, M11).

Compression (app/retrieval/compress.py) keeps sentences verbatim from the chunk, only with
runs of whitespace collapsed to single spaces. So each kept sentence can be found exactly
in the chunk's text by matching its words with any whitespace between them. The span of a
citation runs from the first kept sentence's start to the last one's end.

A span is None when nothing could be located (for example, a chunk the verifier cited
from the re-ranked list without compressed evidence): the system then does not pretend to
know which part of the chunk supports the answer. M14 narrows spans to individual claims.
"""

import re
from collections.abc import Iterable


def locate(text: str, sentence: str) -> tuple[int, int] | None:
    """(start, end) of `sentence` in `text`, ignoring differences in whitespace."""
    words = sentence.split()
    if not words:
        return None
    match = re.search(r"\s+".join(re.escape(w) for w in words), text)
    return (match.start(), match.end()) if match else None


def supporting_span(text: str, sentences: Iterable[str]) -> tuple[int, int] | None:
    """The smallest range of `text` containing every sentence that can be located."""
    found = [span for sentence in sentences if (span := locate(text, sentence))]
    if not found:
        return None
    return min(start for start, _ in found), max(end for _, end in found)
