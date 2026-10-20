"""Read-only, bounded shared-provider budget observability for research logs.

This never reserves, resets, unlocks or changes provider request capacity.
The configured ledger is not proof of a provider's external API entitlement.
"""
from datetime import datetime, timezone
import json
import logging

log = logging.getLogger("public-research-budget")
PROVIDERS = ("Coinbase Public Research", "Kraken Public Research")


def _safe_count(value):
    if isinstance(value, bool):
        return None
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return count if count >= 0 and str(value).strip() == str(count) else None


def _cooldown_active(raw, now):
    if raw is None or raw == "":
        return False
    try:
        when = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if when.tzinfo is None:
            return None
        return when.astimezone(timezone.utc) > now
    except (ValueError, TypeError, OverflowError):
        return None


def read_public_research_budgets(conn, *, now=None):
    """Report the two configured provider-wide ledgers for exactly one UTC day."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows = None
    try:
        rows = conn.execute(
            """SELECT provider, daily_budget, requests_used, remaining_budget,
                      cooldown_until
               FROM provider_budget_ledger
               WHERE utc_date=%s AND capability='__provider__'
                 AND provider IN ('Coinbase Public Research','Kraken Public Research')
               LIMIT 2""",
            (current.date().isoformat(),),
        ).fetchall()
    except Exception as exc:
        return {"status": "LEDGER_UNAVAILABLE", "error_class": type(exc).__name__,
                "observed_at": current.isoformat(), "execution_impact": "NONE"}
    if not isinstance(rows, (list, tuple)):
        return {"status": "LEDGER_UNAVAILABLE", "error_class": "INVALID_RESULT",
                "observed_at": current.isoformat(), "execution_impact": "NONE"}

    by_provider = {}
    for row in rows:
        try:
            provider = row["provider"]
            if provider not in PROVIDERS or provider in by_provider:
                return {"status": "LEDGER_INCONSISTENT", "observed_at": current.isoformat(),
                        "execution_impact": "NONE"}
            budget = _safe_count(row["daily_budget"])
            used = _safe_count(row["requests_used"])
            remaining = _safe_count(row["remaining_budget"])
            cooldown = _cooldown_active(row["cooldown_until"], current)
            if (budget is None or used is None or remaining is None
                    or used + remaining != budget or cooldown is None):
                result = {"status": "LEDGER_INCONSISTENT"}
            else:
                # mark_provider_cooldown_db can create a same-day ledger row
                # with (daily_budget,requests_used,remaining_budget) = (0,0,0)
                # before any quota reservation occurs. That row is not proof
                # that a configured provider allowance was consumed.
                state = ("ZERO_CONFIGURED_BUDGET" if budget == 0 else
                         "CONFIGURED_COOLDOWN_ACTIVE" if cooldown else
                         "CONFIGURED_BUDGET_EXHAUSTED" if remaining == 0 else
                         "CONFIGURED_CAPACITY_REMAINING")
                result = {"status": state, "configured_daily_budget": budget,
                          "requests_reserved": used, "remaining_reservations": remaining,
                          "cooldown_active": cooldown,
                          "exhaustion_proven_by_ledger": bool(budget > 0 and remaining == 0
                                                              and used == budget)}
            by_provider[provider] = result
        except (TypeError, KeyError, ValueError):
            return {"status": "LEDGER_INCONSISTENT", "observed_at": current.isoformat(),
                    "execution_impact": "NONE"}

    for provider in PROVIDERS:
        by_provider.setdefault(provider, {"status": "LEDGER_ROW_ABSENT"})
    return {"status": "READ_ONLY_SNAPSHOT", "observed_at": current.isoformat(),
            "provider_api_entitlements": "UNVERIFIED",
            "providers": by_provider, "execution_impact": "NONE"}


def emit_public_research_budget_report(conn, *, now=None):
    result = read_public_research_budgets(conn, now=now)
    log.info("PUBLIC RESEARCH BUDGET | %s", json.dumps(result, sort_keys=True))
    return result
