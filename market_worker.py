from __future__ import annotations

import json
import logging
import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
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


def _quote_payload_from_history(symbol: str, history: Any, price: float) -> dict[str, Any]:
    route = dict(getattr(history, "attrs", {}).get("provider_route", {}) or {})
    return {
        "symbol": str(symbol).upper(),
        "requested_symbol": route.get("requested_symbol"),
        "provider_symbol": route.get("provider_symbol"),
        "provider": route.get("provider"),
        "price": float(price),
        "quote_timestamp": route.get("quote_timestamp"),
        "interval": route.get("interval", "1d"),
        "quote_verified": route.get("quote_verified") is True,
        "source_identity": route.get("source_identity"),
        "cache_identity": route.get("cache_identity"),
        "ohlcv_fingerprint": route.get("ohlcv_fingerprint"),
    }


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


def _discover_symbol(market: str, symbol: str, name: str) -> tuple[Any, str, Any] | None:
    if stop_event.is_set():
        return None
    try:
        history = get_history(symbol, "1y", "1d")
        if history is None or history.empty:
            return None
        signal = analyze_market(symbol, history, 0.0)
        if signal is None:
            return None
        return signal, name, history
    except Exception as exc:
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
                continue
            signal = analyze_market(symbol, history, 0.0)
            if signal is None:
                continue
            setattr(signal, "market_data_route", dict(getattr(history, "attrs", {}).get("provider_route", {}) or {}))
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
            signals.append(signal)
            prices[symbol] = _quote_payload_from_history(symbol, history, float(getattr(signal, "price", 0.0) or 0.0))
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
                    signal.to_dict()
                    | {
                        "always_on_fast_scan": True,
                        "market_data_route": dict(getattr(history, "attrs", {}).get("provider_route", {}) or {}),
                        "trade_configuration": {
                            "mode": EXECUTION_MODE,
                            "scan": "fast",
                            "action": str(signal.action),
                            "confidence": float(signal.confidence),
                            "score": float(signal.score),
                            "entry_price": float(signal.price),
                        },
                    },
                    created_at=signal_created_at,
                )
                setattr(signal, "signal_id", signal_id)
                forecast = forecast_price(
                    history,
                    3 if market == "cash" else 1,
                    market=market,
                    source_interval=dict(getattr(history, "attrs", {}).get("provider_route", {}) or {}).get("interval", "1d"),
                )
                if forecast:
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
                actions.extend(process_signals(market, signals, prices=prices) or [])
            except Exception as exc:
                log.exception("%s fast execution failed: %s", market, exc)
        try:
            snapshot(market)
        except Exception as exc:
            log.debug("%s fast snapshot failed: %s", market, exc)
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
            setattr(signal, "market_data_route", dict(getattr(history, "attrs", {}).get("provider_route", {}) or {}))
            council = deliberate(signal, news.headlines[:8])
            signal.score = council["score"]
            signal.action = council["action"]
            signal.confidence = council["confidence"]
            signal = _normalize_starter_action(signal)
            signal.reason = (str(signal.reason) + " " + str(council["explanation"])).strip()
            signals.append(signal)
            prices[symbol] = _quote_payload_from_history(symbol, history, float(signal.price))
            signal_created_at = utc_now()
            setattr(signal, "created_at", signal_created_at)
            signal_id = save_json_signal(
                market,
                symbol,
                signal.price,
                signal.score,
                signal.action,
                signal.confidence,
                signal.to_dict()
                | {
                    "headlines": news.headlines[:8],
                    "news_source": news.source,
                    "news_priority": priority,
                    "market_data_route": dict(getattr(history, "attrs", {}).get("provider_route", {}) or {}),
                    "trade_configuration": {
                        "mode": EXECUTION_MODE,
                        "action": str(signal.action),
                        "confidence": float(signal.confidence),
                        "score": float(signal.score),
                        "entry_price": float(signal.price),
                    },
                    "oracle_council": council,
                },
                created_at=signal_created_at,
            )
            setattr(signal, "signal_id", signal_id)
            forecast = forecast_price(
                history,
                5,
                market=market,
                source_interval=dict(getattr(history, "attrs", {}).get("provider_route", {}) or {}).get("interval", "1d"),
            )
            if forecast:
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

        by_symbol = {item["symbol"]: item for item in ranked}
        signals.sort(
            key=lambda signal: by_symbol.get(str(getattr(signal, "symbol", "")), {}).get("opportunity_score", 0),
            reverse=True,
        )
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
                actions.extend(process_signals(market, signals, prices=prices) or [])
            except Exception as exc:
                log.exception("%s signal execution failed: %s", market, exc)
        try:
            snapshot(market)
        except Exception as exc:
            log.exception("%s portfolio snapshot failed: %s", market, exc)
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
