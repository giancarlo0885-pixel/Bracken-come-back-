from __future__ import annotations

import json
from typing import Any


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def normalized_confidence(value: Any) -> float:
    number = as_float(value, 0.0)
    if number <= 1:
        number *= 100
    return max(0.0, min(100.0, number))


def normalized_score(value: Any) -> float:
    return normalized_confidence(value)


def star_rating(score: Any) -> str:
    number = normalized_score(score)
    filled = max(1, min(5, round(number / 20)))
    return "★" * filled + "☆" * (5 - filled)


def action_class(action: Any) -> str:
    action_text = str(action or "HOLD").upper()
    if action_text == "BUY":
        return "buy"
    if action_text == "SELL":
        return "sell"
    return "hold"


def clean_market(value: Any) -> str:
    text = str(value or "").lower()
    return "stock" if text in {"cash", "stock"} else "crypto" if text == "crypto" else text


def short_reason(record: Any, max_length: int = 180) -> str:
    """Return a readable, safely truncated reason from either a record or text.

    Older dashboard code passed a plain reason string plus a length limit, while
    ranking cards pass a database record. Supporting both forms prevents the
    dashboard from crashing during mixed-version deployments.
    """
    fallback = "Ranked from council score, momentum, confidence, volume, and risk."

    if isinstance(record, dict):
        payload = parse_json(record.get("payload"))
        text = ""
        for key in ("reason", "explanation", "summary"):
            candidate = record.get(key) or payload.get(key)
            if candidate:
                text = str(candidate)
                break
        text = text or fallback
    elif record is None:
        text = fallback
    else:
        text = str(record).strip() or fallback

    try:
        limit = max(12, int(max_length))
    except (TypeError, ValueError):
        limit = 180

    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def worker_is_online(status: Any) -> bool:
    """Return True only for states that mean the process is actively healthy.

    ``stopped`` and unknown values used to be counted as online, which could
    hide dead Railway workers from Mission Control.
    """
    return str(status or "").strip().lower() in {"starting", "running", "idle", "healthy", "online"}
