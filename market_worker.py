from __future__ import annotations

import json
import logging
import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Any

from config import (
    DEEP_ANALYSIS_CANDIDATES,
    EXECUTION_MODE,
    INTELLIGENCE_REFRESH_SECONDS,
    LIVE_SCAN_WORKERS,
    NEWS_PRIORITY_CANDIDATES,
    OPPORTUNITY_LIMIT,
    REALTIME_MODE,
    WATCHLISTS,
)
from database import (
    connect,
    initialize_database,
    save_forecast,
    save_intelligence_event,
    save_json_signal,
    trim_old_records,
    utc_now,
)
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


def _request_stop(*_: object) -> None:
    log.info("Worker shutdown requested.")
    stop_event.set()


signal.signal(signal.SIGTERM, _request_stop)
signal.signal(signal.SIGINT, _request_stop)


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
                next_scan_at TEXT,
                session_label TEXT,
                pulse_seconds INTEGER,
                deep_scan_seconds INTEGER,
                execution_mode TEXT DEFAULT 'paper',
                actions_last_cycle INTEGER DEFAULT 0
            )
            """
        )
        for statement in (
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS last_pulse TEXT",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS next_scan_at TEXT",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS session_label TEXT",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS pulse_seconds INTEGER",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS deep_scan_seconds INTEGER",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS execution_mode TEXT DEFAULT 'paper'",
            "ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS actions_last_cycle INTEGER DEFAULT 0",
        ):
            conn.execute(statement)


def set_market_status(
    market: str,
    status: str,
    message: str,
    completed: bool = False,
    *,
    pulse: bool = False,
    next_scan_at: str | None = None,
    session_label: str | None = None,
    pulse_seconds: int | None = None,
    deep_scan_seconds: int | None = None,
    actions_last_cycle: int | None = None,
) -> None:
    now = utc_now()
    status_market = market
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO market_worker_status (
                market, status, message, last_run, heartbeat, last_pulse,
                next_scan_at, session_label, pulse_seconds, deep_scan_seconds,
                execution_mode, actions_last_cycle
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (market) DO UPDATE SET
                status = EXCLUDED.status,
                message = EXCLUDED.message,
                heartbeat = EXCLUDED.heartbeat,
                last_run = CASE WHEN %s THEN EXCLUDED.last_run ELSE market_worker_status.last_run END,
                last_pulse = CASE WHEN %s THEN EXCLUDED.last_pulse ELSE market_worker_status.last_pulse END,
                next_scan_at = COALESCE(EXCLUDED.next_scan_at, market_worker_status.next_scan_at),
                session_label = COALESCE(EXCLUDED.session_label, market_worker_status.session_label),
                pulse_seconds = COALESCE(EXCLUDED.pulse_seconds, market_worker_status.pulse_seconds),
                deep_scan_seconds = COALESCE(EXCLUDED.deep_scan_seconds, market_worker_status.deep_scan_seconds),
                execution_mode = EXCLUDED.execution_mode,
                actions_last_cycle = COALESCE(EXCLUDED.actions_last_cycle, market_worker_status.actions_last_cycle)
            """,
            (
                status_market,
                status,
                message,
                now if completed else None,
                now,
                now if pulse else None,
                next_scan_at,
                session_label,
                pulse_seconds,
                deep_scan_seconds,
                EXECUTION_MODE,
                actions_last_cycle,
                completed,
                pulse,
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
    prices: dict[str, float] = {}
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
            council = deliberate(signal, news.headlines[:8])
            signal.score = council["score"]
            signal.action = council["action"]
            signal.confidence = council["confidence"]
            signal.reason = (str(signal.reason) + " " + str(council["explanation"])).strip()
            signals.append(signal)
            prices[symbol] = float(signal.price)
            save_json_signal(
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
            )
            forecast = forecast_price(history, 5)
            if forecast:
                save_forecast(market, symbol, forecast)
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

    try:
        update_prices(market, prices)
    except Exception as exc:
        log.exception("%s price update failed: %s", market, exc)

    actions: list[Any] = []
    try:
        actions.extend(risk_exits(market, prices) or [])
    except Exception as exc:
        log.exception("%s deep-scan risk exits failed: %s", market, exc)
    try:
        actions.extend(process_signals(market, signals) or [])
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
    prices = {symbol: item.price for symbol, item in snapshots.items() if item.price > 0}
    provider_names = sorted({item.provider for item in snapshots.values() if item.provider})
    provider_text = ", ".join(provider_names[:2]) if provider_names else "unavailable"
    if not prices:
        return [], 0, provider_text
    try:
        update_prices(market, prices)
    except Exception as exc:
        log.warning("%s pulse price update failed: %s", market, exc)
    actions: list[Any] = []
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
        trim_old_records()
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

    initialize_database()
    from migrations import run_migrations

    run_migrations()
    _ensure_status_table()
    label = "Stock Market" if market == "cash" else "Crypto Market"
    initial = cadence_for(market)
    log.info(
        "Starting %s live worker | pulse=%ss deep=%ss session=%s mode=%s",
        label,
        initial.pulse_seconds,
        initial.deep_scan_seconds,
        initial.session_label,
        EXECUTION_MODE,
    )
    set_market_status(
        market,
        "starting",
        f"{label} live pulse is starting.",
        session_label=initial.session_label,
        pulse_seconds=initial.pulse_seconds,
        deep_scan_seconds=initial.deep_scan_seconds,
    )

    deep_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{market}-deep")
    intelligence_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="market-intelligence") if market == "cash" else None
    deep_future: Future[list[Any]] | None = None
    intelligence_future: Future[None] | None = None
    next_deep_due = time.monotonic()
    next_intelligence_due = time.monotonic()
    last_deep_actions = 0

    try:
        while not stop_event.is_set():
            cadence = cadence_for(market)
            now_monotonic = time.monotonic()

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
                    deep_scan_seconds=cadence.deep_scan_seconds,
                    actions_last_cycle=last_deep_actions,
                )
                log.info(message)

            if deep_future is None and now_monotonic >= next_deep_due:
                deep_future = deep_executor.submit(scan_market, market)
                next_deep_due = now_monotonic + cadence.deep_scan_seconds
                log.info("%s deep scan launched; next target in %ss", label, cadence.deep_scan_seconds)

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
            seconds_to_scan = seconds_until(next_deep_due, time.monotonic())
            next_scan_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds_to_scan)).isoformat()
            running_text = "deep scan running" if deep_future is not None else f"next deep scan in {seconds_to_scan}s"
            pulse_text = (
                f"Live pulse active · {refreshed} positions refreshed via {provider_text} · "
                f"{running_text} · {cadence.session_label} session"
            )
            if pulse_actions:
                pulse_text += " · Actions: " + ", ".join(_format_action(action) for action in pulse_actions)
            set_market_status(
                market,
                "running",
                pulse_text,
                pulse=True,
                next_scan_at=next_scan_at,
                session_label=cadence.session_label,
                pulse_seconds=cadence.pulse_seconds,
                deep_scan_seconds=cadence.deep_scan_seconds,
                actions_last_cycle=last_deep_actions + len(pulse_actions),
            )

            if stop_event.wait(cadence.pulse_seconds):
                break
    finally:
        deep_executor.shutdown(wait=False, cancel_futures=True)
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

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    run_worker(os.getenv("WORKER_MARKET", "cash"))
