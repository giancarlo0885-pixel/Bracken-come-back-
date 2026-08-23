from __future__ import annotations

import json
import logging
import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import math
from threading import Event, Lock
from typing import Any

from config import (
    ALWAYS_ON_TRADING,
    DEEP_ANALYSIS_CANDIDATES,
    ENABLE_AUTOTRADE,
    ENABLE_AUTOMATED_EXITS,
    ENABLE_BROKER_SUBMISSION,
    ENABLE_CRYPTO_AUTOTRADE,
    ENABLE_NEW_ENTRIES,
    ENABLE_PORTFOLIO_ROTATION,
    ENABLE_STOCK_AUTOTRADE,
    GLOBAL_KILL_SWITCH,
    GLOBAL_PIT_MODE,
    EXECUTION_MODE,
    FAST_SCAN_BATCH_SIZE,
    FAST_SCAN_TOP_RANKED,
    FAST_SIGNAL_SCAN_ENABLED,
    HIGH_CONFIDENCE_THRESHOLD,
    HIGH_SCORE_THRESHOLD,
    INTELLIGENCE_REFRESH_SECONDS,
    LIVE_SCAN_WORKERS,
    NEWS_PRIORITY_CANDIDATES,
    OPPORTUNITY_LIMIT,
    REALTIME_MODE,
    WATCHLISTS,
    WORKER_CYCLE_ERROR_BACKOFF_SECONDS,
    WORKER_DB_READY_INITIAL_DELAY_SECONDS,
    WORKER_DB_READY_MAX_DELAY_SECONDS,
    DATABASE_MAINTENANCE_INTERVAL_SECONDS,
)
from database import (
    bootstrap_database_with_lock,
    connect,
    is_transient_database_error,
    run_database_maintenance,
    save_forecast,
    save_intelligence_event,
    save_json_signal,
    utc_now,
    wait_for_database_ready,
)
from execution_policy import execution_policy
from engine import analyze_market
from forecasting import forecast_price
from global_market_scanner import active_global_watchlist
from global_pit_engine import _execution_quote_eligible, build_global_universe, persist_global_pit_state, rank_global_opportunities
from global_adaptive_engine import (
    adaptive_portfolio_optimizer,
    apply_cross_market_influence,
    persist_decision_event,
    record_invalid_symbol_failure,
)
from intelligence_hub import collect_all
from market_data import get_history, get_many_snapshots
from news_intelligence import get_news_sentiment
from opportunity_engine import rank_opportunities
from oracle_bot import process_signals, risk_exits, snapshot, update_prices
from oracle_council import deliberate
from portfolio_rotation import rotation_plan
from realtime_runtime import cadence_for, seconds_until

log = logging.getLogger("market-worker")
stop_event = Event()
trade_cycle_lock = Lock()
_rolling_offsets: dict[str, int] = {"cash": 0, "crypto": 0}
_EXECUTION_DISABLED_LOGGED = False


def _request_stop(*_: object) -> None:
    log.info("Worker shutdown requested.")
    stop_event.set()


def _execution_overrides() -> dict[str, Any]:
    return {
        "ENABLE_AUTOTRADE": ENABLE_AUTOTRADE,
        "ENABLE_STOCK_AUTOTRADE": ENABLE_STOCK_AUTOTRADE,
        "ENABLE_CRYPTO_AUTOTRADE": ENABLE_CRYPTO_AUTOTRADE,
        "ENABLE_NEW_ENTRIES": ENABLE_NEW_ENTRIES,
        "ENABLE_AUTOMATED_EXITS": ENABLE_AUTOMATED_EXITS,
        "ENABLE_PORTFOLIO_ROTATION": ENABLE_PORTFOLIO_ROTATION,
        "ENABLE_BROKER_SUBMISSION": ENABLE_BROKER_SUBMISSION,
        "GLOBAL_KILL_SWITCH": GLOBAL_KILL_SWITCH,
    }


def _execution_enabled(market: str = "cash", intent: str = "entry") -> bool:
    global _EXECUTION_DISABLED_LOGGED
    policy = execution_policy(market=market, intent=intent, overrides=_execution_overrides())
    if policy.allowed:
        return True
    if not _EXECUTION_DISABLED_LOGGED:
        log.warning(
            "Execution disabled for %s by central policy (%s); scanning and persistence continue.",
            market,
            policy.reason,
        )
        _EXECUTION_DISABLED_LOGGED = True
    return False


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _quote_age_seconds(quote_timestamp: Any) -> float | None:
    text = str(quote_timestamp or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())


def _latest_history_price(history: Any) -> float | None:
    try:
        import pandas as pd

        if history is None or getattr(history, "empty", True) or "Close" not in history.columns:
            return None
        values = pd.to_numeric(history["Close"], errors="coerce").dropna()
        values = values[values.map(lambda item: math.isfinite(float(item)))]
        if values.empty:
            return None
        return _finite_positive(values.iloc[-1])
    except Exception:
        return None

def _average_dollar_volume(history: Any, lookback: int = 20) -> float | None:
    try:
        import pandas as pd

        if (
            history is None
            or getattr(history, "empty", True)
            or "Close" not in history.columns
            or "Volume" not in history.columns
        ):
            return None
        close = history["Close"]
        volume = history["Volume"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, -1]
        if isinstance(volume, pd.DataFrame):
            volume = volume.iloc[:, -1]
        close = pd.to_numeric(close, errors="coerce")
        volume = pd.to_numeric(volume, errors="coerce")
        values = (close * volume).dropna()
        values = values[values.map(lambda item: math.isfinite(float(item)) and float(item) > 0)]
        if values.empty:
            return None
        average = float(values.tail(max(1, int(lookback))).mean())
        return average if math.isfinite(average) and average > 0 else None
    except Exception:
        return None



def _execution_price_from_history(symbol: str, history: Any, signal_price: Any = None) -> float | None:
    route = dict(getattr(history, "attrs", {}).get("provider_route", {}) or {})
    for value in (route.get("price"), route.get("current_price"), route.get("last_price"), _latest_history_price(history), signal_price):
        price = _finite_positive(value)
        if price is not None:
            return price
    return None


def _quote_payload_from_history(symbol: str, history: Any, price: Any = None, *, scan_type: str = "") -> dict[str, Any]:
    route = dict(getattr(history, "attrs", {}).get("provider_route", {}) or {})
    execution_price = _execution_price_from_history(symbol, history, price)
    quote_timestamp = route.get("quote_timestamp")
    interval = route.get("interval", "1d")
    normalized_symbol = str(symbol or "").upper().strip()
    requested_symbol = str(route.get("requested_symbol") or "").upper().strip()
    provider_symbol = str(route.get("provider_symbol") or "").upper().strip()
    quote_verified = route.get("quote_verified") is True
    identity_verified = bool(
        normalized_symbol
        and requested_symbol == normalized_symbol
        and provider_symbol == normalized_symbol
    )
    avg_dollar_volume = _average_dollar_volume(history)
    return {
        "symbol": normalized_symbol,
        "requested_symbol": requested_symbol,
        "provider_symbol": provider_symbol,
        "provider": route.get("provider"),
        "price": execution_price,
        "quote_timestamp": quote_timestamp,
        "quote_age_seconds": route.get("quote_age_seconds") if route.get("quote_age_seconds") is not None else _quote_age_seconds(quote_timestamp),
        "interval": interval,
        "source_interval": interval,
        "source_mode": route.get("source_mode") or route.get("mode"),
        "scan_type": scan_type or route.get("scan_type"),
        "quote_verified": quote_verified,
        "avg_dollar_volume": avg_dollar_volume,
        "data_quality_score": route.get("data_quality_score"),
        "tradeable": bool(quote_verified and identity_verified and avg_dollar_volume),
        "source_identity": route.get("source_identity"),
        "cache_identity": route.get("cache_identity"),
        "ohlcv_fingerprint": route.get("ohlcv_fingerprint"),
    }


