"""Remove secrets from run snapshots before they are written (R-ops-secrets; M20 T1).

Every `config.json` in eval/results/ is committed to a public repository, so every writer
builds it with `snapshot_json`:
- a setting with a `password`, `secret`, `token` or `api_key` name part is written as "***"
  (a `*_ref` setting only names where a secret is, so it stays; an unset value stays too).
  Name parts are matched, so `budget_max_tokens` and `precise_keyword_weight` are kept;
- credentials inside any URL are removed, whatever the setting is called.
"""

import json
import re
from collections.abc import Mapping
from typing import Any

REDACTED = "***"
_SECRET_NAME = re.compile(r"(?:^|_)(?:password|secret|token|api_key)(?:_|$)", re.IGNORECASE)
# Up to the LAST "@": database drivers accept "@", "/", "?" and spaces inside a password.
_URL_USERINFO = re.compile(r"://.*@", re.DOTALL)
_URL_PARAM = re.compile(r"([?&][^=&]*(?:password|secret|token|api_key)=)[^&]*", re.IGNORECASE)


def is_secret(name: str) -> bool:
    """True if a setting of this name holds a secret value."""
    return bool(_SECRET_NAME.search(name)) and not name.lower().endswith("_ref")


def redact(value: Any) -> Any:
    """A copy of `value` (settings, nested dicts and lists, strings) without secrets."""
    if isinstance(value, Mapping):
        return {k: REDACTED if is_secret(str(k)) and v not in (None, "") else redact(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str) and "://" in value:
        return _URL_PARAM.sub(rf"\g<1>{REDACTED}", _URL_USERINFO.sub("://", value))
    return value


def snapshot_json(config: Mapping[str, Any]) -> str:
    """The text of a run's config.json."""
    return json.dumps(redact(config), indent=2, default=str)
