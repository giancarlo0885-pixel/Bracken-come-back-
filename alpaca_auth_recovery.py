"""One budgeted, data-only verification of replacement Alpaca credentials.

No key, secret, fingerprint or response body leaves this module's private audit.
An unchanged legacy 401 can expire only after validated IEX bars are received.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import math
import os

import pandas as pd

from config import (ALPACA_IEX_DAILY_REQUEST_BUDGET, PROVIDER_PERMISSION_COOLDOWN_SECONDS,
                    PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS)
from global_adaptive_engine import mark_provider_cooldown_db, reserve_provider_budget_db

log = logging.getLogger("alpaca-auth-recovery")
PROVIDER = "Alpaca IEX"
AUTH_REASON = "provider authentication failed (HTTP 401)"
LOCK_ID = 73420261601
GUARD_KEYS = ("utc_date", "last_failure", "cooldown_until", "updated_at")


def _dt(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _active(conn, now):
    records = conn.execute("""SELECT utc_date,last_failure,cooldown_until,updated_at
        FROM provider_budget_ledger WHERE provider=%s AND capability='__provider__'
        AND cooldown_until IS NOT NULL ORDER BY utc_date FOR UPDATE""", (PROVIDER,)).fetchall()
    if any(_dt(row["cooldown_until"]) is None for row in records):
        raise ValueError("invalid cooldown state")
    return [dict(row) for row in records if _dt(row["cooldown_until"]) > now]


def _eligible(records, now):
    return bool(records) and all(
        row["last_failure"] == AUTH_REASON and _dt(row["updated_at"]) is not None
        and (now - _dt(row["updated_at"])).total_seconds() >= PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS
        and (_dt(row["cooldown_until"]) - _dt(row["updated_at"])).total_seconds() == PROVIDER_PERMISSION_COOLDOWN_SECONDS
        for row in records)


def _guards(records):
    return [tuple(row[key] for key in GUARD_KEYS) for row in records]


def _valid_bars(frame, now):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return False
    if any(frame.attrs.get(key) != value for key, value in {
            "provider": PROVIDER, "requested_symbol": "MSFT", "provider_symbol": "MSFT", "feed": "iex"}.items()):
        return False
    columns = ["Open", "High", "Low", "Close", "Volume"]
    if not set(columns).issubset(frame.columns) or not isinstance(frame.index, pd.DatetimeIndex):
        return False
    if frame.index.tz is None or frame.index.hasnans or frame.index.max() > pd.Timestamp(now) + pd.Timedelta(minutes=1):
        return False
    try:
        if not all(not pd.api.types.is_bool(value) and math.isfinite(float(value))
                   for value in frame[columns].to_numpy().flat):
            return False
        return (not (frame[columns[:4]] <= 0).any().any()
                and not (frame["Volume"] < 0).any()
                and not (frame["High"] < frame[["Open", "Close", "Low"]].max(axis=1)).any()
                and not (frame["Low"] > frame[["Open", "Close", "High"]].min(axis=1)).any())
    except (TypeError, ValueError, OverflowError):
        return False


class _BudgetBlocked(Exception):
    pass


def _result(status, http_status=None, rows=0):
    result = {"status": status, "http_status": http_status, "rows": rows, "execution_impact": "NONE"}
    log.info("ALPACA AUTH RECOVERY | status=%s | http_status=%s | validated_rows=%s | execution_impact=NONE",
             status, http_status, rows)
    return result


def recover_alpaca_authentication(*, connect_factory=None, fetch_history=None, now_fn=None):
    """Durably claim once per pair; never clear unknown or changed cooldowns."""
    key, secret = (os.getenv(name, "").strip() for name in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"))
    if not key or not secret:
        return _result("NOT_CONFIGURED")
    log.info(
        "ALPACA IEX CONNECTION | endpoint=CANONICAL | feed=iex | legacy_endpoint_ignored=%s | legacy_feed_ignored=%s | execution_impact=NONE",
        os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/") != "https://data.alpaca.markets",
        os.getenv("ALPACA_DATA_FEED", "iex").strip().lower() != "iex",
    )
    if (os.getenv("EXECUTION_MODE", "paper").strip().lower() != "paper"
            or any(os.getenv(name, "false").strip().lower() == "true"
                   for name in ("ENABLE_BROKER_SUBMISSION", "LIVE_TRADING_ARMED"))):
        return _result("SAFETY_BLOCKED")
    if connect_factory is None:
        if not os.getenv("DATABASE_URL", "").strip():
            return _result("DATABASE_UNAVAILABLE")
        from database import connect
        connect_factory = connect
    if fetch_history is None:
        from alpaca_iex_provider import iex_stock_history
        fetch_history = iex_stock_history
    if now_fn is None:
        now_fn = lambda: datetime.now(timezone.utc)
    fingerprint = hmac.new(secret.encode(), b"oracle-alpaca-auth-v1\0" + key.encode(), hashlib.sha256).hexdigest()
    try:
        from provider_capabilities import capability_available
        if not all(capability_available(PROVIDER, name) for name in ("us_history", "daily_history")):
            return _result("CAPABILITY_BLOCKED")
        started = now_fn()
        with connect_factory() as conn:
            conn.execute("SET LOCAL statement_timeout='5000ms'")
            if not conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS locked", (LOCK_ID,)).fetchone()["locked"]:
                return _result("CHECK_IN_PROGRESS")
            before = _active(conn, started)
            if not before:
                return _result("NO_AUTH_COOLDOWN")
            conn.execute("""CREATE TABLE IF NOT EXISTS alpaca_auth_recovery_receipts (
                credential_id TEXT PRIMARY KEY,status TEXT NOT NULL,
                started_at TIMESTAMPTZ NOT NULL,completed_at TIMESTAMPTZ,receipt JSONB NOT NULL)""")
            if conn.execute("SELECT 1 FROM alpaca_auth_recovery_receipts WHERE credential_id=%s", (fingerprint,)).fetchone():
                return _result("ALREADY_CHECKED")
            if not _eligible(before, started):
                return _result("COOLDOWN_PRESERVED")
            guard = next((row for row in before if row["utc_date"] == started.date().isoformat()), None)
            wide = reserve_provider_budget_db(conn, PROVIDER, "__provider__",
                daily_budget=ALPACA_IEX_DAILY_REQUEST_BUDGET, now=started, auth_recheck_guard=guard)
            history = reserve_provider_budget_db(conn, PROVIDER, "us_history",
                daily_budget=ALPACA_IEX_DAILY_REQUEST_BUDGET, now=started)
            if not wide.get("reserved") or not history.get("reserved"):
                raise _BudgetBlocked()
            expected = _active(conn, started)
            proof = {"original_cooldowns": before, "reserved_cooldowns": expected,
                     "provider_remaining": wide.get("remaining"), "history_remaining": history.get("remaining"),
                     "provider": PROVIDER, "symbol": "MSFT", "period": "5d", "interval": "1d",
                     "feed": "iex", "max_http_requests": 1, "execution_impact": "NONE"}
            conn.execute("""INSERT INTO alpaca_auth_recovery_receipts
                (credential_id,status,started_at,receipt) VALUES (%s,'CLAIMED',%s,%s::jsonb)""",
                (fingerprint, started, json.dumps(proof)))
        # A committed claim survives a crash; another worker/restart cannot
        # issue this pair's request again, even across a UTC date boundary.
        status, http_status, rows = "INVALID_OR_EMPTY_DATA", None, 0
        try:
            frame = fetch_history("MSFT", "5d", "1d", max_pages=1)
            if _valid_bars(frame, now_fn()):
                status, http_status, rows = "AUTHENTICATED_IEX", 200, len(frame)
        except Exception as exc:
            code = getattr(exc, "status_code", None)
            http_status = code if type(code) is int and 100 <= code <= 599 else None
            status = "AUTHENTICATION_FAILED" if code == 401 else "RATE_LIMITED" if code == 429 else "REQUEST_FAILED"
        completed = now_fn()
        with connect_factory() as conn:
            conn.execute("SET LOCAL statement_timeout='5000ms'")
            current = _active(conn, completed)
            if status == "AUTHENTICATED_IEX":
                if _guards(current) != _guards(expected):
                    status = "CONCURRENT_CHANGE_PRESERVED"
                else:
                    for row in expected:
                        changed = conn.execute("""UPDATE provider_budget_ledger SET cooldown_until=%s,updated_at=%s
                            WHERE provider=%s AND capability='__provider__' AND utc_date=%s
                            AND last_failure=%s AND cooldown_until=%s AND updated_at=%s""",
                            (completed.isoformat(), completed.isoformat(), PROVIDER,
                             *(row[name] for name in GUARD_KEYS)))
                        if changed.rowcount != 1:
                            raise RuntimeError("cooldown comparison failed")
            elif http_status in {401, 429}:
                mark_provider_cooldown_db(conn, PROVIDER, now=completed,
                    seconds=PROVIDER_PERMISSION_COOLDOWN_SECONDS if http_status == 401 else PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS,
                    reason=AUTH_REASON if http_status == 401 else "Alpaca rate limited (HTTP 429)")
            conn.execute("""UPDATE alpaca_auth_recovery_receipts
                SET status=%s,completed_at=%s,receipt=receipt || %s::jsonb WHERE credential_id=%s""",
                (status, completed, json.dumps({"status": status, "http_status": http_status,
                 "validated_rows": rows, "cooldowns_before_completion": current}), fingerprint))
        return _result(status, http_status, rows)
    except _BudgetBlocked:
        return _result("BUDGET_BLOCKED")
    except Exception:
        return _result("UNAVAILABLE")