def _attach_execution_metadata(signal: Any, history: Any, scan_type: str) -> dict[str, Any]:
    route = dict(getattr(history, "attrs", {}).get("provider_route", {}) or {})
    route["scan_type"] = scan_type
    route["source_interval"] = route.get("interval", "1d")
    route.setdefault("quote_age_seconds", _quote_age_seconds(route.get("quote_timestamp")))
    price = _execution_price_from_history(getattr(signal, "symbol", ""), history, getattr(signal, "price", None))
    if price is not None:
        route["price"] = price
        route["current_price"] = price
        setattr(signal, "price", price)
    try:
        history.attrs["provider_route"] = route
    except Exception:
        pass
    setattr(signal, "market_data_route", route)
    setattr(signal, "scan_type", scan_type)
    setattr(signal, "source_interval", route.get("interval", "1d"))
    setattr(signal, "quote_timestamp", route.get("quote_timestamp"))
    setattr(signal, "source_quote_timestamp", route.get("quote_timestamp"))
    setattr(signal, "requested_symbol", route.get("requested_symbol"))
    setattr(signal, "provider_symbol", route.get("provider_symbol"))
    setattr(signal, "provider", route.get("provider"))
    setattr(signal, "quote_verified", route.get("quote_verified") is True)
    setattr(signal, "quote_age_seconds", route.get("quote_age_seconds"))
    return route


def _signal_payload(signal: Any, route: dict[str, Any], scan_type: str, **extra: Any) -> dict[str, Any]:
    payload = signal.to_dict() if hasattr(signal, "to_dict") else dict(getattr(signal, "__dict__", {}) or {})
    payload.update(
        {
            "scan_type": scan_type,
            "source_interval": route.get("interval", "1d"),
            "source_quote_timestamp": route.get("quote_timestamp"),
            "quote_timestamp": route.get("quote_timestamp"),
            "quote_age_seconds": route.get("quote_age_seconds"),
            "requested_symbol": route.get("requested_symbol"),
            "provider_symbol": route.get("provider_symbol"),
            "provider": route.get("provider"),
            "quote_verified": route.get("quote_verified") is True,
            "market_data_route": route,
        }
    )
    payload.update(extra)
    return payload


def _normalize_starter_action(signal: Any) -> Any:
    return signal


signal.signal(signal.SIGTERM, _request_stop)
signal.signal(signal.SIGINT, _request_stop)


def _wait_for_worker_database(label: str) -> bool:
    result = wait_for_database_ready(
        stop_event=stop_event,
        initial_delay=WORKER_DB_READY_INITIAL_DELAY_SECONDS,
        max_delay=WORKER_DB_READY_MAX_DELAY_SECONDS,
        label=label,
        log_callback=log.warning,
    )
    return not bool(result.get("stopped"))


def _bootstrap_worker_database(label: str) -> bool:
    while not stop_event.is_set():
        if not _wait_for_worker_database(label):
            return False
        try:
            from migrations import run_migrations

            bootstrap_database_with_lock(run_migrations)
            _ensure_status_table()
            return True
        except Exception as exc:
            if is_transient_database_error(exc):
                log.warning("%s PostgreSQL bootstrap interrupted by transient outage: %s", label, exc.__class__.__name__)
                continue
            raise
    return False


def _run_scheduled_database_maintenance(label: str) -> None:
    try:
        result = run_database_maintenance()
        if result.get("skipped"):
            log.info("%s database maintenance skipped: %s", label, result.get("reason"))
        else:
            log.info("%s database maintenance complete: %s", label, result.get("deleted", {}))
    except Exception as exc:
        log.warning("%s database maintenance failed; worker will retry later: %s", label, exc)


def _ensure_status_table() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_worker_status (
                market TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                message TEXT,
                last_run TEXT,
                heartbeat TEXT,
                last_pulse TEXT,
                last_fast_scan TEXT,
                next_fast_scan_at TEXT,
                next_scan_at TEXT,
                session_label TEXT,
                pulse_seconds INTEGER,
                fast_scan_seconds INTEGER,
                deep_scan_seconds INTEGER,
                execution_mode TEXT DEFAULT 'paper',
                actions_last_cycle INTEGER DEFAULT 0,
                fast_actions_last_cycle INTEGER DEFAULT 0,
                cycle_errors INTEGER DEFAULT 0
            )
            """
        )
        for statement in (
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS last_pulse TEXT",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS last_fast_scan TEXT",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS next_fast_scan_at TEXT",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS next_scan_at TEXT",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS session_label TEXT",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS pulse_seconds INTEGER",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS fast_scan_seconds INTEGER",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS deep_scan_seconds INTEGER",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS execution_mode TEXT DEFAULT 'paper'",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS actions_last_cycle INTEGER DEFAULT 0",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS fast_actions_last_cycle INTEGER DEFAULT 0",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS cycle_errors INTEGER DEFAULT 0",
        ):
            conn.execute(statement)


def set_market_status(
    market: str,
    status: str,
    message: str,
    completed: bool = False,
    *,
    pulse: bool = False,
    fast_scan: bool = False,
    next_fast_scan_at: str | None = None,
    next_scan_at: str | None = None,
    session_label: str | None = None,
    pulse_seconds: int | None = None,
    fast_scan_seconds: int | None = None,
    deep_scan_seconds: int | None = None,
    actions_last_cycle: int | None = None,
    fast_actions_last_cycle: int | None = None,
    cycle_errors: int | None = None,
) -> None:
    now = utc_now()
    status_market = market
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO market_worker_status (
                market, status, message, last_run, heartbeat, last_pulse,
                last_fast_scan, next_fast_scan_at, next_scan_at, session_label,
                pulse_seconds, fast_scan_seconds, deep_scan_seconds,
                execution_mode, actions_last_cycle, fast_actions_last_cycle,
                cycle_errors
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (market) DO UPDATE SET
                status = EXCLUDED.status,
                message = EXCLUDED.message,
                heartbeat = EXCLUDED.heartbeat,
                last_run = CASE WHEN %s THEN EXCLUDED.last_run ELSE market_worker_status.last_run END,
                last_pulse = CASE WHEN %s THEN EXCLUDED.last_pulse ELSE market_worker_status.last_pulse END,
                last_fast_scan = CASE WHEN %s THEN EXCLUDED.last_fast_scan ELSE market_worker_status.last_fast_scan END,
                next_fast_scan_at = COALESCE(EXCLUDED.next_fast_scan_at, market_worker_status.next_fast_scan_at),
                next_scan_at = COALESCE(EXCLUDED.next_scan_at, market_worker_status.next_scan_at),
                session_label = COALESCE(EXCLUDED.session_label, market_worker_status.session_label),
                pulse_seconds = COALESCE(EXCLUDED.pulse_seconds, market_worker_status.pulse_seconds),
                fast_scan_seconds = COALESCE(EXCLUDED.fast_scan_seconds, market_worker_status.fast_scan_seconds),
                deep_scan_seconds = COALESCE(EXCLUDED.deep_scan_seconds, market_worker_status.deep_scan_seconds),
                execution_mode = EXCLUDED.execution_mode,
                actions_last_cycle = COALESCE(EXCLUDED.actions_last_cycle, market_worker_status.actions_last_cycle),
                fast_actions_last_cycle = COALESCE(EXCLUDED.fast_actions_last_cycle, market_worker_status.fast_actions_last_cycle),
                cycle_errors = COALESCE(EXCLUDED.cycle_errors, market_worker_status.cycle_errors)
            """,
            (
                status_market,
                status,
                message,
                now if completed else None,
                now,
                now if pulse else None,
                now if fast_scan else None,
                next_fast_scan_at,
                next_scan_at,
                session_label,
                pulse_seconds,
                fast_scan_seconds,
                deep_scan_seconds,
                EXECUTION_MODE,
                actions_last_cycle,
                fast_actions_last_cycle,
                cycle_errors,
                completed,
                pulse,
                fast_scan,
            ),
        )

