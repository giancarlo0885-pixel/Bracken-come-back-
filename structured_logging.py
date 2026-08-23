from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

from security import is_secret_key, redact_url

STANDARD_LOG_RECORD_KEYS = set(logging.makeLogRecord({}).__dict__)

_FIELD_PATTERNS = {
    "market": re.compile(r"(?:^|[|\s])market=([^|\s,]+)", re.IGNORECASE),
    "symbol": re.compile(r"(?:^|[|\s])symbol=([^|\s,]+)", re.IGNORECASE),
    "provider": re.compile(r"(?:^|[|\s])provider=([^|,]+)", re.IGNORECASE),
    "strategy": re.compile(r"(?:^|[|\s])strategy=([^|,]+)", re.IGNORECASE),
    "scan_type": re.compile(r"(?:^|[|\s])scan_type=([^|\s,]+)", re.IGNORECASE),
    "execution_enabled": re.compile(r"(?:^|[|\s])execution_enabled=([^|\s,]+)", re.IGNORECASE),
}


def _message_field(message: str, name: str) -> str:
    pattern = _FIELD_PATTERNS.get(name)
    if pattern is None:
        return ""
    match = pattern.search(message)
    return redact_url(match.group(1).strip()) if match else ""


def _record_field(record: logging.LogRecord, message: str, name: str) -> Any:
    explicit = getattr(record, name, "")
    if explicit not in (None, ""):
        return _redact_value(explicit)
    return _message_field(message, name)



def _redact_value(value: Any, key_name: Any = None) -> Any:
    if key_name is not None and is_secret_key(key_name):
        return "REDACTED"
    if isinstance(value, str):
        return redact_url(value)
    if isinstance(value, dict):
        return {key: _redact_value(item, key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = redact_url(record.getMessage())
        event = redact_url(getattr(record, "event", message))
        payload: dict[str, Any] = {
            "service": getattr(record, "service", record.name),
            "market": _record_field(record, message, "market"),
            "event": event,
            "symbol": _record_field(record, message, "symbol"),
            "provider": _record_field(record, message, "provider"),
            "strategy": _record_field(record, message, "strategy"),
            "scan_type": _record_field(record, message, "scan_type"),
            "severity": record.levelname,
            "execution_enabled": _record_field(record, message, "execution_enabled"),
            "recommendation_id": getattr(record, "recommendation_id", ""),
            "order_id": getattr(record, "order_id", ""),
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "message": message,
        }
        for key, value in record.__dict__.items():
            if key not in STANDARD_LOG_RECORD_KEYS and key not in payload:
                payload[key] = _redact_value(value)
        if record.exc_info:
            payload["exception"] = redact_url(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_structured_logging(level: str = "INFO") -> None:
    import sys

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(str(level or "INFO").upper())
