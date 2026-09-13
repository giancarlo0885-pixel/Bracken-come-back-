"""Isolated forward paper trials; no portfolio writes or broker submissions.

Two exits share each factual entry: an ATR target/trail and a stop/time control.
Prices come only from fresh verified broker quotes. All friction is explicitly
modeled with Oracle's shared paper-fill model and stored with the trial.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
import time
import uuid
from typing import Any

from entry_patterns import MAX_HOLD_BARS, STRATEGY_VERSION, assess_dip_rebound, finite
from paper_execution_reality import _market_defaults, simulate_fill

log = logging.getLogger("paper-dip-rebound")
TARGET_POLICY = "atr_target_trail_v1"
CONTROL_POLICY = "stop_time_control_v1"
POLICIES = (TARGET_POLICY, CONTROL_POLICY)
MAX_OPEN_SYMBOLS = 10
TRIAL_NOTIONAL = 50.0
QUOTE_FIELDS = ("symbol", "requested_symbol", "provider_symbol", "provider_native_symbol", "provider",
                "price", "bid", "ask", "quote_timestamp", "source_interval", "source_capability", "quote_verified")
_LAST_SUMMARY = 0.0


def active() -> bool:
    truthy = lambda key, default="false": os.getenv(key, default).lower().strip() in {"true", "1", "yes", "on"}
    return (os.getenv("EXECUTION_MODE", "paper").lower().strip() == "paper"
            and truthy("PAPER_DIP_REBOUND_EXPERIMENT", "true") and truthy("PAPER_AUTONOMOUS_LEARNING")
            and not truthy("ENABLE_BROKER_SUBMISSION") and not truthy("LIVE_TRADING_ARMED"))


def _timestamp(value: Any) -> datetime | None:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def verified_quote(symbol: str, quote: Any, now: datetime) -> bool:
    from global_pit_engine import _execution_quote_eligible

    if not isinstance(quote, dict) or quote.get("symbol") != symbol or not symbol.endswith("-USD"):
        return False
    # Historical references and cached position marks cannot fill this experiment.
    if quote.get("provider") != "Robinhood Crypto" or quote.get("source_capability") != "best_bid_ask_realtime":
        return False
    bid, ask, price = (finite(quote.get(key)) for key in ("bid", "ask", "price"))
    stamp = _timestamp(quote.get("quote_timestamp"))
    if bid is None or ask is None or price is None or not (0 < bid <= price <= ask) or stamp is None:
        return False
    if not 0 <= (now - stamp).total_seconds() <= 60 or quote.get("stale") is True:
        return False
    return _execution_quote_eligible(dict(quote, asset_class="crypto"), now=now)


def modeled_fill(side: str, quote: dict[str, Any]) -> dict[str, Any]:
    defaults = _market_defaults("crypto")
    # Supply fractions explicitly; provider/optimizer percentage fields can use
    # different units. A disclosed 5 bps impact assumption requires no invented volume.
    return simulate_fill(
        side=side, market="crypto", reference_price=float(quote["price"]),
        quote={key: quote[key] for key in ("price", "bid", "ask")},
        slippage_pct=defaults["slippage_pct"], fee_pct=defaults["fee_pct"],
        latency_pct=defaults["latency_pct"], market_impact_pct=0.0005,
    ).to_dict()


def plan_entry(pattern: dict[str, Any], quote: dict[str, Any], now: datetime) -> tuple[dict[str, Any] | None, str]:
    assessment = assess_dip_rebound(pattern)
    if not assessment["qualified"]:
        return None, assessment["reason"]
    bar_end = _timestamp(pattern.get("bar_end"))
    quote_at = _timestamp(quote.get("quote_timestamp"))
    if bar_end is None or quote_at is None or not bar_end <= quote_at <= now:
        return None, "quote_before_decision_bar"
    if (now - bar_end).total_seconds() > pattern["bar_minutes"] * 120:
        return None, "expired_pattern"
    if any(finite(pattern.get(key)) is None for key in ("atr", "price", "trough", "prior_peak")) or pattern["atr"] <= 0:
        return None, "missing_price_structure"
    if abs(float(quote["price"]) - pattern["price"]) > 0.75 * pattern["atr"]:
        return None, "price_moved_from_setup"
    buy = modeled_fill("BUY", quote)
    sell_now = modeled_fill("SELL", quote)
    entry = buy["fill_price"]
    stop = pattern["trough"] - 0.25 * pattern["atr"]
    risk = entry - stop
    if not 0 < risk / entry <= 0.03:
        return None, "invalid_or_excessive_stop_distance"
    target = min(pattern["prior_peak"], entry + 2.0 * risk)
    reward = target - entry
    round_trip_cost = 1.0 - sell_now["fill_price"] / entry
    if reward < 1.2 * risk or reward / entry <= 1.25 * round_trip_cost:
        return None, "insufficient_reward_after_costs"
    return {
        "entry": entry, "stop": stop, "target": target, "risk": risk,
        "peak": float(quote["price"]), "deadline": (now + timedelta(minutes=MAX_HOLD_BARS * pattern["bar_minutes"])).isoformat(),
        "entry_fill": buy, "entry_quote": {key: quote[key] for key in QUOTE_FIELDS if key in quote}, "round_trip_cost_fraction": round_trip_cost,
        "cost_model": "shared_paper_fill_plus_5bps_modeled_impact", "notional": TRIAL_NOTIONAL,
        "last_quote_at": quote_at.isoformat(), "pattern": dict(pattern),
        "mfe_pct": 0.0, "mae_pct": 0.0,
    }, "qualified_after_costs"


def exit_decision(state: dict[str, Any], price: float, now: datetime, policy: str) -> tuple[str | None, dict[str, Any]]:
    updated = dict(state)
    prior_peak = float(state["peak"])
    updated["peak"] = max(prior_peak, price)
    move = (price / state["entry"] - 1) * 100
    updated["mfe_pct"] = max(float(state.get("mfe_pct", 0)), move)
    updated["mae_pct"] = min(float(state.get("mae_pct", 0)), move)
    if price <= state["stop"]:
        return "invalidation_stop", updated
    if policy == TARGET_POLICY:
        if price >= state["target"]:
            return "rebound_target", updated
        if prior_peak >= state["entry"] + state["risk"] and price <= prior_peak - state["risk"]:
            return "profit_trail", updated
    if now >= _timestamp(state["deadline"]):
        return "time_exit", updated
    return None, updated


def ensure_schema() -> None:
    from database import connect

    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('paper_dip_rebound_schema_v1'))")
        conn.execute("""CREATE TABLE IF NOT EXISTS paper_dip_rebound_observations (
            id BIGSERIAL PRIMARY KEY, version TEXT NOT NULL, symbol TEXT NOT NULL,
            bar_end TIMESTAMPTZ NOT NULL, interval_minutes INTEGER NOT NULL,
            qualified BOOLEAN NOT NULL, reason TEXT NOT NULL, features JSONB NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL,
            UNIQUE(version,symbol,bar_end,interval_minutes))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS paper_dip_rebound_trials (
            trial_id TEXT PRIMARY KEY, version TEXT NOT NULL, symbol TEXT NOT NULL,
            bar_end TIMESTAMPTZ NOT NULL, interval_minutes INTEGER NOT NULL, policy TEXT NOT NULL,
            entry_at TIMESTAMPTZ NOT NULL, exit_at TIMESTAMPTZ, state JSONB NOT NULL,
            exit_reason TEXT, exit_fill JSONB, net_return_pct DOUBLE PRECISION,
            UNIQUE(version,symbol,bar_end,interval_minutes,policy))""")
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_dip_rebound_one_open
            ON paper_dip_rebound_trials(version,symbol,policy) WHERE exit_at IS NULL""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dip_rebound_observed ON paper_dip_rebound_observations(observed_at)")


def open_symbols() -> list[str]:
    if not active():
        return []
    try:
        from database import rows
        return [item["symbol"] for item in rows(
            "SELECT DISTINCT symbol FROM paper_dip_rebound_trials WHERE version=%s AND exit_at IS NULL ORDER BY symbol LIMIT %s",
            (STRATEGY_VERSION, MAX_OPEN_SYMBOLS),
        )]
    except Exception:
        return []


def observe(signals: list[Any], prices: dict[str, Any], *, now: datetime | None = None) -> dict[str, int]:
    counts = {"observed": 0, "opened": 0, "closed": 0}
    if not active():
        return counts
    from database import connect

    current = now or datetime.now(timezone.utc)
    with connect() as conn:
        locked = conn.execute("SELECT pg_try_advisory_xact_lock(hashtext('paper_dip_rebound_cycle_v1')) AS locked").fetchone()
        if not locked["locked"]:
            return counts
        opened = list(conn.execute("SELECT * FROM paper_dip_rebound_trials WHERE version=%s AND exit_at IS NULL FOR UPDATE", (STRATEGY_VERSION,)).fetchall())
        # A closed experiment cannot re-enter on the same observation cycle.
        occupied = {row["symbol"] for row in opened}
        for trial in opened:
            quote = prices.get(trial["symbol"])
            if not verified_quote(trial["symbol"], quote, current):
                continue
            state = trial["state"]
            quote_at = _timestamp(quote["quote_timestamp"])
            if quote_at <= _timestamp(state["last_quote_at"]):
                continue
            reason, updated = exit_decision(state, float(quote["price"]), current, trial["policy"])
            updated["last_quote_at"] = quote_at.isoformat()
            if reason:
                fill = modeled_fill("SELL", quote)
                net_return = (fill["fill_price"] / state["entry"] - 1) * 100
                fill["quote"] = {key: quote[key] for key in QUOTE_FIELDS if key in quote}
                conn.execute("""UPDATE paper_dip_rebound_trials SET exit_at=%s, state=%s::jsonb,
                    exit_reason=%s, exit_fill=%s::jsonb, net_return_pct=%s WHERE trial_id=%s AND exit_at IS NULL""",
                    (current, json.dumps(updated, default=str), reason, json.dumps(fill, default=str), net_return, trial["trial_id"]))
                counts["closed"] += 1
            else:
                conn.execute("UPDATE paper_dip_rebound_trials SET state=%s::jsonb WHERE trial_id=%s", (json.dumps(updated, default=str), trial["trial_id"]))
        for signal in signals:
            value = lambda name: signal.get(name) if isinstance(signal, dict) else getattr(signal, name, None)
            symbol = str(value("symbol") or "").upper()
            pattern = value("entry_pattern")
            if not isinstance(pattern, dict) or pattern.get("available") is not True or pattern.get("bar_minutes") not in (5, 15):
                continue
            assessment = assess_dip_rebound(pattern)
            quote = prices.get(symbol)
            plan, reason = (plan_entry(pattern, quote, current) if verified_quote(symbol, quote, current)
                            else (None, "fresh_verified_broker_quote_required"))
            inserted = conn.execute("""INSERT INTO paper_dip_rebound_observations
                (version,symbol,bar_end,interval_minutes,qualified,reason,features,observed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING RETURNING id""",
                (STRATEGY_VERSION, symbol, pattern["bar_end"], pattern["bar_minutes"], bool(plan), reason,
                 json.dumps({"pattern": pattern, "assessment": assessment}, allow_nan=False), current)).fetchone()
            if inserted:
                counts["observed"] += 1
            # A single completed bar gets exactly one decision/fill opportunity.
            if not inserted or not plan or symbol in occupied or len(occupied) >= MAX_OPEN_SYMBOLS:
                continue
            for policy in POLICIES:
                conn.execute("""INSERT INTO paper_dip_rebound_trials
                    (trial_id,version,symbol,bar_end,interval_minutes,policy,entry_at,state)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                    (f"dip:{uuid.uuid4()}", STRATEGY_VERSION, symbol, pattern["bar_end"], pattern["bar_minutes"], policy, current, json.dumps(plan, default=str, allow_nan=False)))
                counts["opened"] += 1
            occupied.add(symbol)
    log.info("PAPER DIP REBOUND | version=%s | observed=%d | opened=%d | closed=%d | mode=forward_paper_experiment | broker_submission=NONE",
             STRATEGY_VERSION, counts["observed"], counts["opened"], counts["closed"])
    return counts


