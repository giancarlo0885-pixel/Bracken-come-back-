from __future__ import annotations

from dataclasses import replace

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config import *
from database import connect, row, rows, utc_now
from quant_trade_standard import assess_trade
from oracle_intelligence import evaluate_opportunity
from market_memory import record_closed_trade_memory
from paper_broker import (
    accrued_interest,
    allocate_purchase,
    allocate_sale,
    build_account,
    market_leverage_limit,
    market_starting_capital,
)


log = logging.getLogger("oracle-bot")


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
        if result != result:
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
    text = safe_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_forecast_gate(market: str, symbol: str, price: float) -> tuple[bool, str]:
    """Require a fresh, actionable forecast before a new paper entry.

    The deep worker saves the forecast before calling ``process_signals``. This
    final execution gate keeps stale ranking records and quote-only signals from
    reaching the institutional paper broker as new purchases.
    """
    if not REQUIRE_TARGET_FOR_BUY:
        return True, "forecast gate disabled"

    forecast = row(
        """
        SELECT target_price, low_price, high_price, probability_up, created_at
        FROM forecasts
        WHERE market = %s AND symbol = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (market, symbol),
    ) or {}
    target = safe_float(forecast.get("target_price"))
    if price <= 0:
        return False, "missing live entry price"
    if target <= 0:
        return False, "missing current forecast target"

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


def execute(
    sql: str,
    params: tuple[Any, ...] = (),
) -> None:
    with connect() as conn:
        conn.execute(sql, params)


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
) -> dict[str, float]:
    market = safe_text(market).lower()
    portfolio = _accrue_paper_margin_interest(market, ensure_portfolio(market))

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
    prices: dict[str, float] | None = None,
    *_: Any,
    **__: Any,
) -> int:
    market = safe_text(market).lower()
    prices = prices or {}

    updated = 0

    positions = rows(
        """
        SELECT *
        FROM positions
        WHERE market = %s
        """,
        (market,),
    )

    # The paper broker behaves like a real prime broker: when leverage reaches
    # the hard utilization ceiling, it reduces the weakest positions until the
    # account returns to the warning band. This is simulated risk control only.
    closed_for_margin: set[str] = set()
    account_state = portfolio_equity(market)
    if (
        PAPER_BROKER_MODE
        and positions
        and (
            bool(account_state.get("margin_call"))
            or safe_float(account_state.get("margin_utilization_pct"))
            > PAPER_MAX_MARGIN_UTILIZATION_PCT * 100.0
        )
    ):
        def position_return(item: dict[str, Any]) -> float:
            entry = safe_float(item.get("average_price", item.get("entry_price")))
            current = safe_float(prices.get(safe_text(item.get("symbol")).upper()), safe_float(item.get("current_price")))
            return (current - entry) / entry if entry > 0 and current > 0 else -999.0

        for position in sorted(positions, key=position_return):
            symbol = safe_text(position.get("symbol")).upper()
            current_price = safe_float(prices.get(symbol), safe_float(position.get("current_price")))
            if current_price <= 0:
                continue
            if _close_position(market, position, current_price, "paper_margin_reduction"):
                closed_for_margin.add(symbol)
                actions.append({
                    "market": market, "symbol": symbol, "action": "SELL",
                    "price": current_price, "reason": "paper_margin_reduction",
                    "return_pct": position_return(position),
                })
            account_state = portfolio_equity(market)
            if (
                not bool(account_state.get("margin_call"))
                and safe_float(account_state.get("margin_utilization_pct"))
                < PAPER_MARGIN_WARNING_PCT * 100.0
            ):
                break

    for position in positions:
        if safe_text(position.get("symbol")).upper() in closed_for_margin:
            continue
        symbol = safe_text(position.get("symbol")).upper()
        current_price = safe_float(
            prices.get(symbol, prices.get(symbol.lower()))
        )

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
) -> bool:
    market = safe_text(market).lower()
    symbol = safe_text(position.get("symbol")).upper()
    quantity = safe_float(position.get("quantity"))
    price = safe_float(price)

    if not symbol or quantity <= 0 or price <= 0:
        return False

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
    prices: dict[str, float] | None = None,
    *_: Any,
    **__: Any,
) -> list[dict[str, Any]]:
    market = safe_text(market).lower()
    prices = prices or {}

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

        entry_price = safe_float(
            position.get(
                "average_price",
                position.get("entry_price", 0.0),
            )
        )

        current_price = safe_float(
            prices.get(symbol, prices.get(symbol.lower()))
        )

        if current_price <= 0:
            current_price = safe_float(
                position.get("current_price", 0.0)
            )

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

        if _close_position(
            market,
            position,
            current_price,
            reason,
        ):
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


def _rotate_for_stronger_candidate(
    market: str,
    incoming_symbol: str,
    incoming_score: float,
) -> dict[str, Any] | None:
    """Free one slot only when a materially stronger paper opportunity appears."""
    if not ROTATION_ENABLED:
        return None
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
        current = safe_float(position.get("current_price"))
        entry = safe_float(position.get("average_price"), safe_float(position.get("entry_price")))
        return_pct = ((current / entry) - 1.0) * 100.0 if entry > 0 and current > 0 else -999.0
        held_score = _latest_opportunity_score(market, symbol)
        ranked.append((held_score, return_pct, position))

    held_score, return_pct, weakest = min(ranked, key=lambda item: (item[0], item[1]))
    score_gap = incoming_score - held_score
    if score_gap < ROTATION_MIN_SCORE_GAP:
        return None

    weakest_symbol = safe_text(weakest.get("symbol")).upper()
    exit_price = safe_float(weakest.get("current_price"))
    if exit_price <= 0:
        return None
    reason = f"continuous_rotation_to_{incoming_symbol}"
    if not _close_position(market, weakest, exit_price, reason):
        return None
    log.info(
        "%s | CONTINUOUS ROTATION | sold=%s incoming=%s gap=%.2f",
        market.upper(),
        weakest_symbol,
        incoming_symbol,
        score_gap,
    )
    return {
        "market": market,
        "symbol": weakest_symbol,
        "action": "SELL",
        "price": exit_price,
        "reason": reason,
        "rotation_target": incoming_symbol,
        "score_gap": score_gap,
        "return_pct": return_pct,
    }


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
) -> tuple[bool, str]:
    """Execute an institutional paper-broker buy with controlled leverage."""
    market = safe_text(market).lower()
    symbol = safe_text(symbol).upper()
    price = safe_float(price)

    if not market:
        return False, "missing market"
    if not symbol:
        return False, "missing symbol"
    if price <= 0:
        return False, f"invalid price={price}"

    equity_data = portfolio_equity(market)
    equity = max(safe_float(equity_data.get("equity"), market_starting_capital(market)), 0.01)
    buying_power = max(0.0, safe_float(equity_data.get("buying_power")))
    gross_exposure = max(0.0, safe_float(equity_data.get("gross_exposure")))
    leverage_limit = max(1.0, safe_float(equity_data.get("leverage_limit"), market_leverage_limit(market)))
    maximum_gross = equity * leverage_limit * PAPER_MAX_MARGIN_UTILIZATION_PCT
    risk_limited_buying_power = max(0.0, maximum_gross - gross_exposure)
    available_buying_power = min(buying_power, risk_limited_buying_power)

    if available_buying_power < MIN_TRADE_VALUE:
        return (
            False,
            f"insufficient paper buying power={available_buying_power:.2f}; "
            f"leverage={safe_float(equity_data.get('leverage_used')):.2f}x/{leverage_limit:.2f}x",
        )

    maximum_trade_value = min(
        available_buying_power,
        equity * MAX_TRADE_VALUE_PCT,
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
        available_buying_power,
    )
    if target_trade_value is not None:
        target_value = max(0.0, safe_float(target_trade_value))
        if target_value > 0:
            trade_value = min(trade_value, target_value)

    if trade_value < MIN_TRADE_VALUE:
        return False, f"trade value too small={trade_value:.2f}; minimum={MIN_TRADE_VALUE:.2f}"

    now = utc_now()

    try:
        with connect() as conn:
            portfolio_record = conn.execute(
                "SELECT * FROM portfolios WHERE market=%s FOR UPDATE",
                (market,),
            ).fetchone()
            if not portfolio_record:
                return False, "portfolio row missing"

            current_positions = conn.execute(
                "SELECT * FROM positions WHERE market=%s FOR UPDATE",
                (market,),
            ).fetchall()
            account = build_account(market, portfolio_record, current_positions)
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
                return False, f"buying power changed during execution; available={current_available:.2f}"

            cash_reserve = max(0.0, account.equity * MIN_CASH_RESERVE_PCT)
            new_cash, new_margin_debt, cash_used, borrowed = allocate_purchase(
                cash=account.cash,
                margin_debt=account.margin_debt,
                trade_value=trade_value,
                cash_reserve=cash_reserve,
            )
            quantity = trade_value / price
            if quantity <= 0:
                return False, f"invalid quantity={quantity}"

            existing = next(
                (p for p in current_positions if safe_text(p.get("symbol")).upper() == symbol),
                None,
            )
            if existing:
                old_quantity = safe_float(existing.get("quantity"))
                old_average = safe_float(
                    existing.get("average_price", existing.get("entry_price", price)),
                    price,
                )
                combined_quantity = old_quantity + quantity
                if combined_quantity <= 0:
                    return False, "combined position quantity is invalid"
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

        log.info(
            "%s | INSTITUTIONAL PAPER BUY | %s | quantity=%.8f | price=%.6f | "
            "value=%.2f | borrowed=%.2f | score=%.2f | confidence=%.3f",
            market.upper(), symbol, quantity, price, trade_value, borrowed, score, confidence,
        )
        return True, (
            f"paper buy executed: ${trade_value:,.2f}; "
            f"cash ${cash_used:,.2f}; margin ${borrowed:,.2f}"
        )

    except Exception as exc:
        log.exception("%s | BUY FAILED | %s", market.upper(), symbol)
        return False, f"database execution error: {exc}"


# =========================================================
# SIGNAL PROCESSING
# =========================================================

def process_signals(
    market: str,
    signals: list[Any] | tuple[Any, ...] | None,
    prices: dict[str, float] | None = None,
    *_: Any,
    **__: Any,
) -> list[dict[str, Any]]:
    market = safe_text(market).lower()
    signals = list(signals or [])
    prices = prices or {}
    actions: list[dict[str, Any]] = []

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
        price = signal_price(
            signal,
            safe_float(prices.get(symbol, prices.get(symbol.lower()))),
        )

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
                "%s | REJECT | %s | invalid/missing price",
                market.upper(),
                symbol,
            )
            continue

        if action in {"SELL", "EXIT", "CLOSE"}:
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

            if _close_position(market, position, price, "sell_signal"):
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

        # Strong HOLD signals can become accumulation entries.
        aggressive_hold = (
            action == "HOLD"
            and (
                (
                    score >= max(50.0, HIGH_SCORE_THRESHOLD - 2.0)
                    and confidence >= max(0.44, HIGH_CONFIDENCE_THRESHOLD - 0.04)
                )
                or (
                    # V27: very high confidence can qualify a borderline-score
                    # HOLD for the controlled starter-entry evaluation path.
                    confidence >= 0.82
                    and score >= max(45.0, HIGH_SCORE_THRESHOLD - 8.0)
                )
            )
        )

        if not buy_signal and not aggressive_hold:
            log.info(
                "%s | REJECT | %s | action=%s is not an entry",
                market.upper(),
                symbol,
                action,
            )
            continue

        forecast_ready, forecast_reason = _entry_forecast_gate(market, symbol, price)
        if not forecast_ready:
            log.info(
                "%s | REJECT | %s | forecast gate: %s",
                market.upper(),
                symbol,
                forecast_reason,
            )
            continue

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

        quant_assessment = None
        target_trade_value = None
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
            target_trade_value = float(oracle_decision.portfolio_supercomputer.get("recommended_trade_value", 0.0))
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

        open_count = _open_position_count(market)
        if not existing_position and open_count >= maximum_positions:
            rotation_action = _rotate_for_stronger_candidate(market, symbol, score)
            if rotation_action is None:
                log.info(
                    "%s | REJECT | %s | position limit reached %d/%d and no superior rotation",
                    market.upper(),
                    symbol,
                    open_count,
                    maximum_positions,
                )
                continue
            actions.append(rotation_action)

        recent = recent_trade(market, symbol)
        if recent:
            log.info(
                "%s | REJECT | %s | cooldown active after recent trade",
                market.upper(),
                symbol,
            )
            continue

        success, reason = _buy(
            market, symbol, price, signal, quant_assessment,
            target_trade_value=target_trade_value,
        )
        if not success:
            log.info(
                "%s | REJECT BUY | %s | %s",
                market.upper(),
                symbol,
                reason,
            )
            continue

        actions.append(
            {
                "market": market,
                "symbol": symbol,
                "action": "BUY",
                "price": price,
                "score": score,
                "confidence": confidence,
                "reason": reason,
                "quant": quant_assessment.to_dict() if quant_assessment else None,
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
    data = portfolio_equity(market)

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
