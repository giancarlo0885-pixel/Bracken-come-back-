from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SECRET_KEYS = {
    "api_token",
    "apikey",
    "api_key",
    "token",
    "key",
    "authorization",
    "broker_token",
    "secret",
    "password",
    "cookie",
    "set-cookie",
    "x-api-key",
    "proxy-authorization",
}


def _normalized_secret_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


NORMALIZED_SECRET_KEYS = {_normalized_secret_key(key) for key in SECRET_KEYS}


def is_secret_key(value: Any) -> bool:
    normalized = _normalized_secret_key(value)
    return bool(
        normalized in NORMALIZED_SECRET_KEYS
        or normalized.endswith("apikey")
        or normalized.endswith("token")
        or normalized.endswith("password")
        or normalized.endswith("secret")
    )


def redact_url(value: Any) -> str:
    text = str(value or "")

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parts = urlsplit(raw)
            netloc = parts.netloc
            if "@" in netloc:
                credentials, host = netloc.rsplit("@", 1)
                if ":" in credentials:
                    user, _ = credentials.split(":", 1)
                    credentials = f"{user}:REDACTED"
                else:
                    credentials = "REDACTED"
                netloc = f"{credentials}@{host}"
            query = urlencode(
                [
                    (key, "REDACTED" if is_secret_key(key) else val)
                    for key, val in parse_qsl(parts.query, keep_blank_values=True)
                ]
            )
            return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
        except Exception:
            return raw

    redacted = re.sub(r"(?i)\b(?:https?|postgresql?|mysql|redis|mongodb)://[^\s)]+", replace, text)
    for key in SECRET_KEYS:
        redacted = re.sub(rf"(?i)(^|[?&\s\"'])({re.escape(key)}=)[^&\s)\"']+", rf"\1\2REDACTED", redacted)
        redacted = re.sub(rf"(?i)({re.escape(key)}:\s*)[^\s,;]+", rf"\1REDACTED", redacted)
    redacted = re.sub(r"(?i)(authorization:\s*bearer\s+)[^\s,;]+", r"\1REDACTED", redacted)
    redacted = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{4,}", r"\1REDACTED", redacted)
    redacted = re.sub(r"(?i)(authorization:\s*REDACTED)\s+[^\s,;]+", r"\1", redacted)
    return redacted


def redact_headers(headers: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (headers or {}).items():
        safe[key] = "REDACTED" if is_secret_key(key) else value
    return safe


def safe_exception(exc: Exception) -> str:
    return redact_url(str(exc))[:500]
