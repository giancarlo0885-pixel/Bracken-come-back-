from __future__ import annotations

from dataclasses import replace

import hashlib
import json
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from config import *
from capital_allocator import adaptive_capital_allocation
from database import connect, row, rows, utc_now
from execution_policy import execution_policy
from forecast_quality import model_execution_approved
from quant_trade_standard import assess_trade
from oracle_intelligence import evaluate_opportunity
from market_memory import record_closed_trade_memory
from market_data import MarketSnapshot
from provider_router import normalize_symbol
from risk_engine import ExecutionSwitches, pre_trade_risk_checks
from market_sessions import (
    confirmed_us_listing,
    is_otc_exchange,
    market_session_state,
    normalize_exchange,
    parse_utc,
    quote_is_fresh,
)
from paper_broker import (
    accrued_interest,
    allocate_purchase,
    allocate_sale,
    build_account,
    market_leverage_limit,
    market_starting_capital,
)


log = logging.getLogger("oracle-bot")
_AUTOTRADE_DISABLED_LOGGED = False
PAPER_MARGIN_REDUCTION_REASON = "paper_margin_reduction"
QUOTE_PRICE_TOLERANCE_PCT = 0.001


# =========================================================
# FLEXIBLE TRADING SETTINGS
# =========================================================

FLEXIBLE_COOLDOWN_FACTOR = float(
    globals().get("FLEXIBLE_COOLDOWN_FACTOR", 0.10)
)

HIGH_CONFIDENCE_THRESHOLD = float(
    globals().get("HIGH_CONFIDENCE_THRESHOLD", 0.48)
)

HIGH_SCORE_THRESHOLD = float(
    globals().get("HIGH_SCORE_THRESHOLD", 52.0)
)

EXTRA_OPEN_POSITIONS = int(
    globals().get("EXTRA_OPEN_POSITIONS", 6)
)

MIN_CASH_RESERVE_PCT = float(
    globals().get("MIN_CASH_RESERVE_PCT", 0.01)
)

MIN_TRADE_VALUE = float(
    globals().get("MIN_TRADE_VALUE", 1.00)
)

MAX_TRADE_VALUE_PCT = float(
    globals().get("MAX_TRADE_VALUE_PCT", 0.35)
)
from profit_attribution import fifo_close_lots

MAX_SECTOR_EXPOSURE_PCT = float(
    globals().get("MAX_SECTOR_EXPOSURE_PCT", 0.35)
)
MAX_STOCK_SECTOR_EXPOSURE_PCT = float(
    globals().get("MAX_STOCK_SECTOR_EXPOSURE_PCT", MAX_SECTOR_EXPOSURE_PCT)
)

DEFAULT_STOP_LOSS_PCT = float(
    globals().get("STOP_LOSS_PCT", 0.06)
)

DEFAULT_TAKE_PROFIT_PCT = float(
    globals().get("TAKE_PROFIT_PCT", 0.10)
)

DEFAULT_TRAILING_STOP_PCT = float(
    globals().get("TRAILING_STOP_PCT", 0.045)
)

DEFAULT_COOLDOWN_MINUTES = int(
    globals().get("TRADE_COOLDOWN_MINUTES", 15)
)

DEFAULT_MAX_OPEN_POSITIONS = int(
    globals().get("MAX_OPEN_POSITIONS", 14)
)

STARTING_BALANCE_VALUE = float(
    globals().get("STARTING_BALANCE", 200.0)
)


# =========================================================
# BASIC HELPERS
# =========================================================

def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        result = float(value)
        if result != result or not math.isfinite(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def safe_text(
    value: Any,
    default: str = "",
) -> str:
    if value is None:
        return default
    return str(value).strip()


def _parse_utc(value: Any) -> datetime | None:
    return parse_utc(value)


def _entry_forecast_gate(
    market: str,
    symbol: str,
    price: float,
    signal: Any | None = None,
    quote: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Require a fresh, actionable forecast before a new paper entry.

    The deep worker saves the forecast before calling ``process_signals``. This
    final execution gate keeps stale ranking records and quote-only signals from
    reaching the institutional paper broker as new purchases.
    """
    if not REQUIRE_TARGET_FOR_BUY:
        return True, "forecast gate disabled"
    signal_id = signal_value(signal, "signal_id", signal_value(signal, "id", None))
    if signal_id in (None, ""):
        return False, "forecast signal_id is missing"

    forecast = row(
        """
        SELECT signal_id, target_price, low_price, high_price, probability_up, created_at,
               requested_symbol, provider_symbol, source_interval,
               source_quote_timestamp, scan_type, model, model_version,
               expected_move_pct, data_quality_score, forecast_id, symbol
        FROM forecasts
        WHERE market = %s AND symbol = %s AND signal_id = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (market, symbol, signal_id),
    ) or {}
    if not forecast:
        return False, "forecast linked to current signal is missing"
    required_fields = (
        "signal_id",
        "requested_symbol",
        "provider_symbol",
        "scan_type",
        "source_interval",
        "source_quote_timestamp",
        "model",
        "model_version",
        "data_quality_score",
        "forecast_id",
        "created_at",
    )
    for field in required_fields:
        if forecast.get(field) in (None, ""):
            return False, f"forecast {field} is missing"
    target = safe_float(forecast.get("target_price"))
    if price <= 0:
        return False, "missing live entry price"
    if target <= 0:
        return False, "missing current forecast target"

    quote = quote or {}
    source_interval = safe_text(forecast.get("source_interval"))
    quote_interval = safe_text(quote.get("interval") or signal_value(signal, "source_interval", ""))
    if not quote_interval:
        return False, "signal interval is missing"
    if source_interval != quote_interval:
        return False, f"forecast interval {source_interval} does not match signal interval {quote_interval}"
    forecast_scan = safe_text(forecast.get("scan_type"))
    signal_scan = safe_text(signal_value(signal, "scan_type", "") or _signal_route(signal).get("scan_type", ""))
    if not signal_scan:
        return False, "signal scan type is missing"
    if forecast_scan != signal_scan:
        return False, f"forecast scan type {forecast_scan} does not match signal scan type {signal_scan}"
    requested = _normalized_symbol(forecast.get("requested_symbol"))
    provider_symbol = _normalized_symbol(forecast.get("provider_symbol"))
    if _normalized_symbol(forecast.get("symbol")) != _normalized_symbol(symbol) or requested != _normalized_symbol(symbol) or provider_symbol != _normalized_symbol(symbol):
        return False, "forecast symbol identity does not match signal"
    source_quote_time = forecast.get("source_quote_timestamp")
    quote_time = quote.get("quote_timestamp") or quote.get("timestamp")
    if not quote_time:
        return False, "verified execution quote timestamp is missing"
    forecast_quote = _parse_utc(source_quote_time)
    signal_quote = _parse_utc(quote_time)
    if forecast_quote is None or signal_quote is None:
        return False, "forecast quote timestamp is invalid"
    if abs((forecast_quote - signal_quote).total_seconds()) > 1:
        return False, "forecast quote timestamp does not match signal quote"
    quality = safe_float(forecast.get("data_quality_score"), -1.0)
    if quality < FORECAST_MIN_DATA_QUALITY_SCORE:
        return False, f"forecast data quality {quality:.1f} is below {FORECAST_MIN_DATA_QUALITY_SCORE:.1f}"

    created = _parse_utc(forecast.get("created_at"))
    max_age = DECISION_CRYPTO_MAX_AGE_MINUTES if market == "crypto" else DECISION_STOCK_MAX_AGE_MINUTES
    if created is None:
        return False, "forecast timestamp is missing"
    age_minutes = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 60.0)
    if age_minutes > max_age:
        return False, f"forecast is stale ({age_minutes:.0f} minutes old)"

    expected_move_pct = ((target / price) - 1.0) * 100.0
    minimum_move = MIN_ACTIONABLE_MOVE_CRYPTO_PCT if market == "crypto" else MIN_ACTIONABLE_MOVE_STOCK_PCT
    if expected_move_pct < minimum_move:
        return False, f"expected move {expected_move_pct:.2f}% is below {minimum_move:.2f}%"
    validation_ok, validation_reason = model_execution_approved(
        symbol,
        "crypto" if market == "crypto" else "stock",
        source_interval or quote_interval or "1d",
        safe_text(forecast.get("model"), "log-return diffusion"),
        safe_text(forecast.get("model_version")),
    )
    if not validation_ok:
        return False, validation_reason
    return True, f"forecast approved with {expected_move_pct:.2f}% expected move"


def signal_value(
    signal: Any,
    name: str,
    default: Any = None,
) -> Any:
    if signal is None:
        return default

    if isinstance(signal, dict):
        return signal.get(name, default)

    return getattr(signal, name, default)


def normalized_score(signal: Any) -> float:
    score = safe_float(signal_value(signal, "score", 0.0))

    if score <= 1.0:
        score *= 100.0

    return max(0.0, min(100.0, score))


def normalized_confidence(signal: Any) -> float:
    confidence = safe_float(
        signal_value(signal, "confidence", 0.0)
    )

    if confidence > 1.0:
        confidence /= 100.0

    return max(0.0, min(1.0, confidence))


def signal_action(signal: Any) -> str:
    return safe_text(
        signal_value(signal, "action", "HOLD"),
        "HOLD",
    ).upper()


def signal_price(
    signal: Any,
    fallback: float = 0.0,
) -> float:
    for field in (
        "price",
        "current_price",
        "close",
        "last_price",
        "spot",
    ):
        value = safe_float(signal_value(signal, field, 0.0))
        if value > 0:
            return value

    return fallback


def _normalized_symbol(value: Any) -> str:
    return normalize_symbol(value)


def _execution_overrides() -> dict[str, Any]:
    return {
        "ENABLE_AUTOTRADE": globals().get("ENABLE_AUTOTRADE", False),
        "ENABLE_STOCK_AUTOTRADE": globals().get("ENABLE_STOCK_AUTOTRADE", False),
        "ENABLE_CRYPTO_AUTOTRADE": globals().get("ENABLE_CRYPTO_AUTOTRADE", False),
        "ENABLE_NEW_ENTRIES": globals().get("ENABLE_NEW_ENTRIES", False),
        "ENABLE_AUTOMATED_EXITS": globals().get("ENABLE_AUTOMATED_EXITS", False),
        "ENABLE_PORTFOLIO_ROTATION": globals().get("ENABLE_PORTFOLIO_ROTATION", False),
        "ENABLE_BROKER_SUBMISSION": globals().get("ENABLE_BROKER_SUBMISSION", False),
        "GLOBAL_KILL_SWITCH": globals().get("GLOBAL_KILL_SWITCH", False),
    }


def _execution_policy(market: str = "cash", intent: str = "entry"):
    return execution_policy(market=market, intent=intent, overrides=_execution_overrides())


def _autotrade_enabled(market: str = "cash", intent: str = "entry") -> bool:
    return _execution_policy(market, intent).allowed


def _execution_disabled(reason: str, market: str = "cash", intent: str = "entry") -> bool:
    global _AUTOTRADE_DISABLED_LOGGED
    policy = _execution_policy(market, intent)
    if policy.allowed:
        return False
    if not _AUTOTRADE_DISABLED_LOGGED:
        log.warning("Execution disabled by central policy (%s); %s blocked.", policy.reason, reason)
        _AUTOTRADE_DISABLED_LOGGED = True
    return True


def _signal_route(signal: Any) -> dict[str, Any]:
    route = signal_value(signal, "market_data_route", None)
    if isinstance(route, dict):
        return dict(route)
    payload = signal_value(signal, "payload", None)
    if isinstance(payload, dict) and isinstance(payload.get("market_data_route"), dict):
        return dict(payload["market_data_route"])
    return {}


def _quote_identity_metadata(signal: Any) -> dict[str, Any]:
    metadata = _signal_route(signal)
    for key in (
        "requested_symbol",
        "provider_symbol",
        "quote_symbol",
        "provider",
        "price",
        "quote_timestamp",
        "timestamp",
        "interval",
        "quote_verified",
        "verified",
        "stale",
        "bid",
        "ask",
        "spread_pct",
        "source_capability",
        "correlation_id",
        "decision_correlation_id",
        "source_identity",
        "cache_identity",
        "ohlcv_fingerprint",
    ):
        value = signal_value(signal, key, None)
        if value not in (None, "", []):
            metadata[key] = value
    return metadata