def summarize_and_retain() -> None:
    global _LAST_SUMMARY
    if not active() or time.monotonic() - _LAST_SUMMARY < 300:
        return
    from database import connect
    with connect() as conn:
        # Only bounded research tables are cleaned; canonical trades and lots are untouched.
        conn.execute("""DELETE FROM paper_dip_rebound_observations WHERE observed_at < NOW()-INTERVAL '7 days'
            OR id IN (SELECT id FROM paper_dip_rebound_observations ORDER BY id DESC OFFSET 50000)""")
        conn.execute("""DELETE FROM paper_dip_rebound_trials WHERE exit_at IS NOT NULL
            AND (exit_at < NOW()-INTERVAL '90 days' OR trial_id IN
            (SELECT trial_id FROM paper_dip_rebound_trials WHERE exit_at IS NOT NULL ORDER BY exit_at DESC OFFSET 10000))""")
        stats = list(conn.execute("""SELECT policy, COUNT(*) AS trials,
            COUNT(*) FILTER (WHERE exit_at IS NOT NULL) AS closed,
            AVG(net_return_pct) AS net_expectancy_pct,
            AVG((net_return_pct > 0)::integer) AS win_rate
            FROM paper_dip_rebound_trials WHERE version=%s GROUP BY policy""", (STRATEGY_VERSION,)).fetchall())
        reasons = list(conn.execute("""SELECT reason, COUNT(*) AS samples FROM paper_dip_rebound_observations
            WHERE version=%s AND observed_at > NOW()-INTERVAL '1 hour' GROUP BY reason ORDER BY samples DESC LIMIT 5""", (STRATEGY_VERSION,)).fetchall())
        paired = conn.execute("""SELECT COUNT(*) AS completed_pairs,
            AVG(a.net_return_pct-b.net_return_pct) AS exit_advantage_pct
            FROM paper_dip_rebound_trials a JOIN paper_dip_rebound_trials b
              ON a.version=b.version AND a.symbol=b.symbol AND a.bar_end=b.bar_end AND a.interval_minutes=b.interval_minutes
            WHERE a.version=%s AND a.policy=%s AND b.policy=%s AND a.exit_at IS NOT NULL AND b.exit_at IS NOT NULL""",
            (STRATEGY_VERSION, TARGET_POLICY, CONTROL_POLICY)).fetchone()
    _LAST_SUMMARY = time.monotonic()
    log.info("PAPER DIP REBOUND SUMMARY | version=%s | policies=%s | paired=%s | recent_reasons=%s | promotion=NONE | mode=forward_paper_experiment",
             STRATEGY_VERSION, json.dumps(stats, default=str), json.dumps(paired, default=str), json.dumps(reasons, default=str))


def maintain(signals: list[Any], prices: dict[str, Any]) -> None:
    if not active():
        return
    try:
        observe(signals, prices)
        summarize_and_retain()
    except Exception as exc:
        log.warning("PAPER DIP REBOUND | status=UNAVAILABLE | reason=%s", exc.__class__.__name__)


def initialize() -> bool:
    if not active():
        return False
    ensure_schema()
    log.info("PAPER DIP REBOUND | active=True | version=%s | policies=%s | max_open_symbols=%d | notional_per_trial=%.2f | broker_submission=NONE | promotion=NONE",
             STRATEGY_VERSION, ",".join(POLICIES), MAX_OPEN_SYMBOLS, TRIAL_NOTIONAL)
    return True
