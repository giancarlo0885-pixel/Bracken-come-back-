from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
import logging
import math
import os
from typing import Any

from config import CRYPTO_CORE_WEIGHTS
import runtime_integrity_patch as patch


log = logging.getLogger("optimizer-repair")
_INSTALLED = False
_CORE_TARGET = ContextVar("oracle_core_rebalance_target", default=0.0)
_CORE_APPROVED_DOLLAR_VOLUME = ContextVar("oracle_core_rebalance_dollar_volume", default=0.0)
_LAST_CORE_FALLBACK: tuple[str, ...] = ()
_LAST_VALUE_REPAIR: tuple[str, ...] = ()


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() == "true"


def _paper_only() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _core_aware_positions(positions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """Conservatively classify configured core holdings when the aggregate row lacks a bucket."""
    repaired: list[dict[str, Any]] = []
    fallback: set[str] = set()
    core_symbols = {str(symbol).upper().strip() for symbol in CRYPTO_CORE_WEIGHTS}
    for position in positions or []:
        item = dict(position)
        symbol = str(item.get("symbol") or "").upper().strip()
        explicit_bucket = str(item.get("bucket") or "").strip()
        if symbol in core_symbols and not explicit_bucket:
            # The aggregate positions table historically did not persist lot-level
            # core/tactical attribution. Counting an already-held configured core
            # asset toward its target is the conservative choice because it avoids
            # duplicate strategic exposure when attribution is absent.
            item["bucket"] = "Core"
            fallback.add(symbol)
        repaired.append(item)
    return repaired, tuple(sorted(fallback))


def _core_rebalance_notional(
    capital_allocator_module: Any,
    decision: Any,
    kwargs: dict[str, Any],
    target: float,
) -> float:
    """Return the optimizer-approved strategic target clipped by hard capacity only.

    The paper-learning wrapper may intentionally emit a $2 sample notional after
    the tactical allocator rejects a low-confidence strategic candidate. Its
    derived capacity fields are not authoritative for an already-approved V39
    strategic allocation, so this path rebuilds hard capacity from configuration,
    portfolio state, and the liquidity evidence attached to the V39 approval.
    """
    target = max(0.0, _number(target))
    equity = max(0.0, _number(kwargs.get("equity")))
    cash = max(0.0, _number(kwargs.get("cash")))
    price = max(0.0, _number(kwargs.get("price")))
    if target <= 0 or equity <= 0 or cash <= 0 or price <= 0:
        return 0.0

    configured_reserve_pct = max(
        0.0,
        _number(getattr(capital_allocator_module, "CRYPTO_MIN_CASH_RESERVE_PCT", 0.0)),
    )
    reserve_required = max(
        equity * configured_reserve_pct,
        max(0.0, _number(getattr(decision, "reserve_required", 0.0))),
    )
    spendable_cash = max(0.0, cash - reserve_required)
    if bool(kwargs.get("buying_power_validated")) and kwargs.get("buying_power") is not None:
        spendable_cash = min(spendable_cash, max(0.0, _number(kwargs.get("buying_power"))))

    existing_position_value = max(0.0, _number(kwargs.get("existing_position_value")))
    configured_position_pct = max(
        0.0,
        _number(getattr(capital_allocator_module, "MAX_SINGLE_CRYPTO_TACTICAL_POSITION_PCT", 0.0)),
    )
    max_position_dollars = equity * configured_position_pct
    if max_position_dollars <= 0:
        max_position_dollars = max(0.0, _number(getattr(decision, "max_position_dollars", 0.0)))
    position_room = max(0.0, max_position_dollars - existing_position_value)

    current_exposure = max(0.0, _number(kwargs.get("current_exposure")))
    deployed_limit = max(
        0.0,
        equity * max(0.0, _number(getattr(capital_allocator_module, "MAX_TOTAL_DEPLOYED_PCT", 0.0))),
    )
    deployment_room = max(0.0, deployed_limit - current_exposure)

    # Prefer direct execution-call liquidity when present. If the tactical
    # allocator lost that field, reuse the exact average-dollar-volume evidence
    # from the V39 optimizer allocation that authorized this strategic target.
    dollar_volume = max(
        0.0,
        _number(kwargs.get("dollar_volume")),
        _number(_CORE_APPROVED_DOLLAR_VOLUME.get()),
    )
    participation_pct = max(
        0.0,
        _number(getattr(capital_allocator_module, "MAX_POSITION_VS_DAILY_DOLLAR_VOLUME_PCT", 0.0)),
    )
    participation_room = dollar_volume * participation_pct

    notional = min(
        target,
        spendable_cash,
        position_room,
        deployment_room,
        participation_room,
    )
    minimum = max(0.0, _number(getattr(capital_allocator_module, "MIN_TRADE_NOTIONAL", 0.0)))
    final = round(notional, 2) if notional >= minimum else 0.0
    if final <= 0:
        log.info(
            "CORE_OPTIMIZER_CAPACITY | symbol=%s | target=%.2f | reserve_room=%.2f | position_room=%.2f | "
            "deployment_room=%.2f | liquidity_room=%.2f | dollar_volume=%.2f | status=BLOCKED",
            str(kwargs.get("symbol") or "").upper(),
            target,
            spendable_cash,
            position_room,
            deployment_room,
            participation_room,
            dollar_volume,
        )
    return final


def install_optimizer_repairs(worker: Any) -> bool:
    """Repair strategic optimizer accounting and execution handoff in paper mode only."""
    global _INSTALLED
    if _INSTALLED:
        return True
    if not _paper_only():
        log.info("OPTIMIZER_REPAIR | active=False | reason=non_paper_or_live_controls_enabled")
        return False

    import capital_allocator
    import oracle_bot
    import strategic_core_rebalance_runtime as strategic_runtime

    original_position_rows = worker._v39_position_rows

    def core_aware_position_rows(market: str):
        global _LAST_CORE_FALLBACK
        portfolio, positions = original_position_rows(market)
        if str(market or "").strip().lower() != "crypto" or not _paper_only():
            return portfolio, positions
        repaired, fallback = _core_aware_positions(list(positions or []))
        if fallback and fallback != _LAST_CORE_FALLBACK:
            log.info(
                "CORE_OPTIMIZER_POSITION_REPAIR | symbols=%s | classification=fallback_core | source=aggregate_positions",
                ",".join(fallback),
            )
            _LAST_CORE_FALLBACK = fallback
        return portfolio, repaired

    worker._v39_position_rows = core_aware_position_rows

    # The core planner historically accepted a stored market_value=0 as an
    # authoritative value, so its quantity*price fallback was never reached.
    # Mark configured-core holdings from the current verified quote first, then
    # fall back to the aggregate current_price only when a current quote is absent.
    original_core_plan = strategic_runtime.crypto_core_rebalance_plan

    def core_value_aware_plan(
        quotes: dict[str, dict[str, Any]],
        portfolio: dict[str, Any],
        positions: list[dict[str, Any]],
    ):
        global _LAST_VALUE_REPAIR
        configured = {str(symbol).upper().strip() for symbol in CRYPTO_CORE_WEIGHTS}
        normalized: list[dict[str, Any]] = []
        repaired_symbols: set[str] = set()

        for position in positions or []:
            item = dict(position)
            symbol = str(item.get("symbol") or "").upper().strip()
            if symbol in configured:
                if not str(item.get("bucket") or "").strip():
                    item["bucket"] = "Core"

                quantity = max(0.0, _number(item.get("quantity")))
                quote = dict((quotes or {}).get(symbol) or {})
                price = max(0.0, _number(quote.get("price")))
                if price <= 0:
                    price = max(0.0, _number(item.get("current_price")))

                if quantity > 0 and price > 0:
                    marked_value = quantity * price
                    stored_value = max(0.0, _number(item.get("market_value")))
                    item["market_value"] = marked_value
                    item["current_price"] = price
                    if stored_value <= 0 or abs(stored_value - marked_value) > max(0.01, marked_value * 0.001):
                        repaired_symbols.add(symbol)
            normalized.append(item)

        repaired_tuple = tuple(sorted(repaired_symbols))
        if repaired_tuple and repaired_tuple != _LAST_VALUE_REPAIR:
            log.info(
                "CORE_OPTIMIZER_VALUE_REPAIR | symbols=%s | source=quantity_x_current_verified_price",
                ",".join(repaired_tuple),
            )
            _LAST_VALUE_REPAIR = repaired_tuple

        return original_core_plan(quotes, portfolio, normalized)

    strategic_runtime.crypto_core_rebalance_plan = core_value_aware_plan

    original_adaptive = oracle_bot.adaptive_capital_allocation

    def optimizer_aware_allocation(**kwargs: Any):
        decision = original_adaptive(**kwargs)
        target = max(0.0, _number(_CORE_TARGET.get()))
        if target <= 0 or not _paper_only():
            return decision

        notional = _core_rebalance_notional(capital_allocator, decision, kwargs, target)
        if notional <= 0:
            log.info(
                "CORE_OPTIMIZER_SIZE | symbol=%s | target=%.2f | adaptive=%.2f | final=0.00 | status=CAPACITY_BLOCKED",
                str(kwargs.get("symbol") or "").upper(),
                target,
                _number(getattr(decision, "calculated_notional", 0.0)),
            )
            return replace(
                decision,
                calculated_notional=0.0,
                calculated_quantity=0.0,
                approved=False,
                reason="CORE_REBALANCE_CAPACITY_BLOCKED",
            )

        price = max(0.0, _number(kwargs.get("price")))
        quantity = notional / price if price > 0 else 0.0
        cash_after = max(0.0, _number(kwargs.get("cash")) - notional)
        log.info(
            "CORE_OPTIMIZER_SIZE | symbol=%s | target=%.2f | adaptive=%.2f | final=%.2f | status=PASS",
            str(kwargs.get("symbol") or "").upper(),
            target,
            _number(getattr(decision, "calculated_notional", 0.0)),
            notional,
        )
        return replace(
            decision,
            calculated_notional=notional,
            calculated_quantity=round(quantity, 10),
            cash_after_trade=round(cash_after, 2),
            approved=True,
            reason="CORE_REBALANCE_OPTIMIZER_TARGET",
        )

    oracle_bot.adaptive_capital_allocation = optimizer_aware_allocation

    original_buy = oracle_bot._buy

    def optimizer_aware_buy(
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
        intent = patch._core_rebalance_intent(signal)
        source = str(patch._signal_value(signal, "core_rebalance_source", "") or "")
        target = max(
            0.0,
            _number(
                target_trade_value,
                _number(patch._signal_value(signal, "v39_optimizer_approved_amount", 0.0)),
            ),
        )
        strategic_target = (
            target
            if intent == patch.CORE_REBALANCE_BUY_INTENT
            and source == "configured_core_allocation_gap"
            else 0.0
        )
        allocation = patch._signal_value(signal, "v39_optimizer_allocation", {}) or {}
        liquidity = allocation.get("liquidity") if isinstance(allocation, dict) else {}
        liquidity = liquidity if isinstance(liquidity, dict) else {}
        approved_dollar_volume = max(0.0, _number(liquidity.get("average_dollar_volume")))

        target_token = _CORE_TARGET.set(strategic_target)
        liquidity_token = _CORE_APPROVED_DOLLAR_VOLUME.set(approved_dollar_volume)
        try:
            if strategic_target > 0:
                # Persist explicit core attribution for new strategic lots so
                # subsequent optimizer passes can value them without inference.
                patch._set_signal_value(signal, "bucket", "Core")
                patch._set_signal_value(signal, "core_bucket", "Core")
                log.info(
                    "CORE_OPTIMIZER_HANDOFF | market=%s | symbol=%s | approved_target=%.2f | approved_dollar_volume=%.2f | mode=paper",
                    market,
                    symbol,
                    strategic_target,
                    approved_dollar_volume,
                )
            return original_buy(
                market,
                symbol,
                price,
                signal,
                quant_assessment=quant_assessment,
                target_trade_value=target_trade_value,
                rotation_candidate=rotation_candidate,
                verified_quote=verified_quote,
                rotation_verified_quote=rotation_verified_quote,
            )
        finally:
            _CORE_APPROVED_DOLLAR_VOLUME.reset(liquidity_token)
            _CORE_TARGET.reset(target_token)

    oracle_bot._buy = optimizer_aware_buy
    _INSTALLED = True
    log.info(
        "OPTIMIZER_REPAIR | active=True | fixes=strategic_target_handoff,core_position_accounting,core_value_fallback,approved_liquidity_handoff | live_trading=DISARMED"
    )
    return True
