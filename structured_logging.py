from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "service": getattr(record, "service", record.name),
            "market": getattr(record, "market", ""),
            "event": getattr(record, "event", record.getMessage()),
            "symbol": getattr(record, "symbol", ""),
            "provider": getattr(record, "provider", ""),
            "scan_type": getattr(record, "scan_type", ""),
            "severity": record.levelname,
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_structured_logging(level: str = "INFO") -> None:
    import sys

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(str(level or "INFO").upper())
