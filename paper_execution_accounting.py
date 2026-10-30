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
    """Use one SELL-ledger and full-round-trip fee contract.

    BUY rows already record entry fees. The canonical policy keeps SELL fees
    exit-only, supplies separate full-cost outcome fields, and preserves the
    immutable entry metadata on partial FIFO closes.
    """
    from paper_fee_policy import fee_aware_fifo_close_lots

    return fee_aware_fifo_close_lots(
        lots, quantity=quantity, exit_price=exit_price, exit_time=exit_time,
        fees=fees, tier=tier, confidence_score=confidence_score,
        weighted_signal_score=weighted_signal_score, quote_provider=quote_provider,
        decision_id=decision_id, order_id=order_id,
    )


def _record_order_and_fill(
    conn: Any,
    *,
    execution_trade_id: int,
    market: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    reference_price: float,
    fee_amount: float,
    requested_notional: float | None,
    lot_links: list[tuple[str, str, float]],
    quote: dict[str, Any] | None,
    reason: str,
) -> None:
    """Persist an exact trade/order/fill/lot graph in the execution transaction.

    No time-window or "latest trade" inference is permitted. The caller owns
    the transaction; any failure must propagate and roll back the whole trade.
    Existing historical fills with NULL canonical_trade_id remain unverified.
    """
    market = str(market).lower().strip()
    symbol = str(symbol).upper().strip()
    side = str(side).upper().strip()
    qty = _positive(quantity)
    executed_price = _positive(price)
    reference = _positive(reference_price)
    fee = _non_negative(fee_amount)
    if not market or not symbol or side not in {"BUY", "SELL"}:
        raise ValueError("paper audit requires an exact market, symbol, and side")
    if not execution_trade_id or qty is None or executed_price is None or reference is None or fee is None:
        raise ValueError("paper audit requires a valid committed execution ID, quantity, price, reference and fees")
    if not lot_links or any(
        not lot_id or not ledger_id or _positive(link_qty) is None
        for lot_id, ledger_id, link_qty in lot_links
    ):
        raise ValueError("paper audit requires exact lot and ledger links")
    if len({ledger_id for _, ledger_id, _ in lot_links}) != len(lot_links):
        raise ValueError("paper audit contains duplicate ledger references")
    from paper_fill_provenance import close as evidence_close
    linked_qty = sum(float(link_qty) for _, _, link_qty in lot_links)
    if not evidence_close(linked_qty, qty, quantity=True):
        raise ValueError("paper audit lot quantities do not reconcile")

    # Lock the exact row returned by the INSERT in this transaction. A matching
    # symbol and timestamp are not an execution identifier.
    trade = conn.execute(
        """SELECT id,market,symbol,side,quantity,price,value
           FROM trades WHERE id=%s FOR UPDATE""",
        (execution_trade_id,),
    ).fetchone()
    if not trade or int(trade["id"]) != int(execution_trade_id):
        raise ValueError("paper audit execution trade ID does not exist")
    filled_notional = qty * executed_price
    if (str(trade["market"]).lower() != market
            or str(trade["symbol"]).upper() != symbol
            or str(trade["side"]).upper() != side
            or not evidence_close(trade["quantity"], qty, quantity=True)
            or abs(_finite(trade["price"]) - executed_price) > max(1e-8, executed_price * 1e-8)
            or abs(_finite(trade["value"]) - filled_notional) > max(0.01, filled_notional * 1e-8)):
        raise ValueError("paper audit canonical execution identity or economics mismatch")

    fee_pct = fee / filled_notional
    data = dict(quote or {})
    if data.get("provider") in {"Coinbase Exchange paper pricing", "Kraken WebSocket v2 paper pricing"}:
        if (data.get("symbol")!=symbol or data.get("requested_symbol")!=symbol
                or data.get("provider_symbol")!=symbol or data.get("paper_fee_pct")!=0.006
                or abs(fee_pct-0.006)>1e-10):
            raise ValueError("paper price identity or modeled fee mismatch")
    pricing = reality.simulate_fill if data.get("provider") in {"Coinbase Exchange paper pricing", "Kraken WebSocket v2 paper pricing"} else _simulate_fill_explicit_fee
    fill = pricing(
        side=side, market=market, reference_price=reference, quote=data,
        order_value=data.get("paper_depth_order_value", filled_notional), fee_pct=fee_pct,
    )
    requested = max(filled_notional, _finite(requested_notional, filled_notional))
    requested_qty = requested / reference
    remaining_notional = max(0.0, requested - filled_notional)
    remaining_quantity = max(0.0, requested_qty - qty)
    status = "PARTIAL_CANCELLED" if remaining_notional > max(0.01, requested * 1e-6) else "FILLED"
    now = datetime.now(timezone.utc).isoformat()
    order_id = f"paper-order:{uuid.uuid4()}"
    fill_id = f"paper-fill:{uuid.uuid4()}"

    # The canonical trade fee and fill/lot links commit or roll back together.
    # SELL initially stores gross realized P/L; charge the exit fee once here.
    conn.execute(
        """UPDATE trades SET fees=%s, gross_realized_pnl=realized_pnl,
                  realized_pnl=CASE WHEN side='SELL' THEN realized_pnl-%s ELSE realized_pnl END
           WHERE id=%s""",
        (fee, fee, execution_trade_id),
    )
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
            order_id, market, symbol, side, status, requested_qty, requested,
            qty, filled_notional, remaining_quantity, remaining_notional, reference,
            executed_price, fee, fee_pct, fill.liquidity_value,
            fill.participation_rate, data.get("provider"),
            data.get("quote_timestamp") or data.get("timestamp"), reason, now, now,
        ),
    )
    conn.execute(
        """
        INSERT INTO paper_fills(
            fill_id,order_id,canonical_trade_id,market,symbol,side,quantity,reference_price,
            fill_price,notional,fee_amount,fee_pct,slippage_pct,spread_pct,
            market_impact_pct,latency_pct,quote_provider,quote_timestamp,created_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            fill_id, order_id, execution_trade_id, market, symbol, side, qty,
            reference, executed_price, filled_notional, fee, fee_pct,
            fill.slippage_pct, fill.spread_pct, fill.market_impact_pct,
            fill.latency_pct, data.get("provider"),
            data.get("quote_timestamp") or data.get("timestamp"), now,
        ),
    )
    if data.get("provider") in {"Coinbase Exchange paper pricing", "Kraken WebSocket v2 paper pricing"}:
        import json
        original=data["paper_original_price_quote"]
        if original.get("quote_id")!=data["quote_id"] or original.get("book")!=data.get("book"):
            raise ValueError("paper original price receipt mismatch")
        evidence={"source_quote":original,"execution":{"side":side,"quantity":qty,"price":executed_price,
            "reference_price":reference,"fee_amount":fee,"fee_pct":fee_pct,
            "depth_vwap":data.get("paper_depth_vwap"),"depth_order_value":data.get("paper_depth_order_value"),
            "slippage_pct":fill.slippage_pct,"latency_pct":fill.latency_pct,"market_impact_pct":fill.market_impact_pct},
            "execution_authority":"PAPER_SIMULATION_ONLY"}
        conn.execute("""INSERT INTO paper_fill_price_evidence
            (fill_id,model_version,quote_id,evidence,execution_authority)
            VALUES (%s,%s,%s,%s::jsonb,'PAPER_SIMULATION_ONLY')""",
            (fill_id, data["paper_price_model"], data["quote_id"], json.dumps(evidence,allow_nan=False,default=str)))
    for lot_id, ledger_trade_id, link_qty in lot_links:
        conn.execute(
            """INSERT INTO paper_fill_lot_links(
                   fill_id,lot_id,ledger_trade_id,quantity
               ) VALUES (%s,%s,%s,%s)""",
            (fill_id, lot_id, ledger_trade_id, link_qty),
        )


def _closed_lot_memory_outcome(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Use full-cost FIFO outcomes, keeping SELL ledger net semantics separate."""
    if not rows:
        return None
    totals = {"quantity": 0.0, "net_pnl": 0.0, "entry_cost_basis": 0.0}
    for row in rows:
        qty = _finite(row.get("quantity"), float("nan"))
        net = _finite(row.get("round_trip_net_pnl"), float("nan"))
        basis = _finite(row.get("entry_cost_basis"), float("nan"))
        if (row.get("side") != "SELL" or row.get("broker_mode") != "PAPER"
                or row.get("account_environment") != "PAPER"
                or not all(math.isfinite(v) for v in (qty, net, basis)) or qty <= 0 or basis <= 0):
            return None
        totals["quantity"] += qty
        totals["net_pnl"] += net
        totals["entry_cost_basis"] += basis
    return {**totals, "cost_provenance": "exact_fifo_round_trip_v1"}


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

    # Keep the realism module and oracle_bot's imported alias on the same
    # fee-exclusive model. Otherwise a pre-execution gate can evaluate a
    # different fill contract than the accounting wrapper actually executes.
    reality.simulate_fill = _simulate_fill_explicit_fee
    oracle_bot.simulate_fill = _simulate_fill_explicit_fee

    original_allocate_purchase = oracle_bot.allocate_purchase
    original_allocate_sale = oracle_bot.allocate_sale
    original_record_buy = oracle_bot._record_buy_attribution
    original_record_sell = oracle_bot._record_sell_attribution
    original_memory = oracle_bot.record_closed_trade_memory
    current_buy = oracle_bot._buy
    current_close = oracle_bot._execute_close_position

    # Keep SELL ledger fees exit-only and full-cost outcomes on one policy.
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
        rows = original_record_sell(conn, **kwargs)
        position = kwargs.get("position") or {}
        symbol = str(position.get("symbol") or kwargs.get("symbol") or "").upper().strip()
        context.setdefault("closed_outcomes", {})[(str(kwargs.get("market")), symbol)] = _closed_lot_memory_outcome(rows)
        return rows

    def fee_aware_memory(**kwargs: Any):
        context = _fee_context.get()
        outcome = context.get("closed_outcomes", {}).get((str(kwargs.get("market")), str(kwargs.get("symbol") or "").upper().strip()))
        quantity = _finite(kwargs.get("quantity"), float("nan"))
        if (outcome is None or not math.isfinite(quantity)
                or abs(quantity - outcome["quantity"]) > max(1e-9, abs(quantity) * 1e-9)):
            log.warning("Paper Brain outcome unavailable: missing exact full-cost FIFO close")
            return False
        kwargs["pnl"] = outcome["net_pnl"]
        kwargs["canonical_outcome"] = outcome
        recorded = original_memory(**kwargs)
        log.info("PAPER BRAIN COST OUTCOME | market=%s | symbol=%s | net_pnl=%.10f | entry_cost_basis=%.10f | recorded=%s | cost_provenance=exact_fifo_round_trip_v1",
                 kwargs.get("market"), kwargs.get("symbol"), outcome["net_pnl"], outcome["entry_cost_basis"], bool(recorded))
        return recorded

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

        token = _fee_context.set({"buy_fee_pct": buy_fee, "sell_fee_pct": sell_fee,
                                  "requested_notional": requested,
                                  "buy_reason": "rotation_in" if rotation_candidate else "entry"})
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
            return True
        finally:
            _fee_context.reset(token)

    oracle_bot._buy = accounting_buy
    oracle_bot._execute_close_position = accounting_close
    oracle_bot._paper_execution_accounting_installed = True
    log.info(
        "Installed explicit paper fee accounting and IOC partial-fill cap | brain_cost_provenance=exact_fifo_round_trip_v1 | max_participation=%.4f%%",
        min(0.01, max(0.0001, _env_float("PAPER_FILL_MAX_PARTICIPATION_PCT", 0.0025))) * 100.0,
    )
