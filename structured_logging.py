from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any

from security import redact_url

STANDARD_LOG_RECORD_KEYS = set(logging.makeLogRecord({}).__dict__)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_url(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = redact_url(record.getMessage())
        event = redact_url(getattr(record, "event", message))
        payload: dict[str, Any] = {
            "service": getattr(record, "service", record.name),
            "market": getattr(record, "market", ""),
            "event": event,
            "symbol": getattr(record, "symbol", ""),
            "provider": getattr(record, "provider", ""),
            "strategy": getattr(record, "strategy", ""),
            "scan_type": getattr(record, "scan_type", ""),
            "severity": record.levelname,
            "execution_enabled": getattr(record, "execution_enabled", ""),
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
