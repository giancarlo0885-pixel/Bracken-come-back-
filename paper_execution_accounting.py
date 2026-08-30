from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import logging
import math
import os
import uuid
from typing import Any

import paper_execution_reality as reality


log = logging.getLogger("paper-execution-accounting")


_fee_context: ContextVar[dict[str, float]] = ContextVar(
    "paper_execution_fee_context",
    default={"buy_fee_pct": 0.0, "sell_fee_pct": 0.0},
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _positive(value: Any) -> float | None:
    number = _finite(value, -1.0)
    return number if number > 0 else None


def _non_negative(value: Any) -> float | None:
    number = _finite(value, -1.0)
    return number if number >= 0 else None


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return value if math.isfinite(value) else default


def _fee_pct(market: str, quote: dict[str, Any] | None = None, signal: Any | None = None) -> float:
    data = dict(quote or {})
    for key in ("paper_fee_pct", "estimated_fees_pct", "fee_pct"):
        value = _non_negative(data.get(key))
        if value is not None:
            return value
    if signal is not None:
        for key in ("paper_fee_pct", "estimated_fees_pct", "fee_pct"):
            value = _non_negative(reality._signal_value(signal, key, None))
            if value is not None:
                return value
    defaults = reality._market_defaults(market)
    return max(0.0, _finite(defaults.get("fee_pct"), 0.0))


def _simulate_fill_explicit_fee(
    *,
    side: str,
    market: str,
    reference_price: float,
    quote: dict[str, Any] | None = None,
    order_value: float | None = None,
    slippage_pct: float | None = None,
    fee_pct: float | None = None,
    spread_pct: float | None = None,
    market_impact_pct: float | None = None,
    latency_pct: float | None = None,
):
    """Price a paper fill without burying fees inside the execution price.

    Spread, slippage, market impact and latency affect the simulated fill price.
    Fees remain a separate cash/ledger charge.  This prevents the UI from
    reporting a fee-free ledger while the fee is hidden in cost basis.
    """
    side_text = str(side or "").upper().strip()
    if side_text not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    reference = _positive(reference_price)
    if reference is None:
        raise ValueError("reference_price must be positive and finite")

    quote_data = dict(quote or {})
    defaults = reality._market_defaults(market)
    explicit_spread = _non_negative(spread_pct)
    spread = (
        explicit_spread
        if explicit_spread is not None
        else reality._spread_pct(reference, quote_data, defaults["spread_pct"])
    )
    spread = max(0.0, _finite(spread, 0.0))

    bid = _positive(quote_data.get("bid"))
    ask = _positive(quote_data.get("ask"))
    if side_text == "BUY":
        executable = max(reference, ask) if ask is not None else reference * (1.0 + spread / 2.0)
    else:
        executable = min(reference, bid) if bid is not None else reference * max(0.000001, 1.0 - spread / 2.0)

    slip = _non_negative(slippage_pct)
    if slip is None:
        slip = _non_negative(quote_data.get("estimated_slippage_pct"))
    if slip is None:
        slip = _non_negative(quote_data.get("slippage_pct"))
    slippage = defaults["slippage_pct"] if slip is None else slip

    fee = _non_negative(fee_pct)
    if fee is None:
        fee = _non_negative(quote_data.get("estimated_fees_pct"))
    if fee is None:
        fee = defaults["fee_pct"]

    liquidity = reality._liquidity_value(reference, quote_data)
    derived_impact, participation = reality._market_impact_pct(
        quote_data,
        order_value=order_value,
        liquidity_value=liquidity,
    )
    impact = _non_negative(market_impact_pct)
    if impact is None:
        impact = derived_impact

    latency = _non_negative(latency_pct)
    if latency is None:
        latency = defaults["latency_pct"]

    max_adverse = max(0.0, _env_float("PAPER_MAX_ADVERSE_FILL_PCT", 0.05))
    # Fees are intentionally excluded here and charged separately by the
    # allocation and attribution wrappers below.
    price_friction = min(max_adverse, max(0.0, slippage + impact + latency))
    if side_text == "BUY":
        fill_price = executable * (1.0 + price_friction)
    else:
        fill_price = executable * max(0.000001, 1.0 - price_friction)

    adverse = abs(fill_price - reference) / reference
    return reality.SimulatedFill(
        side=side_text,
        market=str(market or "").lower(),
        reference_price=reference,
        executable_reference=round(executable, 12),
        fill_price=round(fill_price, 12),
        spread_pct=round(spread, 10),
        slippage_pct=round(slippage, 10),
        fee_pct=round(max(0.0, fee), 10),
        market_impact_pct=round(max(0.0, impact), 10),
        latency_pct=round(max(0.0, latency), 10),
        adverse_cost_pct=round(adverse, 10),
        participation_rate=round(participation, 10) if participation is not None else None,
        liquidity_value=round(liquidity, 8) if liquidity is not None else None,
    )


def _requested_notional(signal: Any | None, target_trade_value: float | None) -> float | None:
    target = _positive(target_trade_value)
    if target is not None:
        return target
    if signal is None:
        return None
    for key in ("planned_trade_value", "v39_optimizer_approved_amount", "recommended_trade_value"):
        value = _positive(reality._signal_value(signal, key, None))
        if value is not None:
            return value
    return None


def _fill_capacity(reference_price: float, quote: dict[str, Any] | None) -> tuple[float | None, float | None]:
    data = dict(quote or {})
    liquidity = reality._liquidity_value(reference_price, data)
    if liquidity is None or liquidity <= 0:
        return None, liquidity
    max_participation = min(
        0.01,
        max(0.0001, _env_float("PAPER_FILL_MAX_PARTICIPATION_PCT", 0.0025)),
    )
    return liquidity * max_participation, liquidity


def _fee_aware_fifo_close_lots(
    lots,
    *,
    quantity: float,
    exit_price: float,
    exit_time: Any,
    fees: float = 0.0,
    tier: str | None = None,
    confidence_score: float | None = None,
    weighted_signal_score: float | None = None,
    quote_provider: str | None = None,
    decision_id: str | None = None,
    order_id: str | None = None,
):
    """FIFO lot close that allocates both entry and exit fees exactly once."""
    from profit_attribution import TradeLedgerRow

    remaining = _finite(quantity)
    if remaining <= 0:
        raise ValueError("close quantity must be positive")
    total_close_quantity = remaining
    exit_dt = exit_time
    if not isinstance(exit_dt, datetime):
        text = str(exit_time or "").strip()
        try:
            exit_dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            exit_dt = datetime.now(timezone.utc)
    if exit_dt.tzinfo is None:
        exit_dt = exit_dt.replace(tzinfo=timezone.utc)

    ordered = sorted(
        [lot for lot in lots if _finite(getattr(lot, "quantity_remaining", 0.0)) > 0],
        key=lambda lot: lot.opened_at,
    )
    rows = []
    total_exit_fee = max(0.0, _finite(fees))
    for lot in ordered:
        if remaining <= 0:
            break
        lot_remaining = _finite(lot.quantity_remaining)
        close_qty = min(lot_remaining, remaining)
        if close_qty <= 0:
            continue
        exit_fee_allocated = total_exit_fee * (close_qty / total_close_quantity)
        opened_qty = max(_finite(lot.quantity_opened), close_qty)
        entry_fee_allocated = max(0.0, _finite(lot.entry_fees)) * (close_qty / opened_qty)
        gross = (_finite(exit_price) - _finite(lot.entry_price)) * close_qty
        total_fees = entry_fee_allocated + exit_fee_allocated
        net = gross - total_fees
        entry_cost_basis = _finite(lot.entry_price) * close_qty + entry_fee_allocated
        return_pct = (net / entry_cost_basis * 100.0) if entry_cost_basis > 0 else 0.0

        lot.quantity_remaining = round(lot_remaining - close_qty, 10)
        remaining = round(remaining - close_qty, 10)
        rows.append(
            TradeLedgerRow(
                trade_id=str(uuid.uuid4()),
                symbol=lot.symbol,
                market=lot.market,
                bucket=lot.bucket,
                strategy=lot.strategy,
                side="SELL",
                quantity=close_qty,
                entry_time=lot.opened_at,
                entry_price=lot.entry_price,
                exit_time=exit_dt,
                exit_price=_finite(exit_price),
                gross_pnl=round(gross, 10),
                fees=round(total_fees, 10),
                net_pnl=round(net, 10),
                return_pct=round(return_pct, 10),
                tier=tier,
                confidence_score=confidence_score,
                weighted_signal_score=weighted_signal_score,
                quote_provider=quote_provider,
                decision_id=decision_id if decision_id is not None else lot.decision_id,
                status="CLOSED" if lot.quantity_remaining <= 0 else "PARTIAL",
                broker_mode=lot.broker_mode,
                account_environment=lot.account_environment,
                order_id=order_id,
            )
        )
    if remaining > 0:
        raise ValueError("not enough open lot quantity to close")
    return rows


def _latest_trade(market: str, symbol: str, side: str, since: str) -> dict[str, Any] | None:
    try:
        from database import connect

        with connect() as conn:
            return conn.execute(
                """
                SELECT * FROM trades
                WHERE market=%s AND symbol=%s AND side=%s AND created_at >= %s
                ORDER BY id DESC LIMIT 1
                """,
                (market, symbol, side, since),
            ).fetchone()
    except Exception as exc:
        log.warning("Paper trade lookup failed for accounting: %s", exc)
        return None


def _finalize_trade_row(trade: dict[str, Any] | None, fee_pct: float) -> tuple[float, float, float]:
    if not trade:
        return 0.0, 0.0, 0.0
    trade_id = trade.get("id")
    notional = max(0.0, _finite(trade.get("value")))
    fee_amount = notional * max(0.0, fee_pct)
    gross = _finite(trade.get("realized_pnl"))
    net = gross - fee_amount if str(trade.get("side") or "").upper() == "SELL" else gross
    try:
        from database import connect

        with connect() as conn:
            conn.execute(
                """
                UPDATE trades
                SET fees=%s,
                    gross_realized_pnl=%s,
                    realized_pnl=%s
                WHERE id=%s
                """,
                (fee_amount, gross, net, trade_id),
            )
    except Exception as exc:
        log.warning("Paper trade fee finalization failed: %s", exc)
    return notional, fee_amount, net


def _record_order_and_fill(
    *,
    market: str,
    symbol: str,
    side: str,
    requested_notional: float | None,
    trade: dict[str, Any] | None,
    fill: Any,
    fee_amount: float,
    quote: dict[str, Any] | None,
    reason: str,
) -> None:
    if not trade:
        return
    filled_notional = max(0.0, _finite(trade.get("value")))
    filled_quantity = max(0.0, _finite(trade.get("quantity")))
    if filled_notional <= 0 or filled_quantity <= 0:
        return
    requested = max(filled_notional, _finite(requested_notional, filled_notional))
    requested_qty = requested / fill.reference_price if fill.reference_price > 0 else filled_quantity
    remaining_notional = max(0.0, requested - filled_notional)
    remaining_quantity = max(0.0, requested_qty - filled_quantity)
    status = "PARTIAL_CANCELLED" if remaining_notional > max(0.01, requested * 1e-6) else "FILLED"
    now = datetime.now(timezone.utc).isoformat()
    order_id = f"paper-order:{uuid.uuid4()}"
    fill_id = f"paper-fill:{uuid.uuid4()}"
    data = dict(quote or {})
    try:
        from database import connect

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO paper_orders(
                    order_id,market,symbol,side,order_type,status,
                    requested_quantity,requested_notional,filled_quantity,filled_notional,
                    remaining_quantity,remaining_notional,reference_price,average_fill_price,
                    fee_amount,fee_pct,liquidity_value,participation_rate,quote_provider,
                    quote_timestamp,reason,created_at,updated_at
                ) VALUES (%s,%s,%s,%s,'MARKET_IOC',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    order_id, market, symbol, side, status,
                    requested_qty, requested, filled_quantity, filled_notional,
                    remaining_quantity, remaining_notional, fill.reference_price,
                    fill.fill_price, fee_amount, fill.fee_pct, fill.liquidity_value,
                    fill.participation_rate, data.get("provider"),
                    data.get("quote_timestamp") or data.get("timestamp"), reason, now, now,
                ),
            )
            conn.execute(
                """
                INSERT INTO paper_fills(
                    fill_id,order_id,market,symbol,side,quantity,reference_price,
                    fill_price,notional,fee_amount,fee_pct,slippage_pct,spread_pct,
                    market_impact_pct,latency_pct,quote_provider,quote_timestamp,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    fill_id, order_id, market, symbol, side, filled_quantity,
                    fill.reference_price, fill.fill_price, filled_notional, fee_amount,
                    fill.fee_pct, fill.slippage_pct, fill.spread_pct,
                    fill.market_impact_pct, fill.latency_pct, data.get("provider"),
                    data.get("quote_timestamp") or data.get("timestamp"), now,
                ),
            )
    except Exception as exc:
        log.warning("Paper order/fill audit persistence failed: %s", exc)


def install_paper_execution_accounting(market_worker_module: Any | None = None) -> None:
    """Install explicit fee accounting and conservative IOC partial-fill caps.

    This layer is paper-only.  It is intentionally installed after
    ``install_paper_execution_reality`` so the existing quote, forecast and risk
    gates remain authoritative.
    """
    execution_mode = str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower()
    if execution_mode != "paper":
        log.info("Paper execution accounting not installed because EXECUTION_MODE=%s", execution_mode)
        return

    import oracle_bot
    import profit_attribution

    if getattr(oracle_bot, "_paper_execution_accounting_installed", False):
        return

    # Make the existing realism wrapper use a fee-exclusive price model.
    reality.simulate_fill = _simulate_fill_explicit_fee

    original_allocate_purchase = oracle_bot.allocate_purchase
    original_allocate_sale = oracle_bot.allocate_sale
    original_record_buy = oracle_bot._record_buy_attribution
    original_record_sell = oracle_bot._record_sell_attribution
    original_memory = oracle_bot.record_closed_trade_memory
    current_buy = oracle_bot._buy
    current_close = oracle_bot._execute_close_position

    # Ensure lot accounting includes entry fees on every partial/FIFO close.
    oracle_bot.fifo_close_lots = _fee_aware_fifo_close_lots
    profit_attribution.fifo_close_lots = _fee_aware_fifo_close_lots

    def fee_aware_purchase(*, cash: float, margin_debt: float, trade_value: float, cash_reserve: float):
        context = _fee_context.get()
        fee_pct = max(0.0, _finite(context.get("buy_fee_pct")))
        total_cost = max(0.0, trade_value) * (1.0 + fee_pct)
        return original_allocate_purchase(
            cash=cash,
            margin_debt=margin_debt,
            trade_value=total_cost,
            cash_reserve=cash_reserve,
        )

    def fee_aware_sale(*, cash: float, margin_debt: float, sale_value: float):
        context = _fee_context.get()
        fee_pct = max(0.0, _finite(context.get("sell_fee_pct")))
        net_proceeds = max(0.0, sale_value) * max(0.0, 1.0 - fee_pct)
        return original_allocate_sale(
            cash=cash,
            margin_debt=margin_debt,
            sale_value=net_proceeds,
        )

    def fee_aware_buy_attribution(conn: Any, **kwargs: Any):
        context = _fee_context.get()
        fee_pct = max(0.0, _finite(context.get("buy_fee_pct")))
        quantity = max(0.0, _finite(kwargs.get("quantity")))
        price = max(0.0, _finite(kwargs.get("price")))
        kwargs["fees"] = quantity * price * fee_pct
        return original_record_buy(conn, **kwargs)

    def fee_aware_sell_attribution(conn: Any, **kwargs: Any):
        context = _fee_context.get()
        fee_pct = max(0.0, _finite(context.get("sell_fee_pct")))
        quantity = max(0.0, _finite(kwargs.get("quantity")))
        price = max(0.0, _finite(kwargs.get("price")))
        kwargs["fees"] = quantity * price * fee_pct
        return original_record_sell(conn, **kwargs)

    def fee_aware_memory(**kwargs: Any):
        context = _fee_context.get()
        fee_pct = max(0.0, _finite(context.get("sell_fee_pct")))
        quantity = max(0.0, _finite(kwargs.get("quantity")))
        exit_price = max(0.0, _finite(kwargs.get("exit_price")))
        kwargs["pnl"] = _finite(kwargs.get("pnl")) - quantity * exit_price * fee_pct
        return original_memory(**kwargs)

    oracle_bot.allocate_purchase = fee_aware_purchase
    oracle_bot.allocate_sale = fee_aware_sale
    oracle_bot._record_buy_attribution = fee_aware_buy_attribution
    oracle_bot._record_sell_attribution = fee_aware_sell_attribution
    oracle_bot.record_closed_trade_memory = fee_aware_memory

    def accounting_buy(
        market: str,
        symbol: str,
        price: float,
        signal: Any,
        quant_assessment: Any | None = None,
        target_trade_value: float | None = None,
        rotation_candidate: dict[str, Any] | None = None,
        verified_quote: dict[str, Any] | None = None,
        rotation_verified_quote: dict[str, Any] | None = None,
    ):
        started = datetime.now(timezone.utc).isoformat()
        requested = _requested_notional(signal, target_trade_value)
        capacity, _ = _fill_capacity(price, verified_quote)
        capped_target = target_trade_value
        if capacity is not None:
            if requested is not None:
                capped_target = min(requested, capacity)
            elif capped_target is None:
                capped_target = capacity

        buy_fee = _fee_pct(market, verified_quote, signal)
        sell_fee = 0.0
        rotation_symbol = ""
        outgoing_quote = dict(rotation_verified_quote or {})
        if rotation_candidate:
            rotation_symbol = str(rotation_candidate.get("symbol") or "").upper().strip()
            if not outgoing_quote:
                outgoing_quote = dict(rotation_candidate.get("_verified_rotation_quote") or {})
            sell_fee = _fee_pct(market, outgoing_quote, None)

        token = _fee_context.set({"buy_fee_pct": buy_fee, "sell_fee_pct": sell_fee})
        try:
            result = current_buy(
                market,
                symbol,
                price,
                signal,
                quant_assessment=quant_assessment,
                target_trade_value=capped_target,
                rotation_candidate=rotation_candidate,
                verified_quote=verified_quote,
                rotation_verified_quote=rotation_verified_quote,
            )
            if not result or not bool(result[0]):
                return result

            trade = _latest_trade(str(market).lower(), str(symbol).upper(), "BUY", started)
            _, fee_amount, _ = _finalize_trade_row(trade, buy_fee)
            fill = _simulate_fill_explicit_fee(
                side="BUY",
                market=market,
                reference_price=price,
                quote=verified_quote,
                order_value=max(0.0, _finite(trade.get("value") if trade else capped_target)),
                fee_pct=buy_fee,
            )
            _record_order_and_fill(
                market=str(market).lower(),
                symbol=str(symbol).upper(),
                side="BUY",
                requested_notional=requested,
                trade=trade,
                fill=fill,
                fee_amount=fee_amount,
                quote=verified_quote,
                reason="entry" if not rotation_candidate else "rotation_in",
            )

            if rotation_symbol:
                rotation_trade = _latest_trade(str(market).lower(), rotation_symbol, "SELL", started)
                _, rotation_fee_amount, _ = _finalize_trade_row(rotation_trade, sell_fee)
                reference = _positive(outgoing_quote.get("paper_reference_price")) or _positive(outgoing_quote.get("price"))
                if reference is not None and rotation_trade:
                    rotation_fill = _simulate_fill_explicit_fee(
                        side="SELL",
                        market=market,
                        reference_price=reference,
                        quote=outgoing_quote,
                        order_value=_finite(rotation_trade.get("value")),
                        fee_pct=sell_fee,
                    )
                    _record_order_and_fill(
                        market=str(market).lower(),
                        symbol=rotation_symbol,
                        side="SELL",
                        requested_notional=_finite(rotation_trade.get("value")),
                        trade=rotation_trade,
                        fill=rotation_fill,
                        fee_amount=rotation_fee_amount,
                        quote=outgoing_quote,
                        reason="rotation_out",
                    )
            return result
        finally:
            _fee_context.reset(token)

    def accounting_close(
        market: str,
        position: dict[str, Any],
        price: float,
        reason: str,
        quote_metadata: dict[str, Any] | None = None,
    ) -> bool:
        symbol = str(position.get("symbol") or "").upper().strip()
        started = datetime.now(timezone.utc).isoformat()
        sell_fee = _fee_pct(market, quote_metadata, None)
        token = _fee_context.set({"buy_fee_pct": 0.0, "sell_fee_pct": sell_fee})
        try:
            result = current_close(
                market,
                position,
                price,
                reason,
                quote_metadata=quote_metadata,
            )
            if not result:
                return False
            trade = _latest_trade(str(market).lower(), symbol, "SELL", started)
            _, fee_amount, _ = _finalize_trade_row(trade, sell_fee)
            fill = _simulate_fill_explicit_fee(
                side="SELL",
                market=market,
                reference_price=price,
                quote=quote_metadata,
                order_value=_finite(trade.get("value") if trade else 0.0),
                fee_pct=sell_fee,
            )
            _record_order_and_fill(
                market=str(market).lower(),
                symbol=symbol,
                side="SELL",
                requested_notional=_finite(trade.get("value") if trade else 0.0),
                trade=trade,
                fill=fill,
                fee_amount=fee_amount,
                quote=quote_metadata,
                reason=reason,
            )
            return True
        finally:
            _fee_context.reset(token)

    oracle_bot._buy = accounting_buy
    oracle_bot._execute_close_position = accounting_close
    oracle_bot._paper_execution_accounting_installed = True
    log.info(
        "Installed explicit paper fee accounting and IOC partial-fill cap | max_participation=%.4f%%",
        min(0.01, max(0.0001, _env_float("PAPER_FILL_MAX_PARTICIPATION_PCT", 0.0025))) * 100.0,
    )