def _format_action(action_record: Any) -> str:
    if not isinstance(action_record, dict):
        return str(action_record)
    action_name = str(action_record.get("action", "TRADE")).upper()
    symbol = str(action_record.get("symbol", "UNKNOWN")).upper()
    action_text = f"{action_name} {symbol}"
    quantity = action_record.get("quantity")
    price = action_record.get("price")
    reason = action_record.get("reason")
    if quantity is not None:
        try:
            action_text += f" x {float(quantity):,.6f}"
        except (TypeError, ValueError):
            action_text += f" x {quantity}"
    if price is not None:
        try:
            action_text += f" @ ${float(price):,.4f}"
        except (TypeError, ValueError):
            action_text += f" @ {price}"
    if reason:
        action_text += f" ({reason})"
    return action_text


def _build_completion_message(label: str, actions: list[Any]) -> str:
    if not actions:
        return f"{label} deep scan completed. No new trade met every rule."
    return f"{label} deep scan completed. Actions: " + ", ".join(_format_action(action) for action in actions)



def _v39_position_rows(market: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with connect() as conn:
        portfolio = conn.execute("SELECT * FROM portfolios WHERE market=%s", (market,)).fetchone() or {}
        positions = list(conn.execute("SELECT * FROM positions WHERE market=%s", (market,)).fetchall())
        if market == "cash" and positions:
            enriched_positions = []
            for position in positions:
                item = dict(position)
                if not str(item.get("sector") or "").strip():
                    symbol = str(item.get("symbol") or "").upper().strip()
                    sector_row = conn.execute(
                        """
                        SELECT sector
                        FROM global_market_candidates
                        WHERE symbol = %s
                          AND sector IS NOT NULL
                          AND sector NOT IN ('', 'Unknown', 'UNKNOWN')
                        ORDER BY scanned_at DESC
                        LIMIT 1
                        """,
                        (symbol,),
                    ).fetchone() if symbol else None
                    if sector_row and sector_row.get("sector"):
                        item["sector"] = sector_row.get("sector")
                enriched_positions.append(item)
            positions = enriched_positions
    positions_value = sum(
        _finite_positive(position.get("market_value"))
        or _finite_positive(position.get("quantity")) * _finite_positive(position.get("current_price"))
        for position in positions
    )
    cash = float(portfolio.get("cash", 0.0) or 0.0)
    leverage = float(portfolio.get("leverage_limit", 1.0) or 1.0)
    enriched = {**portfolio, "equity": cash + positions_value, "buying_power": max(0.0, cash * leverage)}
    return enriched, positions


def _v39_signal_opportunity(market: str, signal: Any, prices: dict[str, Any], ranked_by_symbol: dict[str, dict[str, Any]], scan_type: str) -> dict[str, Any]:
    symbol = str(getattr(signal, "symbol", "") or "").upper()
    quote = dict(prices.get(symbol) or {})
    ranked = dict(ranked_by_symbol.get(symbol) or {})
    liquidity = _finite_positive(quote.get("avg_dollar_volume")) or _finite_positive(ranked.get("liquidity")) or 0.0
    stages = ["surveillance", "deep_research" if scan_type == "deep" else "active_hot"]
    action = str(getattr(signal, "action", "") or "").upper()
    if action in {"BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}:
        stages.append("buy_signal")
    if quote.get("quote_verified") is True:
        stages.append("verified_quote")
    requested_symbol = str(quote.get("requested_symbol") or "").upper().strip()
    provider_symbol = str(quote.get("provider_symbol") or "").upper().strip()
    identity_verified = requested_symbol == symbol and provider_symbol == symbol
    execution_fresh = _execution_quote_eligible(
        {
            **quote,
            "symbol": symbol,
            "market": market,
            "asset_class": "crypto" if market == "crypto" else "stock",
        }
    )
    tradeable = bool(quote.get("tradeable", quote.get("quote_verified") is True))
    spread = ranked.get("spread_pct")
    if spread in (None, ""):
        spread = quote.get("spread_pct")
    risk_score = ranked.get("risk_score")
    if risk_score in (None, ""):
        risk_score = getattr(signal, "risk_score", None)
    try:
        spread_known = spread is not None and math.isfinite(float(spread)) and float(spread) >= 0
    except (TypeError, ValueError):
        spread_known = False
    try:
        risk_known = risk_score is not None and math.isfinite(float(risk_score))
    except (TypeError, ValueError):
        risk_known = False
    signal_id = getattr(signal, "signal_id", None)
    forecast_id = getattr(signal, "forecast_id", None)
    qualified = bool(
        action in {"BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}
        and quote.get("quote_verified") is True
        and identity_verified
        and execution_fresh
        and tradeable
        and liquidity > 0
        and spread_known
        and risk_known
        and signal_id
        and forecast_id
    )
    sector = ranked.get("sector") or quote.get("sector")
    if market == "crypto" and not sector:
        sector = "Crypto"
    return {
        "symbol": symbol,
        "requested_symbol": quote.get("requested_symbol"),
        "provider_symbol": quote.get("provider_symbol"),
        "provider": quote.get("provider"),
        "asset_class": "crypto" if market == "crypto" else "stock",
        "market": market,
        "exchange": quote.get("exchange") or ranked.get("exchange"),
        "currency": quote.get("currency") or "USD",
        "sector": sector or "",
        "quote_verified": quote.get("quote_verified") is True,
        "quote_timestamp": quote.get("quote_timestamp"),
        "source_interval": quote.get("source_interval") or quote.get("interval"),
        "interval": quote.get("interval"),
        "tradeable": tradeable,
        "qualified_for_capital": qualified,
        "avg_dollar_volume": liquidity,
        "liquidity": liquidity,
        "spread_pct": spread,
        "opportunity_score": ranked.get("opportunity_score") or getattr(signal, "score", 0.0),
        "expected_move_pct": ranked.get("expected_return") or ranked.get("expected_move_pct"),
        "confidence": getattr(signal, "confidence", 0.0),
        "data_quality_score": quote.get("data_quality_score") or ranked.get("data_quality_score") or 0.0,
        "risk_score": risk_score,
        "scan_type": scan_type,
        "signal_id": signal_id,
        "forecast_id": forecast_id,
        "created_at": getattr(signal, "created_at", None),
        "stages": stages,
    }


def _v39_record_event(market: str, symbol: str, stage: str, payload: dict[str, Any], rejection_reason: str | None = None) -> None:
    try:
        with connect() as conn:
            persist_decision_event(conn, market=market, symbol=symbol, stage=stage, payload=payload, rejection_reason=rejection_reason)
    except Exception as exc:
        log.debug("V39 decision event skipped | market=%s | symbol=%s | stage=%s | error=%s", market, symbol, stage, exc)


def _v39_prioritize_signals(market: str, signals: list[Any], prices: dict[str, Any], ranked: list[dict[str, Any]], scan_type: str) -> list[Any]:
    if not signals:
        return signals
    ranked_by_symbol = {str(item.get("symbol", "")).upper(): item for item in ranked or []}
    opportunities = [_v39_signal_opportunity(market, signal, prices, ranked_by_symbol, scan_type) for signal in signals]
    intelligence = [item for item in opportunities if item.get("asset_class") not in {"stock", "crypto", "equity", "etf"}]
    opportunities = apply_cross_market_influence(opportunities, intelligence)
    try:
        portfolio, positions = _v39_position_rows(market)
        plan = adaptive_portfolio_optimizer(opportunities, portfolio, positions, engine="crypto" if market == "crypto" else "stock")
    except Exception as exc:
        log.debug("V39 optimizer skipped | market=%s | error=%s", market, exc)
        plan = {"allocations": []}
    allocations = [row for row in plan.get("allocations", []) if row.get("symbol")]
    allocation_symbols = [str(row.get("symbol")).upper() for row in allocations]
    allocation_by_symbol = {str(row.get("symbol")).upper(): row for row in allocations}
    allocation_set = set(allocation_by_symbol)
    signal_by_symbol = {str(getattr(signal, "symbol", "")).upper(): signal for signal in signals}
    for signal in signals:
        for attr in (
            "planned_trade_value",
            "v39_optimizer_approved_amount",
            "v39_optimizer_allocation",
            "v39_optimizer_plan",
        ):
            if hasattr(signal, attr):
                try:
                    delattr(signal, attr)
                except Exception:
                    setattr(signal, attr, None)
    for symbol, allocation in allocation_by_symbol.items():
        signal = signal_by_symbol.get(symbol)
        if signal is None:
            continue
        approved_amount = _finite_positive(allocation.get("amount"))
        if approved_amount is None:
            continue
        setattr(signal, "planned_trade_value", approved_amount)
        setattr(signal, "v39_optimizer_approved_amount", approved_amount)
        setattr(signal, "v39_optimizer_allocation", allocation)
        setattr(signal, "v39_optimizer_plan", plan)
    for opportunity in opportunities:
        symbol = str(opportunity.get("symbol") or "").upper()
        stage = "portfolio_approved" if symbol in allocation_set else "verified_quote" if opportunity.get("quote_verified") else "surveillance"
        reason = None if symbol in allocation_set else ("optimizer did not allocate capital" if opportunity.get("qualified_for_capital") else "not capital qualified")
        _v39_record_event(market, symbol, stage, {**opportunity, "portfolio_context": plan}, reason)
    ordered = sorted(
        signals,
        key=lambda signal: (
            str(getattr(signal, "symbol", "")).upper() in allocation_set,
            -allocation_symbols.index(str(getattr(signal, "symbol", "")).upper()) if str(getattr(signal, "symbol", "")).upper() in allocation_set else -9999,
            float(getattr(signal, "score", 0.0) or 0.0),
        ),
        reverse=True,
    )
    return ordered


def _v39_execute_iterative(
    market: str,
    signals: list[Any],
    prices: dict[str, Any],
    ranked: list[dict[str, Any]],
    scan_type: str,
) -> list[Any]:
    """Execute at most one optimized paper entry per portfolio snapshot.

    A rejected candidate is skipped and the worker continues. After a successful
    paper action, the next loop reloads the portfolio inside
    ``_v39_prioritize_signals`` before considering another allocation.
    """
    remaining = list(signals or [])
    actions: list[Any] = []
    attempts = 0
    while remaining and attempts < len(signals or []):
        attempts += 1
        ordered = _v39_prioritize_signals(market, remaining, prices, ranked, scan_type)
        if not ordered:
            break
        signal = ordered[0]
        symbol = str(getattr(signal, "symbol", "") or "").upper()
        action = str(getattr(signal, "action", "") or "").upper()
        if GLOBAL_PIT_MODE and action in {"BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}:
            approved_amount = _finite_positive(getattr(signal, "v39_optimizer_approved_amount", None))
            allocation_symbol = str((getattr(signal, "v39_optimizer_allocation", {}) or {}).get("symbol") or "").upper()
            if approved_amount is None or allocation_symbol != symbol:
                _v39_record_event(
                    market,
                    symbol,
                    "portfolio_rejected",
                    {
                        "symbol": symbol,
                        "action": action,
                        "scan_type": scan_type,
                        "reason": "V39 optimizer allocation required before entry execution",
                    },
                    "optimizer_allocation_required",
                )
                remaining = [item for item in remaining if str(getattr(item, "symbol", "") or "").upper() != symbol]
                continue
        before_count = len(actions)
        result = process_signals(market, [signal], prices=prices) or []
        actions.extend(result)
        remaining = [item for item in remaining if str(getattr(item, "symbol", "") or "").upper() != symbol]
        if len(actions) > before_count:
            log.info("V39 optimizer executed one allocation and will reload portfolio | market=%s | symbol=%s", market, symbol)
            continue
    return actions


def _v39_record_actions(market: str, actions: list[Any]) -> None:
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        symbol = str(action.get("symbol") or "").upper()
        if not symbol:
            continue
        payload = {
            "trade_id": action.get("trade_id"),
            "price": action.get("price"),
            "action": action.get("action"),
            "signal_id": action.get("signal_id"),
            "forecast_id": action.get("forecast_id"),
            "created_at": action.get("created_at"),
            "planned_trade_value": action.get("planned_trade_value"),
            "optimizer_approved_amount": action.get("optimizer_approved_amount"),
        }
        executed = str(action.get("action") or "").upper() in {"BUY", "SELL", "STRONG_BUY", "ACCUMULATE", "LONG"}
        if executed:
            for stage in ("forecast_approved", "portfolio_approved", "execution_approved", "paper_trade_executed"):
                _v39_record_event(market, symbol, stage, payload)
        else:
            _v39_record_event(market, symbol, "execution_approved", payload)


def _v39_quarantine_symbol(symbol: str, provider: str, failure_type: str) -> None:
    try:
        with connect() as conn:
            record_invalid_symbol_failure(conn, symbol=symbol, provider=provider or "unknown", failure_type=failure_type)
    except Exception as exc:
        log.debug("V39 invalid-symbol quarantine skipped | symbol=%s | provider=%s | error=%s", symbol, provider, exc)


def _persist_global_pit_rankings(market: str, ranked: list[dict[str, Any]], prices: dict[str, Any]) -> None:
    if not GLOBAL_PIT_MODE or market != "cash" or not ranked:
        return
    try:
        universe = []
        for item in ranked:
            symbol = str(item.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            quote = dict(prices.get(symbol) or {})
            requested_symbol = str(quote.get("requested_symbol") or "").upper().strip()
            provider_symbol = str(quote.get("provider_symbol") or "").upper().strip()
            quote_verified = quote.get("quote_verified") is True
            identity_verified = requested_symbol == symbol and provider_symbol == symbol
            liquidity = (
                _finite_positive(item.get("liquidity"))
                or _finite_positive(item.get("avg_dollar_volume"))
                or _finite_positive(quote.get("avg_dollar_volume"))
                or 0.0
            )
            raw_quality = item.get("data_quality_score")
            if raw_quality is None:
                raw_quality = quote.get("data_quality_score")
            try:
                data_quality_score = float(raw_quality) if raw_quality is not None else 0.0
            except (TypeError, ValueError):
                data_quality_score = 0.0
            if not math.isfinite(data_quality_score):
                data_quality_score = 0.0

            universe.append({
                "symbol": symbol,
                "requested_symbol": requested_symbol,
                "provider_symbol": provider_symbol,
                "name": item.get("symbol"),
                "asset_class": "stock",
                "market": market,
                "sector": item.get("sector") or (item.get("features") or {}).get("sector") or "Unknown",
                "expected_move_pct": item.get("expected_return") or item.get("expected_move_pct"),
                "confidence": item.get("confidence"),
                "opportunity_score": item.get("opportunity_score"),
                "data_quality_score": max(0.0, data_quality_score),
                "liquidity": liquidity,
                "avg_dollar_volume": liquidity,
                "quote_verified": quote_verified,
                "quote_timestamp": quote.get("quote_timestamp"),
                "quote_age_seconds": quote.get("quote_age_seconds"),
                "interval": quote.get("interval"),
                "source_interval": quote.get("source_interval") or quote.get("interval"),
                "provider_mode": quote.get("source_mode"),
                "provider_support": [quote.get("provider")] if quote.get("provider") else [],
                "discovery_source": "oracle_opportunity_rankings",
                "tradeable": bool(quote_verified and identity_verified and liquidity > 0),
            })
        queue = rank_global_opportunities(build_global_universe({}, universe))
        with connect() as conn:
            persist_global_pit_state(conn, universe, queue)
    except Exception as exc:
        log.debug("Global Pit persistence skipped | market=%s | error=%s", market, exc)


def _discover_symbol(market: str, symbol: str, name: str) -> tuple[Any, str, Any] | None:
    if stop_event.is_set():
        return None
    try:
        history = get_history(symbol, "1y", "1d")
        if history is None or history.empty:
            _v39_quarantine_symbol(symbol, "market_data", "empty_history")
            return None
        signal = analyze_market(symbol, history, 0.0)
        if signal is None:
            return None
        _attach_execution_metadata(signal, history, "deep")
        return signal, name, history
    except Exception as exc:
        _v39_quarantine_symbol(symbol, "market_data", exc.__class__.__name__)
        log.warning("Discovery failed | market=%s | symbol=%s | error=%s", market, symbol, exc)
        return None


def _held_symbols(market: str) -> set[str]:
    try:
        with connect() as conn:
            records = conn.execute("SELECT symbol FROM positions WHERE market=%s", (market,)).fetchall()
        return {str(record.get("symbol", "")).upper() for record in records if record.get("symbol")}
    except Exception:
        return set()



def _rolling_batch(items: list[tuple[str, str]], market: str, size: int) -> list[tuple[str, str]]:
    """Return a rotating slice so the always-on loop eventually covers the full universe."""
    if not items or size <= 0:
        return []
    size = min(size, len(items))
    start = _rolling_offsets.get(market, 0) % len(items)
    batch = [items[(start + index) % len(items)] for index in range(size)]
    _rolling_offsets[market] = (start + size) % len(items)
    return batch


def _latest_ranked_symbols(market: str, limit: int) -> list[str]:
    try:
        with connect() as conn:
            records = conn.execute(
                """
                SELECT DISTINCT ON (symbol) symbol, opportunity_score, created_at
                FROM opportunity_rankings
                WHERE market = %s
                ORDER BY symbol, created_at DESC
                """,
                (market,),
            ).fetchall()
        records = sorted(
            records,
            key=lambda item: float(item.get("opportunity_score", 0.0) or 0.0),
            reverse=True,
        )
        return [str(item.get("symbol", "")).upper() for item in records[:limit] if item.get("symbol")]
    except Exception as exc:
        log.debug("%s fast ranking lookup unavailable: %s", market, exc)
        return []


def _fast_candidate_batch(market: str) -> list[tuple[str, str]]:
    """Blend holdings, current leaders, and a rotating universe slice."""
    watchlist = dict(WATCHLISTS[market])
    candidates: dict[str, str] = {}
    for symbol in sorted(_held_symbols(market)):
        candidates[symbol] = watchlist.get(symbol, symbol)
    for symbol in _latest_ranked_symbols(market, FAST_SCAN_TOP_RANKED):
        candidates.setdefault(symbol, watchlist.get(symbol, symbol))
    universe = [(str(symbol).upper(), str(name)) for symbol, name in watchlist.items()]
    for symbol, name in _rolling_batch(universe, market, FAST_SCAN_BATCH_SIZE):
        candidates.setdefault(symbol, name)
    return list(candidates.items())[: max(FAST_SCAN_BATCH_SIZE, FAST_SCAN_TOP_RANKED)]


def _fast_discover_symbol(market: str, symbol: str, name: str) -> tuple[Any, Any] | None:
    """Build a low-latency intraday signal without the expensive news pass."""
    if stop_event.is_set():
        return None
    attempts = (("5d", "5m"), ("1mo", "1h"))
    for period, interval in attempts:
        try:
            history = get_history(symbol, period, interval)
            if history is None or history.empty or len(history) < 60:
                if history is None or history.empty:
                    _v39_quarantine_symbol(symbol, "market_data", "empty_fast_history")
                continue
            signal = analyze_market(symbol, history, 0.0)
            if signal is None:
                continue
            _attach_execution_metadata(signal, history, "fast")
            signal = _normalize_starter_action(signal)
            signal.reason = (
                f"Always-on {interval} market pulse. " + str(getattr(signal, "reason", ""))
            ).strip()
            return signal, history
        except Exception as exc:
            log.debug("Fast discovery failed | market=%s | symbol=%s | error=%s", market, symbol, exc)
    return None


def fast_scan_market(market: str) -> list[Any]:
    """Continuously evaluate a rolling candidate batch between deep scans.

    This loop never forces a trade. It keeps searching and executes immediately
    whenever a candidate passes the live-data, forecast, quant, portfolio, and
    risk gates.
    """
    batch = _fast_candidate_batch(market)
    if not batch:
        return []

    signals: list[Any] = []
    prices: dict[str, Any] = {}
    workers = min(LIVE_SCAN_WORKERS, max(1, len(batch)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"{market}-fast-symbol") as executor:
        futures = {
            executor.submit(_fast_discover_symbol, market, symbol, name): symbol
            for symbol, name in batch
        }
        for future in as_completed(futures):
            if stop_event.is_set():
                break
            symbol = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                log.debug("Fast candidate failed | market=%s | symbol=%s | error=%s", market, symbol, exc)
                continue
            if result is None:
                continue
            signal, history = result
            route = _attach_execution_metadata(signal, history, "fast")
            signals.append(signal)
            prices[symbol] = _quote_payload_from_history(symbol, history, getattr(signal, "price", None), scan_type="fast")
            try:
                signal_created_at = utc_now()
                setattr(signal, "created_at", signal_created_at)
                signal_id = save_json_signal(
                    market,
                    symbol,
                    signal.price,
                    signal.score,
                    signal.action,
                    signal.confidence,
                    _signal_payload(
                        signal,
                        route,
                        "fast",
                        always_on_fast_scan=True,
                        trade_configuration={
                            "mode": EXECUTION_MODE,
                            "scan": "fast",
                            "scan_type": "fast",
                            "action": str(signal.action),
                            "confidence": float(signal.confidence),
                            "score": float(signal.score),
                            "entry_price": float(signal.price),
                        },
                    ),
                    created_at=signal_created_at,
                )
                setattr(signal, "signal_id", signal_id)
                forecast = forecast_price(
                    history,
                    3 if market == "cash" else 1,
                    market=market,
                    source_interval=route.get("interval", "1d"),
                )
                if forecast:
                    setattr(signal, "forecast_id", getattr(forecast, "forecast_id", None))
                    save_forecast(market, symbol, forecast, scan_type="fast", signal_id=signal_id, signal_created_at=signal_created_at)
            except Exception as exc:
                log.debug("Fast persistence failed | market=%s | symbol=%s | error=%s", market, symbol, exc)

    if not signals:
        return []
    signals.sort(
        key=lambda signal: (
            float(getattr(signal, "score", 0.0) or 0.0),
            float(getattr(signal, "confidence", 0.0) or 0.0),
        ),
        reverse=True,
    )

    actions: list[Any] = []
    with trade_cycle_lock:
        exits_enabled = _execution_enabled(market, "exit")
        entries_enabled = _execution_enabled(market, "entry")
        if exits_enabled:
            try:
                update_prices(market, prices)
            except Exception as exc:
                log.debug("%s fast price update failed: %s", market, exc)
            try:
                actions.extend(risk_exits(market, prices) or [])
            except Exception as exc:
                log.exception("%s fast risk exits failed: %s", market, exc)
        if entries_enabled or exits_enabled:
            try:
                if entries_enabled:
                    actions.extend(_v39_execute_iterative(market, signals, prices, [], "fast") or [])
                elif exits_enabled:
                    actions.extend(process_signals(market, signals, prices=prices) or [])
            except Exception as exc:
                log.exception("%s fast execution failed: %s", market, exc)
        try:
            snapshot(market)
        except Exception as exc:
            log.debug("%s fast snapshot failed: %s", market, exc)
    _v39_record_actions(market, actions)
    return actions

def scan_market(market: str) -> list[Any]:
    """Run the deeper worldwide research and paper-execution cycle."""
    watchlist = dict(WATCHLISTS[market])
    if market == "cash":
        global_movers = active_global_watchlist()
        if global_movers:
            watchlist.update(global_movers)
            log.info("Worldwide scanner promoted %s movers.", len(global_movers))

    preliminary: list[tuple[Any, str]] = []
    histories: dict[str, Any] = {}
    workers = min(LIVE_SCAN_WORKERS, max(1, len(watchlist)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"{market}-discover") as executor:
        futures = {
            executor.submit(_discover_symbol, market, symbol, name): (symbol, name)
            for symbol, name in watchlist.items()
        }
        for future in as_completed(futures):
            if stop_event.is_set():
                break
            result = future.result()
            if result is None:
                continue
            signal, name, history = result
            symbol = str(getattr(signal, "symbol", "")).upper()
            if not symbol:
                continue
            preliminary.append((signal, name))
            histories[symbol] = history

    preliminary.sort(
        key=lambda item: (
            float(getattr(item[0], "score", 0.0) or 0.0),
            float(getattr(item[0], "volume_ratio", 0.0) or 0.0),
        ),
        reverse=True,
    )
    promoted_symbols = {
        str(getattr(signal, "symbol", "")).upper()
        for signal, _ in preliminary[:NEWS_PRIORITY_CANDIDATES]
    }
    held = _held_symbols(market)
    deep_candidates = preliminary[:DEEP_ANALYSIS_CANDIDATES]
    included = {str(getattr(signal, "symbol", "")).upper() for signal, _ in deep_candidates}
    deep_candidates.extend(
        (signal, name)
        for signal, name in preliminary
        if str(getattr(signal, "symbol", "")).upper() in held
        and str(getattr(signal, "symbol", "")).upper() not in included
    )
    log.info(
        "%s discovery | universe=%s usable=%s deep=%s held=%s",
        market,
        len(watchlist),
        len(preliminary),
        len(deep_candidates),
        len(held),
    )

    signals: list[Any] = []
    prices: dict[str, Any] = {}
    for preliminary_signal, name in deep_candidates:
        if stop_event.is_set():
            break
        symbol = str(getattr(preliminary_signal, "symbol", "")).upper()
        history = histories.get(symbol)
        if history is None:
            continue
        priority = symbol in promoted_symbols
        try:
            news = get_news_sentiment(f"{name} {symbol}", priority=priority)
            signal = analyze_market(symbol, history, news.sentiment)
            if signal is None:
                continue
            route = _attach_execution_metadata(signal, history, "deep")
            council = deliberate(signal, news.headlines[:8])
            signal.score = council["score"]
            signal.action = council["action"]
            signal.confidence = council["confidence"]
            signal = _normalize_starter_action(signal)
            route = _attach_execution_metadata(signal, history, "deep")
            signal.reason = (str(signal.reason) + " " + str(council["explanation"])).strip()
            signals.append(signal)
            prices[symbol] = _quote_payload_from_history(symbol, history, getattr(signal, "price", None), scan_type="deep")
            signal_created_at = utc_now()
            setattr(signal, "created_at", signal_created_at)
            signal_id = save_json_signal(
                market,
                symbol,
                signal.price,
                signal.score,
                signal.action,
                signal.confidence,
                _signal_payload(
                    signal,
                    route,
                    "deep",
                    headlines=news.headlines[:8],
                    news_source=news.source,
                    news_priority=priority,
                    trade_configuration={
                        "mode": EXECUTION_MODE,
                        "scan": "deep",
                        "scan_type": "deep",
                        "action": str(signal.action),
                        "confidence": float(signal.confidence),
                        "score": float(signal.score),
                        "entry_price": float(signal.price),
                    },
                    oracle_council=council,
                ),
                created_at=signal_created_at,
            )
            setattr(signal, "signal_id", signal_id)
            forecast = forecast_price(
                history,
                5,
                market=market,
                source_interval=route.get("interval", "1d"),
            )
            if forecast:
                setattr(signal, "forecast_id", getattr(forecast, "forecast_id", None))
                save_forecast(market, symbol, forecast, scan_type="deep", signal_id=signal_id, signal_created_at=signal_created_at)
        except Exception as exc:
            log.warning("Oracle pass failed | market=%s | symbol=%s | error=%s", market, symbol, exc)

    ranked = rank_opportunities(signals, OPPORTUNITY_LIMIT, market=market)
    if ranked:
        try:
            with connect() as conn:
                now = utc_now()
                for item in ranked:
                    conn.execute(
                        """INSERT INTO opportunity_rankings
                        (market,symbol,rank,opportunity_score,payload,created_at)
                        VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
                        (market, item["symbol"], item["rank"], item["opportunity_score"], json.dumps(item), now),
                    )
                    conn.execute(
                        """INSERT INTO oracle_decision_audit
                        (market,symbol,recommendation,grade,opportunity_score,approved,reason,payload,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                        (
                            market,
                            item["symbol"],
                            item.get("recommendation", "WATCH"),
                            item.get("grade"),
                            item["opportunity_score"],
                            bool(item.get("approved")),
                            item.get("reason"),
                            json.dumps(item),
                            now,
                        ),
                    )
                    radar = item.get("radar", {}) or {}
                    conn.execute(
                        """INSERT INTO opportunity_radar_assessments
                        (market,symbol,primary_setup,setup_score,urgency_score,durability_score,catalyst_score,crowding_risk,approved,veto,payload,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                        (
                            market,
                            item["symbol"],
                            radar.get("primary_setup", "UNKNOWN"),
                            radar.get("setup_score", 0),
                            radar.get("urgency_score", 0),
                            radar.get("durability_score", 0),
                            radar.get("catalyst_score", 0),
                            radar.get("crowding_risk", 0),
                            bool(radar.get("approved", False)),
                            bool(radar.get("veto", False)),
                            json.dumps(radar),
                            now,
                        ),
                    )
        except Exception as exc:
            log.exception("%s ranking persistence failed: %s", market, exc)

        _persist_global_pit_rankings(market, ranked, prices)
        by_symbol = {item["symbol"]: item for item in ranked}
        try:
            with connect() as conn:
                positions = list(
                    conn.execute(
                        "SELECT symbol,quantity,entry_price,current_price FROM positions WHERE market=%s",
                        (market,),
                    ).fetchall()
                )
                enriched = []
                for position in positions:
                    entry = float(position.get("entry_price", 0) or 0)
                    current = float(position.get("current_price", 0) or 0)
                    symbol = str(position.get("symbol", ""))
                    enriched.append(
                        {
                            **position,
                            "unrealized_pct": ((current / entry) - 1) * 100 if entry else 0,
                            "opportunity_score": by_symbol.get(symbol, {}).get("opportunity_score", 50),
                        }
                    )
                for plan in rotation_plan(enriched, ranked):
                    conn.execute(
                        """INSERT INTO portfolio_rotations
                        (market,sold_symbol,bought_symbol,score_gap,reason,status,created_at)
                        VALUES (%s,%s,%s,%s,%s,'proposed',%s)""",
                        (market, plan["sell_symbol"], plan["buy_symbol"], plan["score_gap"], plan["reason"], utc_now()),
                    )
        except Exception as exc:
            log.exception("%s rotation planning failed: %s", market, exc)

    actions: list[Any] = []
    with trade_cycle_lock:
        exits_enabled = _execution_enabled(market, "exit")
        entries_enabled = _execution_enabled(market, "entry")
        if exits_enabled:
            try:
                update_prices(market, prices)
            except Exception as exc:
                log.exception("%s price update failed: %s", market, exc)
            try:
                actions.extend(risk_exits(market, prices) or [])
            except Exception as exc:
                log.exception("%s deep-scan risk exits failed: %s", market, exc)
        if entries_enabled or exits_enabled:
            try:
                if entries_enabled:
                    actions.extend(_v39_execute_iterative(market, signals, prices, ranked, "deep") or [])
                elif exits_enabled:
                    actions.extend(process_signals(market, signals, prices=prices) or [])
            except Exception as exc:
                log.exception("%s signal execution failed: %s", market, exc)
        try:
            snapshot(market)
        except Exception as exc:
            log.exception("%s portfolio snapshot failed: %s", market, exc)
    _v39_record_actions(market, actions)
    return actions


def live_position_pulse(market: str) -> tuple[list[Any], int, str]:
    """Refresh open positions and enforce stops between deep scans."""
    try:
        with connect() as conn:
            positions = conn.execute("SELECT symbol FROM positions WHERE market=%s", (market,)).fetchall()
    except Exception as exc:
        log.warning("%s pulse could not load positions: %s", market, exc)
        return [], 0, "none"
    symbols = [str(position.get("symbol", "")).upper() for position in positions if position.get("symbol")]
    if not symbols:
        return [], 0, "none"
    snapshots = get_many_snapshots(symbols, live=True)
    prices = {symbol: item.to_quote_payload() for symbol, item in snapshots.items() if item.price > 0}
    provider_names = sorted({item.provider for item in snapshots.values() if item.provider})
    provider_text = ", ".join(provider_names[:2]) if provider_names else "unavailable"
    if not prices:
        return [], 0, provider_text
    actions: list[Any] = []
    with trade_cycle_lock:
        if _execution_enabled(market, "exit"):
            try:
                update_prices(market, prices)
            except Exception as exc:
                log.warning("%s pulse price update failed: %s", market, exc)
            try:
                actions.extend(risk_exits(market, prices) or [])
            except Exception as exc:
                log.exception("%s pulse risk exits failed: %s", market, exc)
        try:
            snapshot(market)
        except Exception as exc:
            log.warning("%s pulse snapshot failed: %s", market, exc)
    return actions, len(prices), provider_text


def _collect_stock_intelligence() -> None:
    try:
        intelligence_results = collect_all()
        for category, result in intelligence_results.items():
            if stop_event.is_set():
                break
            if not result.available:
                continue
            for record in result.records:
                if stop_event.is_set():
                    break
                save_intelligence_event(category, result.provider, record.get("title", category), record)
    except Exception as exc:
        log.exception("Stock intelligence collection failed: %s", exc)


def _future_result(future: Future[list[Any]] | None, label: str) -> tuple[list[Any] | None, str | None]:
    if future is None or not future.done():
        return None, None
    try:
        return list(future.result() or []), None
    except Exception as exc:
        log.exception("%s deep scan failed: %s", label, exc)
        return [], str(exc)


def run_worker(market: str) -> None:
    requested_market = market.lower().strip()
    market = "cash" if requested_market == "stock" else requested_market
    if market not in WATCHLISTS:
        raise ValueError(f"Unknown market: {market}. Available markets: {list(WATCHLISTS)}")

    label = "Stock Market" if market == "cash" else "Crypto Market"
    if not _bootstrap_worker_database(label):
        log.info("%s worker stopped before database bootstrap completed.", label)
        return
    initial = cadence_for(market)
    log.info(
        "Starting %s always-on worker | pulse=%ss fast=%ss deep=%ss session=%s mode=%s",
        label,
        initial.pulse_seconds,
        initial.fast_scan_seconds,
        initial.deep_scan_seconds,
        initial.session_label,
        EXECUTION_MODE,
    )
    set_market_status(
        market,
        "starting",
        f"{label} always-on trading engine is starting.",
        session_label=initial.session_label,
        pulse_seconds=initial.pulse_seconds,
        fast_scan_seconds=initial.fast_scan_seconds,
        deep_scan_seconds=initial.deep_scan_seconds,
        cycle_errors=0,
    )

    deep_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{market}-deep")
    fast_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{market}-fast")
    intelligence_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="market-intelligence") if market == "cash" else None
    deep_future: Future[list[Any]] | None = None
    fast_future: Future[list[Any]] | None = None
    intelligence_future: Future[None] | None = None
    next_deep_due = time.monotonic()
    next_fast_due = time.monotonic()
    next_intelligence_due = time.monotonic()
    next_maintenance_due = time.monotonic()
    last_deep_actions = 0
    last_fast_actions = 0
    consecutive_errors = 0

    try:
        while not stop_event.is_set():
            try:
                cadence = cadence_for(market)
                now_monotonic = time.monotonic()
                if now_monotonic >= next_maintenance_due:
                    _run_scheduled_database_maintenance(label)
                    next_maintenance_due = now_monotonic + DATABASE_MAINTENANCE_INTERVAL_SECONDS

                completed_actions, deep_error = _future_result(deep_future, label)
                if completed_actions is not None:
                    deep_future = None
                    last_deep_actions = len(completed_actions)
                    message = _build_completion_message(label, completed_actions)
                    if deep_error:
                        message = f"{label} deep scan error: {deep_error}"
                    set_market_status(
                        market,
                        "running" if not deep_error else "degraded",
                        message,
                        completed=not deep_error,
                        session_label=cadence.session_label,
                        pulse_seconds=cadence.pulse_seconds,
                        fast_scan_seconds=cadence.fast_scan_seconds,
                        deep_scan_seconds=cadence.deep_scan_seconds,
                        actions_last_cycle=last_deep_actions,
                        cycle_errors=consecutive_errors,
                    )
                    log.info(message)

                completed_fast, fast_error = _future_result(fast_future, f"{label} fast scan")
                if completed_fast is not None:
                    fast_future = None
                    last_fast_actions = len(completed_fast)
                    fast_message = (
                        f"{label} fast scan completed. "
                        + (
                            "Actions: " + ", ".join(_format_action(action) for action in completed_fast)
                            if completed_fast
                            else "No qualified trade this pass; scanning continues."
                        )
                    )
                    if fast_error:
                        fast_message = f"{label} fast scan error: {fast_error}; automatic retry remains active."
                    set_market_status(
                        market,
                        "running" if not fast_error else "degraded",
                        fast_message,
                        fast_scan=True,
                        session_label=cadence.session_label,
                        pulse_seconds=cadence.pulse_seconds,
                        fast_scan_seconds=cadence.fast_scan_seconds,
                        deep_scan_seconds=cadence.deep_scan_seconds,
                        fast_actions_last_cycle=last_fast_actions,
                        cycle_errors=consecutive_errors,
                    )
                    log.info(fast_message)

                if deep_future is None and now_monotonic >= next_deep_due:
                    deep_future = deep_executor.submit(scan_market, market)
                    next_deep_due = now_monotonic + cadence.deep_scan_seconds
                    log.info("%s deep scan launched; next target in %ss", label, cadence.deep_scan_seconds)

                if (
                    ALWAYS_ON_TRADING
                    and FAST_SIGNAL_SCAN_ENABLED
                    and fast_future is None
                    and now_monotonic >= next_fast_due
                ):
                    fast_future = fast_executor.submit(fast_scan_market, market)
                    next_fast_due = now_monotonic + cadence.fast_scan_seconds
                    log.info("%s fast scan launched; next target in %ss", label, cadence.fast_scan_seconds)

                if market == "cash" and intelligence_executor is not None:
                    if intelligence_future is not None and intelligence_future.done():
                        try:
                            intelligence_future.result()
                        except Exception as exc:
                            log.warning("Intelligence refresh failed: %s", exc)
                        intelligence_future = None
                    if intelligence_future is None and now_monotonic >= next_intelligence_due:
                        intelligence_future = intelligence_executor.submit(_collect_stock_intelligence)
                        next_intelligence_due = now_monotonic + INTELLIGENCE_REFRESH_SECONDS

                pulse_actions: list[Any] = []
                refreshed = 0
                provider_text = "none"
                if REALTIME_MODE:
                    pulse_actions, refreshed, provider_text = live_position_pulse(market)

                seconds_to_deep = seconds_until(next_deep_due, time.monotonic())
                seconds_to_fast = seconds_until(next_fast_due, time.monotonic())
                next_scan_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds_to_deep)).isoformat()
                next_fast_scan_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds_to_fast)).isoformat()
                deep_text = "deep scan running" if deep_future is not None else f"deep scan in {seconds_to_deep}s"
                fast_text = "fast scan running" if fast_future is not None else f"fast scan in {seconds_to_fast}s"
                pulse_text = (
                    f"Always-on engine active · {refreshed} positions refreshed via {provider_text} · "
                    f"{fast_text} · {deep_text} · {cadence.session_label} session"
                )
                if pulse_actions:
                    pulse_text += " · Actions: " + ", ".join(_format_action(action) for action in pulse_actions)
                set_market_status(
                    market,
                    "running",
                    pulse_text,
                    pulse=True,
                    next_fast_scan_at=next_fast_scan_at,
                    next_scan_at=next_scan_at,
                    session_label=cadence.session_label,
                    pulse_seconds=cadence.pulse_seconds,
                    fast_scan_seconds=cadence.fast_scan_seconds,
                    deep_scan_seconds=cadence.deep_scan_seconds,
                    actions_last_cycle=last_deep_actions + len(pulse_actions),
                    fast_actions_last_cycle=last_fast_actions,
                    cycle_errors=consecutive_errors,
                )
                consecutive_errors = 0

                if stop_event.wait(cadence.pulse_seconds):
                    break
            except Exception as exc:
                consecutive_errors += 1
                log.exception(
                    "%s always-on cycle error #%s; retrying in %ss: %s",
                    label,
                    consecutive_errors,
                    WORKER_CYCLE_ERROR_BACKOFF_SECONDS,
                    exc,
                )
                try:
                    set_market_status(
                        market,
                        "degraded",
                        f"Automatic recovery active after cycle error: {exc}",
                        session_label=cadence_for(market).session_label,
                        cycle_errors=consecutive_errors,
                    )
                except Exception:
                    log.exception("Could not write degraded worker status.")
                if stop_event.wait(WORKER_CYCLE_ERROR_BACKOFF_SECONDS):
                    break
    finally:
        deep_executor.shutdown(wait=False, cancel_futures=True)
        fast_executor.shutdown(wait=False, cancel_futures=True)
        if intelligence_executor is not None:
            intelligence_executor.shutdown(wait=False, cancel_futures=True)

    stopped_message = f"{label} worker stopped cleanly."
    log.info(stopped_message)
    try:
        set_market_status(market, "stopped", stopped_message)
    except Exception:
        log.exception("Could not update stopped worker status.")


if __name__ == "__main__":
    import os
    from structured_logging import configure_structured_logging

    configure_structured_logging(os.getenv("LOG_LEVEL", "INFO"))
    run_worker(os.getenv("WORKER_MARKET", "cash"))