def _quote_payload(value: Any, symbol: str = "") -> dict[str, Any]:
    if isinstance(value, MarketSnapshot):
        return value.to_quote_payload()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _verified_quote_for(
    symbol: str,
    quotes: dict[str, Any] | None,
    market: str = "cash",
) -> dict[str, Any] | None:
    symbol = _normalized_symbol(symbol)
    if not quotes:
        return None
    payload = _quote_payload(quotes.get(symbol) or quotes.get(symbol.lower()), symbol)
    if not payload:
        return None
    price = safe_float(payload.get("price"))
    if price <= 0:
        return None
    requested = _normalized_symbol(payload.get("requested_symbol"))
    provider_symbol = _normalized_symbol(payload.get("provider_symbol"))
    quote_symbol = _normalized_symbol(payload.get("symbol") or symbol)
    if quote_symbol != symbol or requested != symbol or provider_symbol != symbol:
        return None
    if payload.get("quote_verified") is not True:
        return None
    quote_timestamp = payload.get("quote_timestamp") or payload.get("timestamp")
    if _parse_utc(quote_timestamp) is None:
        return None
    max_age_seconds = (
        DECISION_CRYPTO_MAX_AGE_MINUTES
        if safe_text(market).lower() == "crypto"
        else DECISION_STOCK_MAX_AGE_MINUTES
    ) * 60
    if not quote_is_fresh(
        quote_timestamp,
        safe_text(payload.get("interval"), "1d"),
        max_intraday_age_seconds=max_age_seconds,
        symbol=symbol,
    ):
        return None
    payload["price"] = price
    return payload


def _quote_rejection_reason(symbol: str, quotes: dict[str, Any] | None, market: str = "cash") -> str:
    symbol = _normalized_symbol(symbol)
    payload = _quote_payload((quotes or {}).get(symbol) or (quotes or {}).get(symbol.lower()), symbol)
    if not payload:
        return "invalid/missing price"
    price = safe_float(payload.get("price"))
    if price <= 0:
        return "invalid/missing price"
    requested = _normalized_symbol(payload.get("requested_symbol"))
    provider_symbol = _normalized_symbol(payload.get("provider_symbol"))
    quote_symbol = _normalized_symbol(payload.get("symbol") or symbol)
    if quote_symbol != symbol or requested != symbol or provider_symbol != symbol:
        return "provider quote identity mismatch"
    if payload.get("quote_verified") is not True:
        return "quote is not provider verified"
    quote_timestamp = payload.get("quote_timestamp") or payload.get("timestamp")
    interval = safe_text(payload.get("interval"), "1d")
    if _parse_utc(quote_timestamp) is None:
        return "verified quote timestamp is missing"
    max_age_seconds = (
        DECISION_CRYPTO_MAX_AGE_MINUTES
        if safe_text(market).lower() == "crypto"
        else DECISION_STOCK_MAX_AGE_MINUTES
    ) * 60
    if not quote_is_fresh(
        quote_timestamp,
        interval,
        max_intraday_age_seconds=max_age_seconds,
        symbol=symbol,
    ):
        intraday = not interval.lower().endswith("d") and interval.lower() not in {"1wk", "1mo", "3mo"}
        session = market_session_state(exchange=payload.get("exchange"), region=payload.get("region"), symbol=symbol)
        if market == "cash" and intraday and session in {"closed", "after-hours", "premarket"}:
            return "MARKET_CLOSED_STALE_INTRADAY_QUOTE"
        return "verified quote timestamp is stale"
    return "verified quote unavailable"


def _verified_price_for(symbol: str, quotes: dict[str, Any] | None, market: str = "cash") -> float:
    quote = _verified_quote_for(symbol, quotes, market)
    return safe_float(quote.get("price")) if quote else 0.0


def _quote_price_matches(signal_price_value: float, quote_price_value: float) -> bool:
    signal_price_value = safe_float(signal_price_value)
    quote_price_value = safe_float(quote_price_value)
    if signal_price_value <= 0 or quote_price_value <= 0:
        return False
    tolerance = max(0.01, quote_price_value * QUOTE_PRICE_TOLERANCE_PCT)
    return abs(signal_price_value - quote_price_value) <= tolerance


