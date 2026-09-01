from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from config import ENABLE_NEWS
from database import row, rows


_HEALTHY = {"ok", "pass", "healthy", "ready", "available", "configured"}
_CONFIRMED = {"pass", "verified", "confirmed", "agreement", "ok"}
_REJECTED = {"reject", "rejected", "blocked", "failed", "fail"}


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def provider_health_summary(*, maximum_age_minutes: int = 90) -> dict[str, Any]:
    try:
        records = rows(
            """
            SELECT provider, configured, status, latency_ms, message, checked_at
            FROM provider_health
            ORDER BY provider
            """
        )
    except Exception as exc:
        return {"ok": False, "status": "UNAVAILABLE", "reason": exc.__class__.__name__, "providers": []}

    now = datetime.now(timezone.utc)
    details: list[dict[str, Any]] = []
    healthy_count = 0
    configured_count = 0
    for item in records:
        configured = bool(item.get("configured"))
        configured_count += 1 if configured else 0
        checked = _parse_ts(item.get("checked_at"))
        age_minutes = None if checked is None else max(0.0, (now - checked).total_seconds() / 60.0)
        status = str(item.get("status") or "").strip().lower()
        healthy = configured and status in _HEALTHY and age_minutes is not None and age_minutes <= maximum_age_minutes
        healthy_count += 1 if healthy else 0
        details.append(
            {
                "provider": str(item.get("provider") or ""),
                "configured": configured,
                "status": status or "unknown",
                "age_minutes": age_minutes,
                "healthy": healthy,
            }
        )

    ok = configured_count > 0 and healthy_count > 0
    return {
        "ok": ok,
        "status": "PASS" if ok else "INSUFFICIENT_PROVIDER_HEALTH",
        "configured_providers": configured_count,
        "healthy_providers": healthy_count,
        "providers": details,
    }


def quote_integrity_summary(*, lookback_hours: int = 24, maximum_divergence_pct: float = 1.0) -> dict[str, Any]:
    """Assess whether execution consensus is behaving safely.

    A divergent quote that was *rejected* is evidence that the consensus guard
    worked and must not poison capital readiness for the entire lookback window.
    The hard failure is a divergent observation that was nevertheless marked as
    confirmed/accepted. We still surface rejected/divergent counts for audit.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(lookback_hours)))
    try:
        stats = row(
            """
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (
                    WHERE LOWER(COALESCE(consensus_status,'')) IN ('pass','verified','confirmed','agreement','ok')
                )::int AS confirmed,
                COUNT(*) FILTER (
                    WHERE LOWER(COALESCE(consensus_status,'')) IN ('reject','rejected','blocked','failed','fail')
                )::int AS rejected,
                COUNT(*) FILTER (
                    WHERE difference_pct IS NOT NULL
                      AND difference_pct > %s
                      AND LOWER(COALESCE(consensus_status,'')) IN ('pass','verified','confirmed','agreement','ok')
                )::int AS unsafe_confirmed_divergent,
                COUNT(*) FILTER (
                    WHERE difference_pct IS NOT NULL
                      AND difference_pct > %s
                      AND LOWER(COALESCE(consensus_status,'')) IN ('reject','rejected','blocked','failed','fail')
                )::int AS safely_rejected_divergent
            FROM quote_verifications
            WHERE created_at >= %s
            """,
            (float(maximum_divergence_pct), float(maximum_divergence_pct), cutoff.isoformat()),
        ) or {}
    except Exception as exc:
        return {"ok": False, "status": "UNAVAILABLE", "reason": exc.__class__.__name__}

    total = int(stats.get("total") or 0)
    confirmed = int(stats.get("confirmed") or 0)
    rejected = int(stats.get("rejected") or 0)
    unsafe = int(stats.get("unsafe_confirmed_divergent") or 0)
    safely_rejected = int(stats.get("safely_rejected_divergent") or 0)
    # Zero samples is UNKNOWN, never silently PASS. Rejected divergence is safe
    # only because the execution guard prevented it from becoming accepted truth.
    ok = total > 0 and confirmed > 0 and unsafe == 0
    return {
        "ok": ok,
        "status": "PASS" if ok else ("INSUFFICIENT_EVIDENCE" if total == 0 or confirmed == 0 else "FAIL_CLOSED"),
        "sample_count": total,
        "confirmed": confirmed,
        "rejected": rejected,
        "unsafe_confirmed_divergent": unsafe,
        "safely_rejected_divergent": safely_rejected,
        # Compatibility field: only divergences that escaped the rejection gate
        # count as readiness-failing divergence.
        "divergent": unsafe,
        "maximum_divergence_pct": float(maximum_divergence_pct),
    }


def news_integrity_summary(*, maximum_age_hours: int = 24) -> dict[str, Any]:
    if not ENABLE_NEWS:
        return {"ok": True, "status": "NOT_REQUIRED", "reason": "news disabled by configuration"}
    try:
        latest = row(
            """
            SELECT provider, event_time, created_at
            FROM intelligence_events
            WHERE category IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """
        ) or {}
    except Exception as exc:
        return {"ok": False, "status": "UNAVAILABLE", "reason": exc.__class__.__name__}
    stamp = _parse_ts(latest.get("event_time")) or _parse_ts(latest.get("created_at"))
    if stamp is None:
        return {"ok": False, "status": "INSUFFICIENT_EVIDENCE", "provider": None, "age_hours": None}
    age_hours = max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds() / 3600.0)
    ok = age_hours <= max(1, int(maximum_age_hours))
    return {
        "ok": ok,
        "status": "PASS" if ok else "STALE",
        "provider": str(latest.get("provider") or "UNKNOWN"),
        "age_hours": age_hours,
    }


def capital_data_health() -> dict[str, Any]:
    providers = provider_health_summary()
    quotes = quote_integrity_summary()
    news = news_integrity_summary()
    # News is a contextual evidence source. If enabled and unhealthy, the Oracle
    # must abstain from claims that require news, but market-price integrity is
    # the hard capital gate here.
    hard_ok = bool(providers.get("ok")) and bool(quotes.get("ok"))
    return {
        "ok": hard_ok,
        "status": "PASS" if hard_ok else "FAIL_CLOSED",
        "providers": providers,
        "quote_integrity": quotes,
        "news": news,
    }
