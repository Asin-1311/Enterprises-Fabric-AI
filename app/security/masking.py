"""Mask sensitive identifiers before anything is written to traces, logs or feedback
(CLAUDE.md section 12).

Order matters:
- GSTIN before PAN, because a GSTIN contains a PAN (27ABCDE1234F1Z5).
- Email before phone/account, so digits inside an address are not split.
- Phone before account number, so a 10-digit mobile number is labelled as a phone.
"""

import json
import re
from typing import Any

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("[GSTIN]", re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")),
    ("[PAN]", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("[EMAIL]", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    # Digit runs must not be part of a decimal number (e.g. a confidence of
    # 0.7692307692307693 in a trace): no "." or digit just before, and no ".digit" after.
    ("[PHONE]", re.compile(r"(?:\+91[\s-]?)?(?<![\d.])\b[6-9]\d{4}[\s-]?\d{5}\b(?!\.\d)")),
    ("[ACCOUNT]", re.compile(r"(?<![\d.])\b\d{9,18}\b(?!\.\d)")),
]


def mask_text(text: str) -> str:
    """Replace PAN, GSTIN, email, phone and bank account numbers with placeholders."""
    for placeholder, pattern in _RULES:
        text = pattern.sub(placeholder, text)
    return text


def mask_json(obj: Any) -> str:
    """Serialise `obj` to JSON and mask it (for trace input/output columns)."""
    return mask_text(json.dumps(obj, ensure_ascii=False, default=str))