def _execution_quote_guard(
    market: str,
    symbol: str,
    price: float,
    signal: Any,
    position: dict[str, Any] | None = None,
    quote_metadata: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    symbol = _normalized_symbol(symbol)
    signal_symbol = _normalized_symbol(signal_value(signal, "symbol", symbol))
    if signal_symbol != symbol:
        return False, f"signal/quote symbol mismatch ({signal_symbol}/{symbol})"
    if position is not None:
        position_symbol = _normalized_symbol(position.get("symbol"))
        if position_symbol != symbol:
            return False, f"position/quote symbol mismatch ({position_symbol}/{symbol})"
    if price <= 0 or not math.isfinite(price):
        return False, "quote price is invalid"
    metadata = _quote_identity_metadata(signal)
    if quote_metadata:
        metadata.update({key: value for key, value in quote_metadata.items() if value not in (None, "", [])})
    if not metadata:
        return False, "verified quote identity metadata is missing"
    requested = _normalized_symbol(metadata.get("requested_symbol") or metadata.get("quote_symbol"))
    provider_symbol = _normalized_symbol(metadata.get("provider_symbol") or metadata.get("quote_symbol"))
    if not requested:
        return False, "requested quote symbol is missing"
    if not provider_symbol:
        return False, "provider quote symbol is missing"
    if requested != symbol or provider_symbol != symbol:
        return False, f"provider quote identity mismatch ({requested}/{provider_symbol}/{symbol})"
    if metadata.get("quote_verified") is not True:
        return False, "quote is not provider verified"
    quote_price = safe_float(metadata.get("price"))
    if quote_price <= 0:
        return False, "verified quote price is missing"
    if not _quote_price_matches(price, quote_price):
        return False, "execution price differs from verified quote"
    quote_timestamp = metadata.get("quote_timestamp") or metadata.get("timestamp")
    if _parse_utc(quote_timestamp) is None:
        return False, "verified quote timestamp is missing"
    max_age_seconds = (
        DECISION_CRYPTO_MAX_AGE_MINUTES
        if safe_text(market).lower() == "crypto"
        else DECISION_STOCK_MAX_AGE_MINUTES
    ) * 60
    if not quote_is_fresh(
        quote_timestamp,
        safe_text(metadata.get("interval"), "1d"),
        max_intraday_age_seconds=max_age_seconds,
        symbol=symbol,
    ):
        return False, "verified quote timestamp is stale"
    return True, "quote identity verified"


def _duplicate_price_anomaly_symbols(prices: dict[str, Any]) -> set[str]:
    grouped: dict[str, list[str]] = {}
    for raw_symbol, raw_price in (prices or {}).items():
        symbol = _normalized_symbol(raw_symbol)
        payload = _quote_payload(raw_price, symbol)
        if not payload:
            continue
        for field in ("cache_identity", "source_identity", "ohlcv_fingerprint"):
            identity = safe_text(payload.get(field))
            if symbol and identity:
                grouped.setdefault(identity, []).append(symbol)
    blocked: set[str] = set()
    for identity, symbols in grouped.items():
        unique = sorted(set(symbols))
        if len(unique) >= 2:
            blocked.update(unique)
            log.warning(
                "Duplicate provider/cache execution anomaly blocked | identity=%s affected_symbols=%d sample=%s",
                identity[:80],
                len(unique),
                ",".join(unique[:8]),
            )
    return blocked


def _repriced_positions(
    positions: list[dict[str, Any]],
    quotes: dict[str, Any],
    market: str,
    anomalous_symbols: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    anomalous_symbols = anomalous_symbols or set()
    repriced: list[dict[str, Any]] = []
    missing: list[str] = []
    for position in positions:
        item = dict(position)
        symbol = _normalized_symbol(item.get("symbol"))
        if not symbol:
            missing.append("<missing-symbol>")
            repriced.append(item)
            continue
        if symbol in anomalous_symbols:
            missing.append(symbol)
            repriced.append(item)
            continue
        price = _verified_price_for(symbol, quotes, market)
        if price <= 0:
            missing.append(symbol)
            repriced.append(item)
            continue
        item["current_price"] = price
        repriced.append(item)
    return repriced, missing


def _current_account_from_quotes(
    market: str,
    positions: list[dict[str, Any]],
    quotes: dict[str, Any],
    anomalous_symbols: set[str],
) -> tuple[Any | None, list[str], list[dict[str, Any]]]:
    repriced, missing = _repriced_positions(positions, quotes, market, anomalous_symbols)
    if missing:
        return None, missing, repriced
    return build_account(market, ensure_portfolio(market), repriced), [], repriced


def execute(
    sql: str,
    params: tuple[Any, ...] = (),
) -> None:
    with connect() as conn:
        conn.execute(sql, params)


def _claim_field(value: Any) -> str:
    return safe_text(value, "")


def _forecast_id_for_signal(market: str, symbol: str, signal_id: Any) -> str:
    if signal_id in (None, ""):
        return ""
    record = row(
        "SELECT forecast_id FROM forecasts WHERE market=%s AND symbol=%s AND signal_id=%s ORDER BY id DESC LIMIT 1",
        (market, symbol, signal_id),
    ) or {}
    return safe_text(record.get("forecast_id"))


def _execution_key(
    *,
    market: str,
    symbol: str,
    side: str,
    price: float,
    quote: dict[str, Any],
    signal: Any | None = None,
    position: dict[str, Any] | None = None,
) -> tuple[str, str]:
    signal_id = signal_value(signal, "signal_id", signal_value(signal, "id", ""))
    forecast_id = signal_value(signal, "forecast_id", "") or _forecast_id_for_signal(market, symbol, signal_id)
    quote_timestamp = _claim_field(quote.get("quote_timestamp") or quote.get("timestamp"))
    source_identity = _claim_field(quote.get("source_identity") or quote.get("cache_identity") or quote.get("provider"))
    parts = [
        market,
        _normalized_symbol(symbol),
        side.upper(),
        f"{safe_float(price):.8f}",
        quote_timestamp,
        source_identity,
        _claim_field(signal_id),
        _claim_field(forecast_id),
    ]
    if side.upper() == "SELL" and position:
        parts.extend([_claim_field(position.get("id")), _claim_field(position.get("opened_at"))])
    decision_id = "|".join(parts)
    return hashlib.sha256(decision_id.encode("utf-8")).hexdigest(), decision_id


def _try_execution_claim(
    conn: Any,
    *,
    market: str,
    symbol: str,
    side: str,
    price: float,
    quote: dict[str, Any],
    signal: Any | None = None,
    position: dict[str, Any] | None = None,
) -> tuple[bool, str, str]:
    quote_timestamp = _claim_field(quote.get("quote_timestamp") or quote.get("timestamp"))
    source_identity = _claim_field(quote.get("source_identity") or quote.get("cache_identity") or quote.get("provider"))
    if not quote_timestamp or not source_identity:
        return False, "", "execution claim requires quote timestamp and source identity"
    execution_key, decision_id = _execution_key(
        market=market,
        symbol=symbol,
        side=side,
        price=price,
        quote=quote,
        signal=signal,
        position=position,
    )
    try:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (execution_key,))
    except Exception as exc:
        return False, execution_key, f"execution advisory lock failed: {exc}"
    record = conn.execute(
        """
        INSERT INTO execution_claims
        (execution_key, decision_id, market, symbol, side, quote_timestamp,
         verified_price, source_identity, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'claimed',%s)
        ON CONFLICT DO NOTHING
        RETURNING execution_key
        """,
        (
            execution_key,
            decision_id,
            market,
            symbol,
            side.upper(),
            quote_timestamp,
            safe_float(price),
            source_identity,
            utc_now(),
        ),
    ).fetchone()
    if not record:
        return False, execution_key, "duplicate execution claim"
    return True, execution_key, "execution claimed"


def _complete_execution_claim(conn: Any, execution_key: str) -> None:
    conn.execute(
        "UPDATE execution_claims SET status='completed', completed_at=%s WHERE execution_key=%s",
        (utc_now(), execution_key),
    )


def _risk_switches() -> ExecutionSwitches:
    return ExecutionSwitches(
        autotrade=globals().get("ENABLE_AUTOTRADE", False),
        stock_autotrade=globals().get("ENABLE_STOCK_AUTOTRADE", False),
        crypto_autotrade=globals().get("ENABLE_CRYPTO_AUTOTRADE", False),
        new_entries=globals().get("ENABLE_NEW_ENTRIES", False),
        automated_exits=globals().get("ENABLE_AUTOMATED_EXITS", False),
        portfolio_rotation=globals().get("ENABLE_PORTFOLIO_ROTATION", False),
        broker_submission=globals().get("ENABLE_BROKER_SUBMISSION", False),
        global_kill_switch=globals().get("GLOBAL_KILL_SWITCH", False),
    )


def _finite_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _quote_spread_pct(quote: dict[str, Any]) -> float | None:
    explicit = _finite_number(quote.get("spread_pct"))
    if explicit is not None:
        return explicit
    bid = _finite_number(quote.get("bid"))
    ask = _finite_number(quote.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    midpoint = (bid + ask) / 2.0
    return (ask - bid) / midpoint if midpoint > 0 else None


def _quote_slippage_pct(quote: dict[str, Any]) -> float | None:
    for key in ("slippage_pct", "estimated_slippage_pct"):
        value = _finite_number(quote.get(key))
        if value is not None:
            return value
    return None


def _quote_liquidity_value(quote: dict[str, Any], price: float) -> float | None:
    for key in ("liquidity_value", "dollar_volume", "avg_dollar_volume", "average_dollar_volume"):
        value = _finite_number(quote.get(key))
        if value is not None and value >= 0:
            return value
    volume = _finite_number(quote.get("volume"))
    if volume is not None and volume >= 0 and price > 0:
        return volume * price
    return None


def _period_start(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _trade_sum(conn: Any, market: str, sql_expr: str, since: str, sides: tuple[str, ...] = ("BUY", "SELL")) -> float:
    record = conn.execute(
        f"""
        SELECT COALESCE(SUM({sql_expr}), 0) AS total
        FROM trades
        WHERE market=%s
          AND side = ANY(%s)
          AND created_at >= %s
        """,
        (market, list(sides), since),
    ).fetchone()
    return safe_float(record.get("total") if record else 0.0)


def _unrealized_pnl(positions: list[dict[str, Any]]) -> float:
    total = 0.0
    for position in positions:
        quantity = safe_float(position.get("quantity"))
        current = safe_float(position.get("current_price"))
        average = safe_float(position.get("average_price"), safe_float(position.get("entry_price")))
        if quantity > 0 and current > 0 and average > 0:
            total += (current - average) * quantity
    return total


def _build_execution_risk_context(
    conn: Any,
    *,
    market: str,
    symbol: str,
    side: str,
    intent: str,
    order_value: float,
    portfolio: dict[str, Any],
    positions: list[dict[str, Any]],
    quote: dict[str, Any],
    concentration_pct: float | None = None,
) -> dict[str, Any]:
    """Build finite risk inputs from the locked execution transaction."""
    price = safe_float(quote.get("price"))
    repriced = [dict(position) for position in positions]
    quote_symbol = _normalized_symbol(symbol)
    for position in repriced:
        if _normalized_symbol(position.get("symbol")) == quote_symbol and price > 0:
            position["current_price"] = price
    account = build_account(market, portfolio, repriced)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start = _period_start(7)
    daily_realized = _trade_sum(conn, market, "realized_pnl", today_start, ("SELL",))
    weekly_realized = _trade_sum(conn, market, "realized_pnl", week_start, ("SELL",))
    daily_unrealized = _unrealized_pnl(repriced)
    weekly_unrealized = daily_unrealized
    equity_basis = max(account.equity, safe_float(portfolio.get("starting_balance")), 0.0)
    daily_loss_pct = None if equity_basis <= 0 else max(0.0, -(daily_realized + daily_unrealized) / equity_basis)
    weekly_loss_pct = None if equity_basis <= 0 else max(0.0, -(weekly_realized + weekly_unrealized) / equity_basis)
    new_entries_record = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM trades
        WHERE market=%s AND side='BUY' AND created_at >= %s
        """,
        (market, today_start),
    ).fetchone()
    new_entries_today = safe_float(new_entries_record.get("total") if new_entries_record else None, -1.0)
    turnover_value = _trade_sum(conn, market, "value", today_start, ("BUY", "SELL"))
    turnover_pct_today = None if account.equity <= 0 else turnover_value / account.equity
    target_concentration = concentration_pct
    if target_concentration is None:
        existing_value = 0.0
        for position in repriced:
            if _normalized_symbol(position.get("symbol")) == quote_symbol:
                existing_value += safe_float(position.get("quantity")) * safe_float(position.get("current_price"))
        target_value = max(0.0, existing_value + (order_value if side.upper() == "BUY" else 0.0))
        target_concentration = None if account.equity <= 0 else target_value / account.equity
    return {
        "account": account,
        "positions": repriced,
        "cash": account.cash,
        "portfolio_equity": account.equity,
        "buying_power": account.buying_power,
        "gross_market_exposure": account.gross_exposure,
        "margin_debt": account.margin_debt,
        "leverage_used": account.leverage_used,
        "margin_utilization_pct": account.margin_utilization_pct,
        "open_position_count": len(repriced),
        "target_position_concentration": target_concentration,
        "daily_realized_pnl": daily_realized,
        "daily_unrealized_pnl": daily_unrealized,
        "daily_loss_pct": daily_loss_pct,
        "weekly_realized_pnl": weekly_realized,
        "weekly_unrealized_pnl": weekly_unrealized,
        "weekly_loss_pct": weekly_loss_pct,
        "new_entries_today": new_entries_today if new_entries_today >= 0 else None,
        "turnover_pct_today": turnover_pct_today,
        "correlation_exposure_pct": _finite_number(quote.get("correlation_exposure_pct")),
        "spread_pct": _quote_spread_pct(quote),
        "slippage_pct": _quote_slippage_pct(quote),
        "liquidity_value": _quote_liquidity_value(quote, price),
        "sector_exposure_pct": _finite_number(quote.get("sector_exposure_pct")),
    }


def _shared_risk_gate(
    *,
    conn: Any,
    market: str,
    symbol: str,
    side: str,
    intent: str,
    order_value: float,
    portfolio: dict[str, Any],
    quote: dict[str, Any],
    positions: list[dict[str, Any]],
    concentration_pct: float | None = None,
) -> tuple[bool, str, Any]:
    risk_context = _build_execution_risk_context(
        conn,
        market=market,
        symbol=symbol,
        side=side,
        intent=intent,
        order_value=order_value,
        portfolio=portfolio,
        positions=positions,
        quote=quote,
        concentration_pct=concentration_pct,
    )
    result = pre_trade_risk_checks(
        market=market,
        symbol=symbol,
        side=side,
        intent=intent,
        order_value=order_value,
        portfolio_equity=risk_context["portfolio_equity"],
        cash=risk_context["cash"],
        quote=quote,
        positions=positions,
        daily_loss_pct=risk_context["daily_loss_pct"],
        weekly_loss_pct=risk_context["weekly_loss_pct"],
        spread_pct=risk_context["spread_pct"],
        slippage_pct=risk_context["slippage_pct"],
        liquidity_value=risk_context["liquidity_value"],
        correlation_exposure_pct=risk_context["correlation_exposure_pct"],
        concentration_pct=risk_context["target_position_concentration"],
        new_entries_today=risk_context["new_entries_today"],
        turnover_pct_today=risk_context["turnover_pct_today"],
        leverage_used=risk_context["leverage_used"],
        margin_utilization_pct=risk_context["margin_utilization_pct"],
        switches=_risk_switches(),
    )
    result.metrics.update({key: value for key, value in risk_context.items() if isinstance(value, (int, float))})
    if not result.allowed:
        return False, "; ".join(result.reasons) or result.reason, result
    return True, "shared risk approved", result


def _position_market_value(position: dict[str, Any]) -> float:
    quantity = safe_float(position.get("quantity"))
    price = safe_float(position.get("current_price"))
    return max(0.0, quantity * price)


def _clean_sector(value: Any) -> str:
    text = safe_text(value).strip()
    if not text or text.lower() in {"unknown", "none", "null", "n/a"}:
        return ""
    return text.upper()


def _sector_from_candidate(symbol: str) -> str:
    symbol = _normalized_symbol(symbol)
    if not symbol:
        return ""
    try:
        record = row(
            """
            SELECT sector
            FROM global_market_candidates
            WHERE symbol = %s
            ORDER BY scanned_at DESC
            LIMIT 1
            """,
            (symbol,),
        ) or {}
    except Exception:
        return ""
    return _clean_sector(record.get("sector"))


def _sector_for_symbol(symbol: str, metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    for key in ("sector", "stock_sector", "gics_sector"):
        sector = _clean_sector(metadata.get(key))
        if sector:
            return sector
    return _sector_from_candidate(symbol)


def _sector_exposure_after(
    *,
    market: str,
    symbol: str,
    trade_value: float,
    equity: float,
    positions: list[dict[str, Any]],
    quote: dict[str, Any],
) -> tuple[float | None, str, float]:
    if market != "cash":
        return None, "", 0.0
    sector = _sector_for_symbol(symbol, quote)
    if not sector:
        return None, "", 0.0
    existing_sector_value = 0.0
    for position in positions:
        position_symbol = _normalized_symbol(position.get("symbol"))
        if not position_symbol:
            continue
        position_sector = _sector_for_symbol(position_symbol, position)
        if position_sector == sector:
            existing_sector_value += _position_market_value(position)
    sector_after = (existing_sector_value + max(0.0, trade_value)) / equity if equity > 0 else 1.0
    return sector_after, sector, existing_sector_value


def _paper_buy_safeguard(
    *,
    market: str,
    symbol: str,
    trade_value: float,
    account: Any,
    positions: list[dict[str, Any]],
    quote: dict[str, Any],
) -> tuple[bool, str]:
    symbol = _normalized_symbol(symbol)
    trade_value = safe_float(trade_value)
    equity = safe_float(getattr(account, "equity", 0.0))
    if trade_value <= 0 or equity <= 0:
        return False, "paper buy safeguard requires positive trade value and equity"
    price = safe_float(quote.get("price"))
    if price <= 0 or quote.get("quote_verified") is not True:
        return False, "paper buy safeguard requires a verified fresh price"
    if not quote_is_fresh(quote.get("quote_timestamp"), safe_text(quote.get("interval"), "1d"), symbol=symbol):
        return False, "paper buy safeguard rejected stale price"
    if safe_float(getattr(account, "buying_power", 0.0)) < trade_value:
        return False, "paper buy safeguard rejected insufficient buying power"
    if bool(getattr(account, "margin_call", False)):
        return False, "paper buy safeguard rejected margin-call account"
    if safe_float(getattr(account, "cash", 0.0)) - trade_value < equity * MIN_CASH_RESERVE_PCT:
        return False, "paper buy safeguard rejected cash reserve breach"

    existing_value = sum(
        _position_market_value(position)
        for position in positions
        if _normalized_symbol(position.get("symbol")) == symbol
    )
    resulting_position_value = existing_value + trade_value
    max_position_value = equity * MAX_POSITION_FRACTION
    if resulting_position_value > max_position_value:
        reason = "duplicate buy accumulation" if existing_value > 0 else "maximum position"
        return (
            False,
            f"paper buy safeguard rejected {reason} exposure "
            f"({resulting_position_value / equity:.2%}/{MAX_POSITION_FRACTION:.2%})",
        )

    leverage_limit = max(1.0, safe_float(getattr(account, "leverage_limit", market_leverage_limit(market))))
    max_gross_exposure = equity * leverage_limit * PAPER_MAX_MARGIN_UTILIZATION_PCT
    gross_after = safe_float(getattr(account, "gross_exposure", 0.0)) + trade_value
    if gross_after > max_gross_exposure:
        return False, "paper buy safeguard rejected maximum portfolio exposure"

    margin_utilization = safe_float(getattr(account, "margin_utilization_pct", 0.0))
    if margin_utilization >= PAPER_MAX_MARGIN_UTILIZATION_PCT * 100:
        return False, "paper buy safeguard rejected high margin utilization"

    sector_after, sector, _ = _sector_exposure_after(
        market=market,
        symbol=symbol,
        trade_value=trade_value,
        equity=equity,
        positions=positions,
        quote=quote,
    )
    if market == "cash" and not sector:
        return False, "paper buy safeguard requires verified stock sector metadata"
    sector_limit = MAX_STOCK_SECTOR_EXPOSURE_PCT if market == "cash" else MAX_SECTOR_EXPOSURE_PCT
    if sector_after is not None and sector_after > sector_limit:
        return (
            False,
            f"paper buy safeguard rejected sector concentration "
            f"{sector} ({sector_after:.2%}/{sector_limit:.2%})",
        )
    return True, "paper buy safeguard approved"


# =========================================================
# DATABASE COMPATIBILITY
# =========================================================

def _table_columns(
    table_name: str,
) -> set[str]:
    try:
        records = rows(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
            """,
            (table_name,),
        )

        return {
            safe_text(record.get("column_name"))
            for record in records
            if record.get("column_name")
        }

    except Exception:
        return set()


def _insert_compatible(
    table_name: str,
    values: dict[str, Any],
) -> bool:
    columns = _table_columns(table_name)

    if not columns:
        return False

    usable = {
        key: value
        for key, value in values.items()
        if key in columns
    }

    if not usable:
        return False

    names = list(usable)
    placeholders = ", ".join(["%s"] * len(names))
    sql_columns = ", ".join(names)

    try:
        execute(
            f"""
            INSERT INTO {table_name}
            ({sql_columns})
            VALUES ({placeholders})
            """,
            tuple(usable[name] for name in names),
        )
        return True

    except Exception:
        log.exception("Unable to insert into %s", table_name)
        return False


# =========================================================
# PORTFOLIO
# =========================================================

def ensure_portfolio(
    market: str,
) -> dict[str, Any]:
    market = safe_text(market).lower()

    existing = row(
        """
        SELECT *
        FROM portfolios
        WHERE market = %s
        """,
        (market,),
    )

    if existing:
        return existing

    now = utc_now()
    starting_capital = market_starting_capital(market)
    leverage_limit = market_leverage_limit(market)

    try:
        execute(
            """
            INSERT INTO portfolios
            (
                market, cash, starting_balance, leverage_limit, margin_debt,
                margin_interest_accrued, margin_interest_updated_at,
                broker_profile, updated_at
            )
            VALUES (%s, %s, %s, %s, 0, 0, %s, %s, %s)
            ON CONFLICT (market) DO NOTHING
            """,
            (
                market, starting_capital, starting_capital, leverage_limit,
                now, PAPER_BROKER_PROFILE, now,
            ),
        )

    except Exception:
        log.exception(
            "Could not initialize portfolio for %s",
            market,
        )

    return (
        row(
            """
            SELECT *
            FROM portfolios
            WHERE market = %s
            """,
            (market,),
        )
        or {
            "market": market,
            "cash": starting_capital,
            "starting_balance": starting_capital,
            "leverage_limit": leverage_limit,
            "margin_debt": 0.0,
            "broker_profile": PAPER_BROKER_PROFILE,
        }
    )


def _load_portfolio_read_only(market: str) -> dict[str, Any]:
    market = safe_text(market).lower()
    existing = row(
        """
        SELECT *
        FROM portfolios
        WHERE market = %s
        """,
        (market,),
    )
    if existing:
        return existing
    starting_capital = market_starting_capital(market)
    return {
        "market": market,
        "cash": starting_capital,
        "starting_balance": starting_capital,
        "leverage_limit": market_leverage_limit(market),
        "margin_debt": 0.0,
        "margin_interest_accrued": 0.0,
        "broker_profile": PAPER_BROKER_PROFILE,
    }


def _seconds_since(value: Any) -> float | None:
    try:
        then = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds())
    except Exception:
        return None


def _accrue_paper_margin_interest(market: str, portfolio: dict[str, Any]) -> dict[str, Any]:
    if not PAPER_BROKER_MODE:
        return portfolio
    debt = max(0.0, safe_float(portfolio.get("margin_debt")))
    if debt <= 0:
        return portfolio
    age = _seconds_since(portfolio.get("margin_interest_updated_at"))
    if age is None or age < PAPER_MARGIN_INTEREST_ACCRUAL_SECONDS:
        return portfolio

    try:
        with connect() as conn:
            current = conn.execute(
                "SELECT * FROM portfolios WHERE market=%s FOR UPDATE",
                (market,),
            ).fetchone()
            if not current:
                return portfolio
            current_debt = max(0.0, safe_float(current.get("margin_debt")))
            current_age = _seconds_since(current.get("margin_interest_updated_at"))
            if current_debt <= 0 or current_age is None or current_age < PAPER_MARGIN_INTEREST_ACCRUAL_SECONDS:
                return current
            interest = accrued_interest(
                market=market,
                margin_debt=current_debt,
                last_updated=current.get("margin_interest_updated_at"),
            )
            now = utc_now()
            if interest > 0:
                conn.execute(
                    """
                    UPDATE portfolios
                    SET margin_debt = margin_debt + %s,
                        margin_interest_accrued = COALESCE(margin_interest_accrued,0) + %s,
                        margin_interest_updated_at = %s,
                        updated_at = %s
                    WHERE market=%s
                    """,
                    (interest, interest, now, now, market),
                )
                updated = dict(current)
                updated["margin_debt"] = current_debt + interest
                updated["margin_interest_accrued"] = safe_float(current.get("margin_interest_accrued")) + interest
                updated["margin_interest_updated_at"] = now
                return updated
            return current
    except Exception:
        log.exception("Could not accrue paper margin interest for %s", market)
        return portfolio


def portfolio_equity(
    market: str,
    *,
    read_only: bool = False,
) -> dict[str, float]:
    market = safe_text(market).lower()
    execution_enabled = _autotrade_enabled(market)
    portfolio = _load_portfolio_read_only(market) if read_only or not execution_enabled else ensure_portfolio(market)
    if not read_only and execution_enabled:
        portfolio = _accrue_paper_margin_interest(market, portfolio)

    positions = rows(
        """
        SELECT *
        FROM positions
        WHERE market = %s
        """,
        (market,),
    )

    account = build_account(market, portfolio, positions)
    return {
        "cash": account.cash,
        "positions_value": account.positions_value,
        "invested": account.positions_value,
        "equity": account.equity,
        "starting_balance": account.starting_capital,
        "margin_debt": account.margin_debt,
        "margin_interest_accrued": account.margin_interest_accrued,
        "gross_exposure": account.gross_exposure,
        "buying_power": account.buying_power,
        "leverage_limit": account.leverage_limit,
        "leverage_used": account.leverage_used,
        "margin_utilization_pct": account.margin_utilization_pct,
        "maintenance_requirement": account.maintenance_requirement,
        "excess_liquidity": account.excess_liquidity,
        "margin_call": 1.0 if account.margin_call else 0.0,
    }


def portfolio_equity_read_only(market: str) -> dict[str, float]:
    return portfolio_equity(market, read_only=True)


def recent_trade(
    market: str,
    symbol: str,
) -> dict[str, Any] | None:
    cooldown_minutes = max(
        1,
        int(DEFAULT_COOLDOWN_MINUTES * FLEXIBLE_COOLDOWN_FACTOR),
    )

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(minutes=cooldown_minutes)
    ).isoformat()

    try:
        return row(
            """
            SELECT *
            FROM trades
            WHERE market = %s
              AND symbol = %s
              AND created_at >= %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                market,
                symbol,
                cutoff,
            ),
        )
    except Exception:
        return None


# =========================================================
# PRICE UPDATES
# =========================================================

def update_prices(
    market: str,
    prices: dict[str, Any] | None = None,
    *_: Any,
    **__: Any,
) -> int:
    market = safe_text(market).lower()
    prices = prices or {}
    if _execution_disabled("price updates", market, "exit"):
        return 0
    anomalous_symbols = _duplicate_price_anomaly_symbols(prices)

    updated = 0

    positions = rows(
        """
        SELECT *
        FROM positions
        WHERE market = %s
        """,
        (market,),
    )

    closed_for_margin: set[str] = set()
    account, incomplete_margin_symbols, _ = _current_account_from_quotes(market, list(positions), prices, anomalous_symbols)
    if incomplete_margin_symbols and positions:
        log.info(
            "%s | margin reduction deferred: incomplete verified portfolio pricing affected_symbols=%d sample=%s",
            market.upper(),
            len(set(incomplete_margin_symbols)),
            ",".join(sorted(set(incomplete_margin_symbols))[:8]),
        )
    if (
        PAPER_BROKER_MODE
        and positions
        and account is not None
        and (
            bool(account.margin_call)
            or safe_float(account.margin_utilization_pct)
            > PAPER_MAX_MARGIN_UTILIZATION_PCT * 100.0
        )
    ):
        def position_return(item: dict[str, Any]) -> float:
            entry = safe_float(item.get("average_price", item.get("entry_price")))
            symbol = safe_text(item.get("symbol")).upper()
            current = 0.0 if symbol in anomalous_symbols else _verified_price_for(symbol, prices, market)
            return (current - entry) / entry if entry > 0 and current > 0 else -999.0

        for position in sorted(positions, key=position_return):
            symbol = safe_text(position.get("symbol")).upper()
            if symbol in anomalous_symbols:
                continue
            quote = _verified_quote_for(symbol, prices, market)
            current_price = safe_float(quote.get("price")) if quote else 0.0
            if current_price <= 0:
                continue
            if _close_position(market, position, current_price, PAPER_MARGIN_REDUCTION_REASON, quote_metadata=quote):
                closed_for_margin.add(symbol)
            remaining = [p for p in positions if safe_text(p.get("symbol")).upper() not in closed_for_margin]
            account, incomplete_margin_symbols, _ = _current_account_from_quotes(market, list(remaining), prices, anomalous_symbols)
            if account is None:
                log.info(
                    "%s | margin reduction stopped: incomplete verified portfolio pricing affected_symbols=%d sample=%s",
                    market.upper(),
                    len(set(incomplete_margin_symbols)),
                    ",".join(sorted(set(incomplete_margin_symbols))[:8]),
                )
                break
            if (
                not bool(account.margin_call)
                and safe_float(account.margin_utilization_pct)
                < PAPER_MARGIN_WARNING_PCT * 100.0
            ):
                break

    for position in positions:
        symbol = safe_text(position.get("symbol")).upper()
        if symbol in closed_for_margin:
            continue
        if symbol in anomalous_symbols:
            continue
        current_price = _verified_price_for(symbol, prices, market)

        if current_price <= 0:
            continue

        try:
            execute(
                """
                UPDATE positions
                SET current_price = %s,
                    highest_price = GREATEST(
                        COALESCE(highest_price, %s),
                        %s
                    ),
                    updated_at = %s
                WHERE market = %s
                  AND symbol = %s
                """,
                (
                    current_price,
                    current_price,
                    current_price,
                    utc_now(),
                    market,
                    symbol,
                ),
            )
            updated += 1

        except Exception:
            log.exception(
                "Price update failed for %s %s",
                market,
                symbol,
            )

    return updated


# =========================================================
# POSITION CLOSING
# =========================================================

def _close_position(
    market: str,
    position: dict[str, Any],
    price: float,
    reason: str,
    quote_metadata: dict[str, Any] | None = None,
) -> bool:
    market = safe_text(market).lower()
    if _execution_disabled("position close", market, "exit"):
        return False
    symbol = safe_text(position.get("symbol")).upper()
    quantity = safe_float(position.get("quantity"))
    price = safe_float(price)

    if not symbol or quantity <= 0 or price <= 0:
        return False
    if _normalized_symbol(position.get("symbol")) != symbol or not math.isfinite(price):
        return False
    quote_ok, quote_reason = _execution_quote_guard(
        market,
        symbol,
        price,
        {"symbol": symbol},
        position,
        quote_metadata=quote_metadata,
    )
    if not quote_ok:
        log.info("%s | REJECT CLOSE | %s | %s", market.upper(), symbol, quote_reason)
        return False

    return _execute_close_position(market, position, price, reason, quote_metadata=quote_metadata)


def _quote_provider_name(quote_metadata: dict[str, Any] | None) -> str | None:
    if not quote_metadata:
        return None
    provider = quote_metadata.get("provider") or quote_metadata.get("quote_provider") or quote_metadata.get("history_provider")
    return safe_text(provider) or None


def _lot_bucket_from_signal(signal: Any | None, market: str) -> str:
    bucket = signal_value(signal, "bucket", signal_value(signal, "tier", ""))
    if bucket:
        return safe_text(bucket)
    return "Crypto Core" if market == "crypto" else "Tactical"


def _record_buy_attribution(
    conn: Any,
    *,
    market: str,
    symbol: str,
    quantity: float,
    price: float,
    fees: float,
    signal: Any | None,
    quote_metadata: dict[str, Any] | None,
    now: str,
) -> None:
    bucket = _lot_bucket_from_signal(signal, market)
    strategy = safe_text(signal_value(signal, "strategy", signal_value(signal, "scan_type", "")))
    decision_id = signal_value(signal, "signal_id", signal_value(signal, "id", None))
    confidence = safe_float(signal_value(signal, "confidence", None), None) if signal is not None else None
    score = safe_float(signal_value(signal, "score", None), None) if signal is not None else None
    trade_id = f"ledger-buy:{uuid.uuid4()}"
    lot_id = f"lot:{uuid.uuid4()}"
    conn.execute(
        """
        INSERT INTO position_lots (
            lot_id, symbol, market, bucket, strategy, opened_at, quantity_opened,
            quantity_remaining, entry_price, entry_fees, decision_id,
            broker_mode, account_environment, created_at
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PAPER','PAPER',%s)
        """,
        (lot_id, symbol, market, bucket, strategy, now, quantity, quantity, price, fees, decision_id, now),
    )
    conn.execute(
        """
        INSERT INTO trade_ledger (
            trade_id, symbol, market, bucket, strategy, side, quantity, entry_time,
            entry_price, exit_time, exit_price, gross_pnl, fees, net_pnl,
            return_pct, tier, confidence_score, weighted_signal_score,
            quote_provider, decision_id, order_id, broker_mode,
            account_environment, status, created_at, updated_at
        )
        VALUES (%s,%s,%s,%s,%s,'BUY',%s,%s,%s,NULL,NULL,0,%s,%s,0,%s,%s,%s,%s,%s,NULL,'PAPER','PAPER','OPEN',%s,%s)
        """,
        (
            trade_id,
            symbol,
            market,
            bucket,
            strategy,
            quantity,
            now,
            price,
            fees,
            -abs(fees),
            bucket,
            confidence,
            score,
            _quote_provider_name(quote_metadata),
            decision_id,
            now,
            now,
        ),
    )


def _record_sell_attribution(
    conn: Any,
    *,
    market: str,
    position: dict[str, Any],
    price: float,
    quantity: float,
    fees: float,
    reason: str,
    quote_metadata: dict[str, Any] | None,
    now: str,
) -> None:
    symbol = safe_text(position.get("symbol")).upper()
    lot_rows = conn.execute(
        """
        SELECT *
        FROM position_lots
        WHERE market=%s AND symbol=%s AND quantity_remaining > 0
        ORDER BY opened_at ASC, id ASC
        FOR UPDATE
        """,
        (market, symbol),
    ).fetchall()
    if not lot_rows:
        entry_price = safe_float(position.get("average_price", position.get("entry_price", price)), price)
        opened_at = position.get("opened_at") or position.get("updated_at") or now
        synthetic_lot = {
            "lot_id": f"lot:synthetic:{symbol}:{uuid.uuid4()}",
            "symbol": symbol,
            "market": market,
            "bucket": "Historical",
            "strategy": "legacy_position",
            "opened_at": opened_at,
            "quantity_opened": quantity,
            "quantity_remaining": quantity,
            "entry_price": entry_price,
            "entry_fees": 0.0,
            "decision_id": None,
            "broker_mode": "PAPER",
            "account_environment": "PAPER",
        }
        conn.execute(
            """
            INSERT INTO position_lots (
                lot_id, symbol, market, bucket, strategy, opened_at,
                quantity_opened, quantity_remaining, entry_price, entry_fees,
                decision_id, broker_mode, account_environment, created_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,'PAPER','PAPER',%s)
            """,
            (
                synthetic_lot["lot_id"],
                symbol,
                market,
                synthetic_lot["bucket"],
                synthetic_lot["strategy"],
                synthetic_lot["opened_at"],
                quantity,
                quantity,
                entry_price,
                0.0,
                now,
            ),
        )
        lot_rows = [synthetic_lot]

    from profit_attribution import PositionLot

    lots = [
        PositionLot(
            lot_id=safe_text(lot.get("lot_id")),
            symbol=safe_text(lot.get("symbol")).upper(),
            market=safe_text(lot.get("market")).lower(),
            bucket=safe_text(lot.get("bucket"), "Tactical"),
            strategy=safe_text(lot.get("strategy")),
            opened_at=_parse_utc(lot.get("opened_at")) or datetime.now(timezone.utc),
            quantity_opened=safe_float(lot.get("quantity_opened")),
            quantity_remaining=safe_float(lot.get("quantity_remaining")),
            entry_price=safe_float(lot.get("entry_price")),
            entry_fees=safe_float(lot.get("entry_fees")),
            decision_id=lot.get("decision_id"),
            broker_mode=safe_text(lot.get("broker_mode"), "PAPER"),
            account_environment=safe_text(lot.get("account_environment"), "PAPER"),
        )
        for lot in lot_rows
    ]
    ledger_rows = fifo_close_lots(
        lots,
        quantity=quantity,
        exit_price=price,
        exit_time=now,
        fees=fees,
        tier=safe_text(position.get("tier") or "paper_exit"),
        quote_provider=_quote_provider_name(quote_metadata),
        order_id=safe_text(reason),
    )
    for lot in lots:
        conn.execute(
            """
            UPDATE position_lots
            SET quantity_remaining=%s
            WHERE lot_id=%s
            """,
            (lot.quantity_remaining, lot.lot_id),
        )
    for ledger in ledger_rows:
        data = ledger.to_dict()
        conn.execute(
            """
            INSERT INTO trade_ledger (
                trade_id, symbol, market, bucket, strategy, side, quantity,
                entry_time, entry_price, exit_time, exit_price, gross_pnl,
                fees, net_pnl, return_pct, tier, confidence_score,
                weighted_signal_score, quote_provider, decision_id, order_id,
                broker_mode, account_environment, status, created_at, updated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                data["trade_id"],
                data["symbol"],
                data["market"],
                data["bucket"],
                data["strategy"],
                data["side"],
                data["quantity"],
                data["entry_time"],
                data["entry_price"],
                data["exit_time"],
                data["exit_price"],
                data["gross_pnl"],
                data["fees"],
                data["net_pnl"],
                data["return_pct"],
                data["tier"],
                data["confidence_score"],
                data["weighted_signal_score"],
                data["quote_provider"],
                data["decision_id"],
                data["order_id"],
                data["broker_mode"],
                data["account_environment"],
                data["status"],
                now,
                now,
            ),
        )


def _execute_close_position(
    market: str,
    position: dict[str, Any],
    price: float,
    reason: str,
    quote_metadata: dict[str, Any] | None = None,
) -> bool:
    market = safe_text(market).lower()
    symbol = safe_text(position.get("symbol")).upper()
    quantity = safe_float(position.get("quantity"))
    price = safe_float(price)

    value = quantity * price

    entry_price = safe_float(
        position.get(
            "average_price",
            position.get("entry_price", price),
        ),
        price,
    )

    realized_pnl = (price - entry_price) * quantity
    now = utc_now()

    try:
        with connect() as conn:
            quote_metadata = quote_metadata or {}
            claimed, execution_key, claim_reason = _try_execution_claim(
                conn,
                market=market,
                symbol=symbol,
                side="SELL",
                price=price,
                quote=quote_metadata,
                position=position,
            )
            if not claimed:
                log.info("%s | REJECT SELL | %s | %s", market.upper(), symbol, claim_reason)
                return False
            portfolio = conn.execute(
                """
                SELECT *
                FROM portfolios
                WHERE market = %s
                FOR UPDATE
                """,
                (market,),
            ).fetchone()

            if not portfolio:
                return False
            account = build_account(market, portfolio, [position])
            risk_intent = (
                "forced_risk_reduction"
                if reason in {PAPER_MARGIN_REDUCTION_REASON, "stop_loss", "take_profit", "trailing_stop"}
                else "exit"
            )
            risk_ok, risk_reason, _ = _shared_risk_gate(
                conn=conn,
                market=market,
                symbol=symbol,
                side="SELL",
                intent=risk_intent,
                order_value=value,
                portfolio=portfolio,
                quote=quote_metadata or {},
                positions=[position],
                concentration_pct=0.0,
            )
            if not risk_ok:
                log.info("%s | REJECT SELL | %s | shared risk: %s", market.upper(), symbol, risk_reason)
                return False

            new_cash, new_margin_debt, margin_repayment = allocate_sale(
                cash=safe_float(portfolio.get("cash")),
                margin_debt=safe_float(portfolio.get("margin_debt")),
                sale_value=value,
            )

            conn.execute(
                """
                UPDATE portfolios
                SET cash = %s,
                    margin_debt = %s,
                    updated_at = %s
                WHERE market = %s
                """,
                (
                    new_cash,
                    new_margin_debt,
                    now,
                    market,
                ),
            )

            conn.execute(
                """
                DELETE FROM positions
                WHERE market = %s
                  AND symbol = %s
                """,
                (
                    market,
                    symbol,
                ),
            )

            conn.execute(
                """
                INSERT INTO trades (
                    market,
                    symbol,
                    side,
                    quantity,
                    price,
                    value,
                    realized_pnl,
                    score,
                    reason,
                    created_at
                )
                VALUES (
                    %s, %s, 'SELL', %s, %s, %s,
                    %s, NULL, %s, %s
                )
                """,
                (
                    market,
                    symbol,
                    quantity,
                    price,
                    value,
                    realized_pnl,
                    reason,
                    now,
                ),
            )
            _record_sell_attribution(
                conn,
                market=market,
                position=position,
                price=price,
                quantity=quantity,
                fees=0.0,
                reason=reason,
                quote_metadata=quote_metadata,
                now=now,
            )
            _complete_execution_claim(conn, execution_key)

        record_closed_trade_memory(
            market=market,
            symbol=symbol,
            position=position,
            exit_price=price,
            pnl=realized_pnl,
            exit_reason=reason,
            quantity=quantity,
        )
        log.info(
            "%s SELL %s quantity=%.8f price=%.6f margin_repaid=%.2f reason=%s",
            market.upper(),
            symbol,
            quantity,
            price,
            margin_repayment,
            reason,
        )
        return True

    except Exception:
        log.exception(
            "Could not close %s %s",
            market,
            symbol,
        )
        return False


# =========================================================
# RISK EXITS
# =========================================================

def risk_exits(
    market: str,
    prices: dict[str, Any] | None = None,
    *_: Any,
    **__: Any,
) -> list[dict[str, Any]]:
    market = safe_text(market).lower()
    prices = prices or {}
    if _execution_disabled("risk exits", market, "exit"):
        return []
    anomalous_symbols = _duplicate_price_anomaly_symbols(prices)

    actions: list[dict[str, Any]] = []

    positions = rows(
        """
        SELECT *
        FROM positions
        WHERE market = %s
        """,
        (market,),
    )

    for position in positions:
        symbol = safe_text(position.get("symbol")).upper()
        if symbol in anomalous_symbols:
            continue

        entry_price = safe_float(
            position.get(
                "average_price",
                position.get("entry_price", 0.0),
            )
        )

        quote = _verified_quote_for(symbol, prices, market)
        if quote is None:
            continue
        current_price = safe_float(quote.get("price"))

        highest_price = safe_float(
            position.get("highest_price"),
            max(entry_price, current_price),
        )

        if entry_price <= 0 or current_price <= 0:
            continue

        change_pct = (
            current_price - entry_price
        ) / entry_price

        trailing_change = (
            (current_price - highest_price) / highest_price
            if highest_price > 0
            else 0.0
        )

        reason: str | None = None

        if change_pct <= -DEFAULT_STOP_LOSS_PCT:
            reason = "stop_loss"
        elif change_pct >= DEFAULT_TAKE_PROFIT_PCT:
            reason = "take_profit"
        elif (
            highest_price > entry_price
            and trailing_change <= -DEFAULT_TRAILING_STOP_PCT
        ):
            reason = "trailing_stop"

        if not reason:
            continue

        if _close_position(market, position, current_price, reason, quote_metadata=quote):
            actions.append(
                {
                    "market": market,
                    "symbol": symbol,
                    "action": "SELL",
                    "price": current_price,
                    "reason": reason,
                    "return_pct": change_pct,
                }
            )

    return actions


# =========================================================
# POSITION COUNT
# =========================================================

def _open_position_count(
    market: str,
) -> int:
    try:
        result = row(
            """
            SELECT COUNT(*) AS total
            FROM positions
            WHERE market = %s
            """,
            (market,),
        )

        return int(safe_float(result.get("total"))) if result else 0

    except Exception:
        return 0


def _latest_opportunity_score(market: str, symbol: str) -> float:
    try:
        record = row(
            """
            SELECT opportunity_score
            FROM opportunity_rankings
            WHERE market=%s AND symbol=%s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (market, symbol),
        ) or {}
        return safe_float(record.get("opportunity_score"), 50.0)
    except Exception:
        return 50.0


def _is_penny_stock(symbol: str, price: float, signal: Any) -> bool:
    exchange = signal_value(signal, "exchange", "")
    return (
        PENNY_STOCK_MIN_PRICE <= price <= PENNY_STOCK_MAX_PRICE
        or is_otc_exchange(exchange)
        or bool(signal_value(signal, "penny_stock", False))
    )


def _penny_position_count(market: str) -> int:
    try:
        positions = rows("SELECT symbol,current_price,average_price,entry_price FROM positions WHERE market=%s", (market,))
    except Exception:
        return 0
    total = 0
    for position in positions:
        price = safe_float(position.get("current_price"), safe_float(position.get("average_price"), safe_float(position.get("entry_price"))))
        if PENNY_STOCK_MIN_PRICE <= price <= PENNY_STOCK_MAX_PRICE:
            total += 1
    return total


def _decode_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [safe_text(item) for item in value if safe_text(item)]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [safe_text(item) for item in parsed if safe_text(item)]
        except Exception:
            pass
        return [part.strip() for part in text.replace("|", ",").split(",") if part.strip()]
    return []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return safe_text(value).lower() in {"true", "1", "yes", "y"}


def _latest_verified_candidate_metadata(symbol: str) -> dict[str, Any] | None:
    try:
        record = row(
            """
            SELECT symbol, exchange, price, daily_volume, relative_volume, avg_dollar_volume,
                   primary_category, mover_tags, discovery_source,
                   discovery_timestamp, quote_timestamp, data_freshness_seconds,
                   risk_bucket, tradeable, scanned_at, payload
            FROM global_market_candidates
            WHERE symbol=%s
            ORDER BY scanned_at DESC
            LIMIT 1
            """,
            (symbol,),
        )
    except Exception:
        return None
    if not record:
        return None
    metadata = dict(record)
    payload = metadata.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            metadata.setdefault(key, value)
    metadata["mover_tags"] = _decode_tags(metadata.get("mover_tags"))
    return metadata


def _verified_signal_metadata(symbol: str, signal: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in (
        "exchange",
        "daily_volume",
        "volume",
        "avg_dollar_volume",
        "average_dollar_volume",
        "primary_category",
        "mover_tags",
        "discovery_source",
        "discovery_timestamp",
        "quote_timestamp",
        "created_at",
        "timestamp",
        "scanned_at",
        "tradeable",
    ):
        value = signal_value(signal, key, None)
        if value not in (None, "", []):
            metadata[key] = value
    candidate = _latest_verified_candidate_metadata(symbol)
    if candidate:
        metadata.update({key: value for key, value in candidate.items() if value not in (None, "", [])})
    metadata["mover_tags"] = _decode_tags(metadata.get("mover_tags"))
    return metadata


def _penny_portfolio_exposure_after(
    positions: list[dict[str, Any]],
    equity: float,
    proposed_trade_value: float,
) -> tuple[float, float]:
    existing_value = 0.0
    for position in positions:
        current = safe_float(
            position.get(
                "current_price",
                position.get("average_price", position.get("entry_price")),
            )
        )
        if PENNY_STOCK_MIN_PRICE <= current <= PENNY_STOCK_MAX_PRICE:
            existing_value += safe_float(position.get("quantity")) * current
    total_value = max(0.0, existing_value) + max(0.0, proposed_trade_value)
    pct = total_value / equity if equity > 0 else 1.0
    return total_value, pct


def _penny_stock_gate(market: str, symbol: str, price: float, signal: Any, score: float, confidence: float) -> tuple[bool, str]:
    if not _is_penny_stock(symbol, price, signal):
        return True, "not a penny stock"
    if not PENNY_STOCK_ENABLED:
        return False, "penny-stock entries disabled"
    metadata = _verified_signal_metadata(symbol, signal)
    exchange = normalize_exchange(metadata.get("exchange", ""))
    if is_otc_exchange(exchange) and not OTC_STOCKS_ENABLED:
        return False, "OTC penny stocks disabled"
    if not exchange:
        return False, "penny-stock verified exchange metadata is missing"
    if not confirmed_us_listing(exchange):
        return False, f"penny-stock exchange is not a confirmed listed venue ({exchange})"
    if price < PENNY_STOCK_MIN_PRICE or price > PENNY_STOCK_MAX_PRICE:
        return False, "penny-stock price is outside allowed bounds"
    if score < PENNY_STOCK_MIN_SCORE:
        return False, f"penny-stock score {score:.1f} below {PENNY_STOCK_MIN_SCORE:.1f}"
    if confidence < PENNY_STOCK_MIN_CONFIDENCE:
        return False, f"penny-stock confidence {confidence:.2f} below {PENNY_STOCK_MIN_CONFIDENCE:.2f}"
    scanned_at = _parse_utc(metadata.get("scanned_at"))
    if scanned_at is None:
        return False, "penny-stock verified candidate timestamp is missing"
    candidate_age = max(0.0, (datetime.now(timezone.utc) - scanned_at).total_seconds())
    if candidate_age > GLOBAL_CANDIDATE_TTL_SECONDS:
        return False, "penny-stock verified candidate is expired"
    if not _truthy(metadata.get("tradeable")):
        return False, "penny-stock verified candidate is not tradeable"
    quote_timestamp = metadata.get("quote_timestamp")
    if _parse_utc(quote_timestamp) is None:
        return False, "penny-stock verified quote timestamp is missing"
    if not quote_is_fresh(
        quote_timestamp,
        "1d",
        max_intraday_age_seconds=DECISION_STOCK_MAX_AGE_MINUTES * 60,
        exchange=exchange,
        symbol=symbol,
    ):
        return False, "penny-stock verified market data is stale"
    primary_category = safe_text(metadata.get("primary_category")).lower()
    if primary_category and primary_category != "penny_stock":
        return False, f"penny-stock metadata category is inconsistent ({primary_category})"
    if not safe_text(metadata.get("discovery_source")):
        return False, "penny-stock discovery source metadata is missing"
    volume = safe_float(metadata.get("daily_volume", metadata.get("volume", 0.0)))
    avg_dollar_volume = safe_float(metadata.get("avg_dollar_volume", metadata.get("average_dollar_volume", 0.0)))
    if volume < PENNY_STOCK_MIN_DAILY_VOLUME:
        return False, "penny-stock daily volume is insufficient"
    if avg_dollar_volume < PENNY_STOCK_MIN_AVG_DOLLAR_VOLUME:
        return False, "penny-stock dollar volume is insufficient"
    if _penny_position_count(market) >= PENNY_STOCK_MAX_OPEN_POSITIONS:
        return False, "penny-stock position limit reached"
    return True, "penny-stock controls passed"


def _rotate_for_stronger_candidate(
    market: str,
    incoming_symbol: str,
    incoming_score: float,
    quotes: dict[str, Any] | None = None,
    anomalous_symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    """Select a rotation candidate without closing it.

    The actual continuous_rotation_to_* sell is executed in the same locked
    transaction as the replacement BUY, after the incoming order passes
    cooldown, sizing, cash, leverage, quant, and risk checks.
    """
    if not ROTATION_ENABLED:
        return None
    if _execution_disabled("portfolio rotation", market, "rotation"):
        return None
    quotes = quotes or {}
    anomalous_symbols = anomalous_symbols or set()
    positions = rows(
        """
        SELECT * FROM positions
        WHERE market=%s AND symbol<>%s
        """,
        (market, incoming_symbol),
    )
    if not positions:
        return None

    ranked: list[tuple[float, float, dict[str, Any]]] = []
    for position in positions:
        symbol = safe_text(position.get("symbol")).upper()
        if symbol in anomalous_symbols:
            continue
        quote = _verified_quote_for(symbol, quotes, market)
        current = safe_float(quote.get("price")) if quote else 0.0
        if current <= 0:
            continue
        entry = safe_float(position.get("average_price"), safe_float(position.get("entry_price")))
        return_pct = ((current / entry) - 1.0) * 100.0 if entry > 0 and current > 0 else -999.0
        held_score = _latest_opportunity_score(market, symbol)
        candidate_position = dict(position)
        candidate_position["_verified_rotation_quote"] = quote
        ranked.append((held_score, return_pct, candidate_position))
    if not ranked:
        return None

    held_score, return_pct, weakest = min(ranked, key=lambda item: (item[0], item[1]))
    score_gap = incoming_score - held_score
    if score_gap < ROTATION_MIN_SCORE_GAP:
        return None

    weakest_symbol = safe_text(weakest.get("symbol")).upper()
    outgoing_quote = _quote_payload(weakest.get("_verified_rotation_quote"), weakest_symbol)
    exit_price = safe_float(outgoing_quote.get("price"))
    if exit_price <= 0:
        return None
    candidate = dict(weakest)
    candidate["_verified_rotation_quote"] = outgoing_quote
    candidate["_rotation_action"] = {
        "market": market,
        "symbol": weakest_symbol,
        "action": "SELL",
        "price": exit_price,
        "reason": f"continuous_rotation_to_{incoming_symbol}",
        "rotation_target": incoming_symbol,
        "score_gap": score_gap,
        "return_pct": return_pct,
    }
    return candidate


# =========================================================
# BUY EXECUTION
# =========================================================

def _buy(
    market: str,
    symbol: str,
    price: float,
    signal: Any,
    quant_assessment: Any | None = None,
    target_trade_value: float | None = None,
    rotation_candidate: dict[str, Any] | None = None,
    verified_quote: dict[str, Any] | None = None,
    rotation_verified_quote: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Execute an institutional paper-broker buy with controlled leverage."""
    market = safe_text(market).lower()
    if _execution_disabled("buy", market, "entry"):
        return False, "autotrade disabled", None
    if rotation_candidate and _execution_disabled("portfolio rotation", market, "rotation"):
        return False, "portfolio rotation disabled", None
    symbol = safe_text(symbol).upper()
    price = safe_float(price)
    rotation_action = (
        dict(rotation_candidate.get("_rotation_action"))
        if rotation_candidate and isinstance(rotation_candidate.get("_rotation_action"), dict)
        else None
    )

    if not market:
        return False, "missing market", None
    if not symbol:
        return False, "missing symbol", None
    if price <= 0:
        return False, f"invalid price={price}", None
    quote_ok, quote_reason = _execution_quote_guard(market, symbol, price, signal, quote_metadata=verified_quote)
    if not quote_ok:
        return False, quote_reason, None

    equity_data = portfolio_equity(market)
    equity = max(safe_float(equity_data.get("equity"), market_starting_capital(market)), 0.01)
    buying_power = max(0.0, safe_float(equity_data.get("buying_power")))
    gross_exposure = max(0.0, safe_float(equity_data.get("gross_exposure")))
    leverage_limit = max(1.0, safe_float(equity_data.get("leverage_limit"), market_leverage_limit(market)))
    maximum_gross = equity * leverage_limit * PAPER_MAX_MARGIN_UTILIZATION_PCT
    risk_limited_buying_power = max(0.0, maximum_gross - gross_exposure)
    available_buying_power = min(buying_power, risk_limited_buying_power)

    if available_buying_power < MIN_TRADE_VALUE and not rotation_candidate:
        return (
            False,
            f"insufficient paper buying power={available_buying_power:.2f}; "
            f"leverage={safe_float(equity_data.get('leverage_used')):.2f}x/{leverage_limit:.2f}x",
            None,
        )

    sizing_buying_power = (
        available_buying_power
        if not rotation_candidate
        else equity * leverage_limit * PAPER_MAX_MARGIN_UTILIZATION_PCT
    )
    max_trade_pct = MAX_TRADE_VALUE_PCT
    if PENNY_STOCK_MIN_PRICE <= price <= PENNY_STOCK_MAX_PRICE:
        max_trade_pct = min(max_trade_pct, PENNY_STOCK_MAX_TRADE_VALUE_PCT)

    maximum_trade_value = min(
        sizing_buying_power,
        equity * max_trade_pct,
    )

    # Rich paper capital must still respect realistic liquidity. When the global
    # scanner supplies average dollar volume, the simulated order is capped to a
    # small percentage of that market's normal daily turnover.
    average_dollar_volume = max(
        0.0,
        safe_float(
            signal_value(
                signal,
                "avg_dollar_volume",
                signal_value(signal, "average_dollar_volume", 0.0),
            )
        ),
    )
    if average_dollar_volume > 0:
        maximum_trade_value = min(
            maximum_trade_value,
            average_dollar_volume * PAPER_MAX_MARKET_PARTICIPATION_PCT,
        )

    confidence = normalized_confidence(signal)
    score = normalized_score(signal)
    strength = max(0.55, min(1.0, max(confidence, score / 100.0)))
    quant_multiplier = (
        safe_float(getattr(quant_assessment, "position_multiplier", 1.0), 1.0)
        if quant_assessment is not None
        else 1.0
    )

    trade_value = min(
        maximum_trade_value * strength * quant_multiplier,
        sizing_buying_power,
    )
    if target_trade_value is not None:
        target_value = max(0.0, safe_float(target_trade_value))
        if target_value > 0:
            trade_value = min(trade_value, target_value)

    if trade_value < MIN_TRADE_VALUE:
        return False, f"trade value too small={trade_value:.2f}; minimum={MIN_TRADE_VALUE:.2f}", None

    now = utc_now()
    closed_memory: tuple[dict[str, Any], float, float, float, str] | None = None

    try:
        with connect() as conn:
            portfolio_record = conn.execute(
                "SELECT * FROM portfolios WHERE market=%s FOR UPDATE",
                (market,),
            ).fetchone()
            if not portfolio_record:
                return False, "portfolio row missing", None
            claimed, execution_key, claim_reason = _try_execution_claim(
                conn,
                market=market,
                symbol=symbol,
                side="BUY",
                price=price,
                quote=verified_quote or {},
                signal=signal,
            )
            if not claimed:
                return False, claim_reason, None

            current_positions = conn.execute(
                "SELECT * FROM positions WHERE market=%s FOR UPDATE",
                (market,),
            ).fetchall()
            active_positions = list(current_positions)
            execution_portfolio = dict(portfolio_record)
            margin_repayment = 0.0
            if rotation_candidate:
                rotation_symbol = safe_text(rotation_candidate.get("symbol")).upper()
                outgoing_quote = _quote_payload(
                    rotation_verified_quote or rotation_candidate.get("_verified_rotation_quote"),
                    rotation_symbol,
                )
                locked_rotation = next(
                    (p for p in active_positions if safe_text(p.get("symbol")).upper() == rotation_symbol),
                    None,
                )
                if locked_rotation is None:
                    return False, "rotation candidate is no longer open", None
                outgoing_ok, outgoing_reason = _execution_quote_guard(
                    market,
                    rotation_symbol,
                    safe_float(outgoing_quote.get("price")),
                    {"symbol": rotation_symbol},
                    locked_rotation,
                    quote_metadata=outgoing_quote,
                )
                if not outgoing_ok:
                    return False, f"rotation outgoing quote rejected: {outgoing_reason}", None
                incoming_ok, incoming_reason = _execution_quote_guard(
                    market,
                    symbol,
                    price,
                    signal,
                    quote_metadata=verified_quote,
                )
                if not incoming_ok:
                    return False, f"rotation incoming quote rejected: {incoming_reason}", None
                exit_price = safe_float(outgoing_quote.get("price"))
                quantity_to_sell = safe_float(locked_rotation.get("quantity"))
                if exit_price <= 0 or quantity_to_sell <= 0:
                    return False, "rotation candidate has invalid exit data", None
                rotation_claimed, rotation_execution_key, rotation_claim_reason = _try_execution_claim(
                    conn,
                    market=market,
                    symbol=rotation_symbol,
                    side="SELL",
                    price=exit_price,
                    quote=outgoing_quote,
                    signal={"symbol": rotation_symbol},
                    position=locked_rotation,
                )
                if not rotation_claimed:
                    return False, f"rotation outgoing duplicate rejected: {rotation_claim_reason}", None
                sale_value = quantity_to_sell * exit_price
                outgoing_risk_ok, outgoing_risk_reason, _ = _shared_risk_gate(
                    conn=conn,
                    market=market,
                    symbol=rotation_symbol,
                    side="SELL",
                    intent="rotation_out",
                    order_value=sale_value,
                    portfolio=execution_portfolio,
                    quote=outgoing_quote,
                    positions=active_positions,
                    concentration_pct=0.0,
                )
                if not outgoing_risk_ok:
                    return False, f"rotation outgoing risk rejected: {outgoing_risk_reason}", None
                new_cash_after_sale, new_debt_after_sale, margin_repayment = allocate_sale(
                    cash=safe_float(execution_portfolio.get("cash")),
                    margin_debt=safe_float(execution_portfolio.get("margin_debt")),
                    sale_value=sale_value,
                )
                execution_portfolio["cash"] = new_cash_after_sale
                execution_portfolio["margin_debt"] = new_debt_after_sale
                active_positions = [
                    p for p in active_positions
                    if safe_text(p.get("symbol")).upper() != rotation_symbol
                ]

            account = build_account(market, execution_portfolio, active_positions)
            current_maximum_gross = (
                account.equity
                * account.leverage_limit
                * PAPER_MAX_MARGIN_UTILIZATION_PCT
            )
            current_available = min(
                account.buying_power,
                max(0.0, current_maximum_gross - account.gross_exposure),
            )
            trade_value = min(trade_value, current_available)
            if trade_value < MIN_TRADE_VALUE:
                return False, f"buying power changed during execution; available={current_available:.2f}", None
            existing_position_value = 0.0
            for active in active_positions:
                if safe_text(active.get("symbol")).upper() == symbol:
                    existing_position_value += safe_float(active.get("quantity")) * safe_float(
                        active.get("current_price", active.get("entry_price", price))
                    )
            stop_price = safe_float(
                signal_value(
                    signal,
                    "stop_price",
                    signal_value(signal, "stop_loss", signal_value(signal, "stop", price * (1.0 - DEFAULT_STOP_LOSS_PCT))),
                )
            )
            tier = safe_text(signal_value(signal, "tier", signal_value(signal, "risk_tier", "B")), "B").upper()
            if tier not in {"A", "B", "C"}:
                tier = "B"
            regime = safe_text(
                signal_value(
                    signal,
                    "market_regime",
                    signal_value(signal, "crypto_regime", signal_value(signal, "regime", "neutral")),
                ),
                "neutral",
            )
            allocator_volume = average_dollar_volume or safe_float(signal_value(signal, "dollar_volume_24h", 0.0))
            allocation = adaptive_capital_allocation(
                symbol=symbol,
                market=market,
                equity=account.equity,
                cash=account.cash,
                current_exposure=account.gross_exposure,
                price=price,
                stop_price=stop_price,
                tier=tier,
                confidence=confidence,
                reward_risk=safe_float(signal_value(signal, "reward_risk_ratio", 1.5), 1.5),
                market_regime=regime,
                dollar_volume=allocator_volume,
                spread_pct=safe_float(signal_value(signal, "spread_pct", 0.0)),
                drawdown_pct=safe_float(equity_data.get("drawdown_pct", 0.0)),
                existing_position_value=existing_position_value,
                buying_power=account.buying_power,
                buying_power_validated=True,
            )
            if not allocation.approved:
                return False, f"adaptive capital sizing rejected: {allocation.reason}", None
            trade_value = min(trade_value, allocation.calculated_notional)
            if trade_value < MIN_TRADE_NOTIONAL:
                return False, f"trade value too small={trade_value:.2f}; minimum={MIN_TRADE_NOTIONAL:.2f}", None
            safeguard_ok, safeguard_reason = _paper_buy_safeguard(
                market=market,
                symbol=symbol,
                trade_value=trade_value,
                account=account,
                positions=[dict(position) for position in active_positions],
                quote=verified_quote or {},
            )
            if not safeguard_ok:
                return False, safeguard_reason, None

            if PENNY_STOCK_MIN_PRICE <= price <= PENNY_STOCK_MAX_PRICE:
                _, penny_pct = _penny_portfolio_exposure_after(
                    [dict(position) for position in active_positions],
                    account.equity,
                    trade_value,
                )
                if penny_pct > PENNY_STOCK_MAX_PORTFOLIO_PCT:
                    return (
                        False,
                        (
                            f"penny-stock portfolio limit exceeded "
                            f"({penny_pct:.2%}/{PENNY_STOCK_MAX_PORTFOLIO_PCT:.2%})"
                        ),
                        None,
                    )

            risk_ok, risk_reason, _ = _shared_risk_gate(
                conn=conn,
                market=market,
                symbol=symbol,
                side="BUY",
                intent="rotation_in" if rotation_candidate else "entry",
                order_value=trade_value,
                portfolio=execution_portfolio,
                quote=verified_quote or {},
                positions=active_positions,
                concentration_pct=None,
            )
            if not risk_ok:
                return False, f"shared risk rejected: {risk_reason}", None

            cash_reserve = max(0.0, account.equity * MIN_CASH_RESERVE_PCT)
            new_cash, new_margin_debt, cash_used, borrowed = allocate_purchase(
                cash=account.cash,
                margin_debt=account.margin_debt,
                trade_value=trade_value,
                cash_reserve=cash_reserve,
            )
            quantity = trade_value / price
            if quantity <= 0:
                return False, f"invalid quantity={quantity}", None

            existing = next(
                (p for p in active_positions if safe_text(p.get("symbol")).upper() == symbol),
                None,
            )
            if rotation_candidate and rotation_action:
                rotation_symbol = safe_text(rotation_action.get("symbol")).upper()
                locked_rotation = next(
                    (p for p in current_positions if safe_text(p.get("symbol")).upper() == rotation_symbol),
                    None,
                )
                if locked_rotation is None:
                    return False, "rotation candidate disappeared before execution", None
                entry_price = safe_float(
                    locked_rotation.get(
                        "average_price",
                        locked_rotation.get("entry_price", exit_price),
                    ),
                    exit_price,
                )
                realized_pnl = (exit_price - entry_price) * quantity_to_sell
                conn.execute(
                    """
                    DELETE FROM positions
                    WHERE market = %s
                      AND symbol = %s
                    """,
                    (market, rotation_symbol),
                )
                conn.execute(
                    """
                    INSERT INTO trades(
                        market,symbol,side,quantity,price,value,realized_pnl,score,reason,created_at
                    ) VALUES (%s,%s,'SELL',%s,%s,%s,%s,NULL,%s,%s)
                    """,
                    (
                        market,
                        rotation_symbol,
                        quantity_to_sell,
                        exit_price,
                        quantity_to_sell * exit_price,
                        realized_pnl,
                        rotation_action.get("reason", f"continuous_rotation_to_{symbol}"),
                        now,
                    ),
                )
                _record_sell_attribution(
                    conn,
                    market=market,
                    position=dict(locked_rotation),
                    price=exit_price,
                    quantity=quantity_to_sell,
                    fees=0.0,
                    reason=safe_text(rotation_action.get("reason"), f"continuous_rotation_to_{symbol}"),
                    quote_metadata=outgoing_quote,
                    now=now,
                )
                _complete_execution_claim(conn, rotation_execution_key)
                closed_memory = (
                    dict(locked_rotation),
                    exit_price,
                    realized_pnl,
                    quantity_to_sell,
                    safe_text(rotation_action.get("reason"), f"continuous_rotation_to_{symbol}"),
                )
            if existing:
                old_quantity = safe_float(existing.get("quantity"))
                old_average = safe_float(
                    existing.get("average_price", existing.get("entry_price", price)),
                    price,
                )
                combined_quantity = old_quantity + quantity
                if combined_quantity <= 0:
                    return False, "combined position quantity is invalid", None
                combined_average = (old_quantity * old_average + quantity * price) / combined_quantity
                old_highest = safe_float(existing.get("highest_price"), price)
                conn.execute(
                    """
                    UPDATE positions
                    SET quantity=%s, entry_price=%s, average_price=%s,
                        current_price=%s, highest_price=%s, updated_at=%s
                    WHERE market=%s AND symbol=%s
                    """,
                    (
                        combined_quantity, combined_average, combined_average, price,
                        max(old_highest, price), now, market, symbol,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO positions(
                        market,symbol,quantity,entry_price,average_price,current_price,
                        highest_price,opened_at,updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (market, symbol, quantity, price, price, price, price, now, now),
                )

            conn.execute(
                """
                UPDATE portfolios
                SET cash=%s, margin_debt=%s, updated_at=%s
                WHERE market=%s
                """,
                (new_cash, new_margin_debt, now, market),
            )

            reason_text = (
                f"Institutional paper buy; score={score:.2f}; confidence={confidence:.2f}; "
                f"cash_used=${cash_used:,.2f}; margin_used=${borrowed:,.2f}; "
                f"{getattr(quant_assessment, 'reason', 'legacy standard')}"
            )
            conn.execute(
                """
                INSERT INTO trades(
                    market,symbol,side,quantity,price,value,realized_pnl,score,reason,created_at
                ) VALUES (%s,%s,'BUY',%s,%s,%s,0,%s,%s,%s)
                """,
                (market, symbol, quantity, price, trade_value, score, reason_text, now),
            )
            _record_buy_attribution(
                conn,
                market=market,
                symbol=symbol,
                quantity=quantity,
                price=price,
                fees=0.0,
                signal=signal,
                quote_metadata=verified_quote,
                now=now,
            )
            _complete_execution_claim(conn, execution_key)

        log.info(
            "%s | INSTITUTIONAL PAPER BUY | %s | quantity=%.8f | price=%.6f | "
            "value=%.2f | borrowed=%.2f | score=%.2f | confidence=%.3f",
            market.upper(), symbol, quantity, price, trade_value, borrowed, score, confidence,
        )
        if closed_memory is not None:
            closed_position, exit_price, realized_pnl, closed_quantity, exit_reason = closed_memory
            record_closed_trade_memory(
                market=market,
                symbol=safe_text(closed_position.get("symbol")).upper(),
                position=closed_position,
                exit_price=exit_price,
                pnl=realized_pnl,
                exit_reason=exit_reason,
                quantity=closed_quantity,
            )
            log.info(
                "%s | CONTINUOUS ROTATION | sold=%s incoming=%s margin_repaid=%.2f",
                market.upper(),
                safe_text(closed_position.get("symbol")).upper(),
                symbol,
                margin_repayment,
            )
        return True, (
            f"paper buy executed: ${trade_value:,.2f}; "
            f"cash ${cash_used:,.2f}; margin ${borrowed:,.2f}"
        ), rotation_action

    except Exception as exc:
        log.exception("%s | BUY FAILED | %s", market.upper(), symbol)
        return False, f"database execution error: {exc}", None


# =========================================================
# SIGNAL PROCESSING
# =========================================================

def process_signals(
    market: str,
    signals: list[Any] | tuple[Any, ...] | None,
    prices: dict[str, Any] | None = None,
    *_: Any,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    market = safe_text(market).lower()
    signals = list(signals or [])
    prices = prices or {}
    optimizer_allocations = kwargs.get("optimizer_allocations") or {}
    actions: list[dict[str, Any]] = []
    anomalous_symbols = _duplicate_price_anomaly_symbols(prices)

    maximum_positions = (
        DEFAULT_MAX_OPEN_POSITIONS
        + EXTRA_OPEN_POSITIONS
    )

    log.info(
        "%s | Processing %d signals | max_positions=%d | "
        "score_threshold=%.1f | confidence_threshold=%.2f",
        market.upper(),
        len(signals),
        maximum_positions,
        HIGH_SCORE_THRESHOLD,
        HIGH_CONFIDENCE_THRESHOLD,
    )

    for signal in signals:
        symbol = safe_text(signal_value(signal, "symbol", "")).upper()
        if not symbol:
            log.info("%s | REJECT | missing symbol", market.upper())
            continue

        action = signal_action(signal)
        score = normalized_score(signal)
        confidence = normalized_confidence(signal)
        quote = _verified_quote_for(symbol, prices, market)
        if quote is None:
            price = 0.0
            quote_rejection_reason = _quote_rejection_reason(symbol, prices, market)
        else:
            quote_rejection_reason = ""
            price = safe_float(quote.get("price"))
            signal_price_value = signal_price(signal, price)
            if not _quote_price_matches(signal_price_value, price):
                log.info(
                    "%s | REJECT | %s | signal price %.6f differs from verified quote %.6f",
                    market.upper(),
                    symbol,
                    signal_price_value,
                    price,
                )
                continue

        log.info(
            "%s | CANDIDATE | %s | action=%s | score=%.2f | "
            "confidence=%.3f | price=%.6f",
            market.upper(),
            symbol,
            action,
            score,
            confidence,
            price,
        )

        if price <= 0:
            log.info(
                "%s | REJECT | %s | %s",
                market.upper(),
                symbol,
                quote_rejection_reason or "invalid/missing price",
            )
            continue
        if symbol in anomalous_symbols:
            log.info(
                "%s | REJECT | %s | duplicate-price anomaly quarantine",
                market.upper(),
                symbol,
            )
            continue

        if action in {"SELL", "EXIT", "CLOSE"}:
            if _execution_disabled("sell signal", market, "exit"):
                continue
            position = row(
                """
                SELECT *
                FROM positions
                WHERE market = %s
                  AND symbol = %s
                """,
                (market, symbol),
            )

            if not position:
                log.info(
                    "%s | REJECT SELL | %s | no open position",
                    market.upper(),
                    symbol,
                )
                continue
            quote_ok, quote_reason = _execution_quote_guard(market, symbol, price, signal, position, quote)
            if not quote_ok:
                log.info(
                    "%s | REJECT SELL | %s | %s",
                    market.upper(),
                    symbol,
                    quote_reason,
                )
                continue

            if _close_position(market, position, price, "sell_signal", quote_metadata=quote):
                actions.append(
                    {
                        "market": market,
                        "symbol": symbol,
                        "action": "SELL",
                        "price": price,
                        "reason": "sell_signal",
                    }
                )
            continue

        buy_signal = action in {
            "BUY",
            "STRONG_BUY",
            "ACCUMULATE",
            "LONG",
        }

        if not buy_signal:
            log.info(
                "%s | REJECT | %s | action=%s is not an entry",
                market.upper(),
                symbol,
                action,
            )
            continue
        if _execution_disabled("buy signal", market, "entry"):
            continue
        quote_ok, quote_reason = _execution_quote_guard(market, symbol, price, signal, quote_metadata=quote)
        if not quote_ok:
            log.info(
                "%s | REJECT BUY | %s | %s",
                market.upper(),
                symbol,
                quote_reason,
            )
            continue

        forecast_ready, forecast_reason = _entry_forecast_gate(market, symbol, price, signal, quote)
        if not forecast_ready:
            log.info(
                "%s | REJECT | %s | forecast gate: %s",
                market.upper(),
                symbol,
                forecast_reason,
            )
            continue

        log.info(
            "EXECUTION_QUOTE_HANDOFF | symbol=%s | market=%s | price=%s | bid=%s | ask=%s | "
            "timestamp=%s | provider=%s | verified=%s | stale=%s | spread_pct=%s | "
            "capability=%s | correlation_id=%s",
            symbol,
            market,
            quote.get("price"),
            quote.get("bid"),
            quote.get("ask"),
            quote.get("quote_timestamp") or quote.get("timestamp"),
            quote.get("provider"),
            quote.get("quote_verified"),
            quote.get("stale"),
            quote.get("spread_pct"),
            quote.get("source_capability"),
            quote.get("correlation_id") or quote.get("decision_correlation_id"),
        )

        # A BUY qualifies through either score or confidence. Only signals
        # that are weak on both measurements are rejected.
        minimum_score = max(45.0, HIGH_SCORE_THRESHOLD - 7.0)
        minimum_confidence = max(
            0.40,
            HIGH_CONFIDENCE_THRESHOLD - 0.08,
        )

        if score < minimum_score and confidence < minimum_confidence:
            log.info(
                "%s | REJECT | %s | weak signal: score=%.2f < %.2f "
                "and confidence=%.3f < %.3f",
                market.upper(),
                symbol,
                score,
                minimum_score,
                confidence,
                minimum_confidence,
            )
            continue

        penny_ready, penny_reason = _penny_stock_gate(market, symbol, price, signal, score, confidence)
        if not penny_ready:
            log.info(
                "%s | REJECT | %s | penny-stock gate: %s",
                market.upper(),
                symbol,
                penny_reason,
            )
            continue

        quant_assessment = None
        optimizer_target = safe_float(
            signal_value(
                signal,
                "planned_trade_value",
                signal_value(signal, "v39_optimizer_approved_amount", optimizer_allocations.get(symbol)),
            )
        )
        target_trade_value = optimizer_target if optimizer_target > 0 else None
        if ENABLE_QUANT_TRADE_STANDARD:
            allocation_equity = portfolio_equity(market)
            allocation_positions = rows(
                "SELECT * FROM positions WHERE market=%s",
                (market,),
            )
            oracle_decision = evaluate_opportunity(
                signal,
                market=market,
                min_quality=QUANT_MIN_QUALITY,
                min_net_ev_pct=QUANT_MIN_NET_EV_PCT,
                max_spread_pct=QUANT_MAX_SPREAD_PCT,
                max_slippage_pct=QUANT_MAX_SLIPPAGE_PCT,
                portfolio=allocation_equity,
                positions=allocation_positions,
            )
            quant_assessment = assess_trade(
                signal,
                market=market,
                min_quality=QUANT_MIN_QUALITY,
                min_net_ev_pct=QUANT_MIN_NET_EV_PCT,
                max_spread_pct=QUANT_MAX_SPREAD_PCT,
                max_slippage_pct=QUANT_MAX_SLIPPAGE_PCT,
            )
            scenario_multiplier = float(oracle_decision.scenario.get("position_multiplier", 1.0))
            global_multiplier = float(oracle_decision.global_intelligence.get("position_multiplier", 1.0))
            capital_multiplier = float(oracle_decision.capital.get("final_multiplier", 1.0))
            radar_multiplier = float(oracle_decision.radar.get("position_multiplier", 1.0))
            portfolio_multiplier = float(oracle_decision.portfolio_supercomputer.get("position_multiplier", 1.0))
            oracle_target_trade_value = float(oracle_decision.portfolio_supercomputer.get("recommended_trade_value", 0.0))
            if oracle_target_trade_value > 0 and target_trade_value is not None:
                target_trade_value = min(target_trade_value, oracle_target_trade_value)
            elif oracle_target_trade_value > 0:
                target_trade_value = oracle_target_trade_value
            quant_assessment = replace(
                quant_assessment,
                position_multiplier=max(0.0, min(1.15, quant_assessment.position_multiplier * global_multiplier * radar_multiplier * scenario_multiplier * capital_multiplier * portfolio_multiplier)),
                approved=bool(quant_assessment.approved and oracle_decision.recommendation == "BUY"),
                reason=(
                    f"{quant_assessment.reason}; {oracle_decision.global_intelligence.get('summary', '')}; "
                    f"{oracle_decision.radar.get('summary', '')}; {oracle_decision.scenario.get('summary', '')}; "
                    f"{oracle_decision.capital.get('summary', '')}; {oracle_decision.portfolio_supercomputer.get('summary', '')}; "
                    f"{oracle_decision.explainability.get('summary', '')}"
                ),
            )
            if oracle_decision.recommendation != "BUY":
                log.info(
                    "%s | ORACLE MEMORY REJECT | %s | %s",
                    market.upper(),
                    symbol,
                    oracle_decision.reason,
                )
                continue

        existing_position = row(
            """
            SELECT *
            FROM positions
            WHERE market = %s
              AND symbol = %s
            """,
            (market, symbol),
        )

        recent = recent_trade(market, symbol)
        if recent:
            log.info(
                "%s | REJECT | %s | cooldown active after recent trade",
                market.upper(),
                symbol,
            )
            continue

        open_count = _open_position_count(market)
        rotation_candidate = None
        if not existing_position and open_count >= maximum_positions:
            rotation_candidate = _rotate_for_stronger_candidate(market, symbol, score, prices, anomalous_symbols)
            if rotation_candidate is None:
                log.info(
                    "%s | REJECT | %s | position limit reached %d/%d and no superior rotation",
                    market.upper(),
                    symbol,
                    open_count,
                    maximum_positions,
                )
                continue

        success, reason, rotation_action = _buy(
            market, symbol, price, signal, quant_assessment,
            target_trade_value=target_trade_value,
            rotation_candidate=rotation_candidate,
            verified_quote=quote,
            rotation_verified_quote=(
                rotation_candidate.get("_verified_rotation_quote")
                if isinstance(rotation_candidate, dict)
                else None
            ),
        )
        if not success:
            log.info(
                "%s | REJECT BUY | %s | %s",
                market.upper(),
                symbol,
                reason,
            )
            continue

        if rotation_action:
            actions.append(rotation_action)
        actions.append(
            {
                "market": market,
                "symbol": symbol,
                "action": action,
                "price": price,
                "score": score,
                "confidence": confidence,
                "reason": reason,
                "quant": quant_assessment.to_dict() if quant_assessment else None,
                "planned_trade_value": target_trade_value,
                "optimizer_approved_amount": optimizer_target if optimizer_target > 0 else None,
                "signal_id": signal_value(signal, "signal_id", signal_value(signal, "id", None)),
                "forecast_id": signal_value(signal, "forecast_id", None),
                "created_at": signal_value(signal, "created_at", None),
            }
        )

    log.info(
        "%s | Signal processing complete | executed_actions=%d",
        market.upper(),
        len(actions),
    )
    return actions


# =========================================================
# EQUITY SNAPSHOT
# =========================================================

def snapshot(
    market: str,
    *_: Any,
    **__: Any,
) -> dict[str, float]:
    market = safe_text(market).lower()
    execution_enabled = _autotrade_enabled(market)
    data = portfolio_equity(market, read_only=not execution_enabled)

    equity = safe_float(data.get("equity"))
    cash = safe_float(data.get("cash"))
    positions_value = safe_float(
        data.get("positions_value")
    )
    starting_balance = max(
        safe_float(
            data.get("starting_balance"),
            market_starting_capital(market),
        ),
        0.01,
    )

    drawdown = min(
        0.0,
        (equity - starting_balance) / starting_balance,
    )

    if not execution_enabled:
        return {
            "cash": cash,
            "positions_value": positions_value,
            "equity": equity,
            "starting_balance": starting_balance,
            "drawdown": drawdown,
            "margin_debt": safe_float(data.get("margin_debt")),
            "leverage_used": safe_float(data.get("leverage_used")),
            "margin_utilization_pct": safe_float(data.get("margin_utilization_pct")),
            "buying_power": safe_float(data.get("buying_power")),
            "maintenance_requirement": safe_float(data.get("maintenance_requirement")),
            "excess_liquidity": safe_float(data.get("excess_liquidity")),
            "margin_interest_accrued": safe_float(data.get("margin_interest_accrued")),
        }

    try:
        now = utc_now()
        risk_state = (
            "margin-call" if bool(data.get("margin_call"))
            else "elevated-margin" if safe_float(data.get("margin_utilization_pct")) >= PAPER_MARGIN_WARNING_PCT * 100.0
            else "normal"
        )
        execute(
            """
            UPDATE portfolios
            SET peak_equity = GREATEST(COALESCE(peak_equity, 0), %s),
                risk_state = %s,
                updated_at = %s
            WHERE market = %s
            """,
            (equity, risk_state, now, market),
        )
        execute(
            """
            INSERT INTO equity_snapshots (
                market,
                equity,
                cash,
                positions_value,
                drawdown,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                market,
                equity,
                cash,
                positions_value,
                drawdown,
                now,
            ),
        )
    except Exception:
        log.exception(
            "Could not save equity snapshot for %s",
            market,
        )

    return {
        "cash": cash,
        "positions_value": positions_value,
        "equity": equity,
        "starting_balance": starting_balance,
        "drawdown": drawdown,
        "margin_debt": safe_float(data.get("margin_debt")),
        "buying_power": safe_float(data.get("buying_power")),
        "gross_exposure": safe_float(data.get("gross_exposure")),
        "leverage_used": safe_float(data.get("leverage_used")),
        "leverage_limit": safe_float(data.get("leverage_limit")),
        "margin_utilization_pct": safe_float(data.get("margin_utilization_pct")),
        "excess_liquidity": safe_float(data.get("excess_liquidity")),
        "margin_interest_accrued": safe_float(data.get("margin_interest_accrued")),
    }
