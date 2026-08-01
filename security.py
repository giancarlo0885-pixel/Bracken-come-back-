from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SECRET_KEYS = {"api_token", "apikey", "api_key", "token", "key", "authorization", "broker_token", "secret", "password"}


def redact_url(value: Any) -> str:
    text = str(value or "")

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parts = urlsplit(raw)
            query = urlencode(
                [
                    (key, "REDACTED" if key.lower() in SECRET_KEYS else val)
                    for key, val in parse_qsl(parts.query, keep_blank_values=True)
                ]
            )
            return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
        except Exception:
            return raw

    redacted = re.sub(r"https?://[^\s)]+", replace, text)
    for key in SECRET_KEYS:
        redacted = re.sub(rf"(?i)(^|[?&\s])({re.escape(key)}=)[^&\s)]+", rf"\1\2REDACTED", redacted)
        redacted = re.sub(rf"(?i)({re.escape(key)}:\s*)[^\s,;]+", rf"\1REDACTED", redacted)
    return redacted


def redact_headers(headers: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (headers or {}).items():
        safe[key] = "REDACTED" if str(key).lower() in SECRET_KEYS else value
    return safe


def safe_exception(exc: Exception) -> str:
    return redact_url(str(exc))[:500]
