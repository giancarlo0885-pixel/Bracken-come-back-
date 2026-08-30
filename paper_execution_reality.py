from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
import math
import os
from typing import Any


log = logging.getLogger("paper-execution-reality")


def _finite(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _non_negative(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number >= 0 else None


def _env_bps(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default)))) / 10_000.0
    except (TypeError, ValueError):
        return max(0.0, default) / 10_000.0


def _market_defaults(market: str) -> dict[str, float]:
    crypto = str(market or "").lower() == "crypto"
    return {
        # Match the assumptions already used by quant_trade_standard when richer
        # quote data is absent, so pre-trade scoring and paper execution do not
        # use contradictory friction assumptions.
        "spread_pct": 0.0018 if crypto else 0.0008,
        "slippage_pct": 0.00135 if crypto else 0.0006,
        "fee_pct": 0.0010 if crypto else 0.0002,
        "latency_pct": _env_bps(
            "PAPER_CRYPTO_LATENCY_COST_BPS" if crypto else "PAPER_STOCK_LATENCY_COST_BPS",
            3.0 if crypto else 1.0,
        ),
    }


@dataclass(frozen=True)
class SimulatedFill:
    side: str
    market: str
    reference_price: float
    executable_reference: float
    fill_price: float
    spread_pct: float
    slippage_pct: float
    fee_pct: float
    market_impact_pct: float
    latency_pct: float
    adverse_cost_pct: float
    participation_rate: float | None
    liquidity_value: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _spread_pct(reference_price: float, quote: dict[str, Any], default: float) -> float:
    explicit = _non_negative(quote.get("spread_pct"))
    if explicit is not None:
        return explicit
    bid = _positive(quote.get("bid"))
    ask = _positive(quote.get("ask"))
    if bid is not None and ask is not None and ask >= bid:
        midpoint = (bid + ask) / 2.0
        if midpoint > 0:
            return max(0.0, (ask - bid) / midpoint)
    return default


def _liquidity_value(reference_price: float, quote: dict[str, Any]) -> float | None:
    for key in ("liquidity_value", "dollar_volume", "avg_dollar_volume", "average_dollar_volume"):
        value = _non_negative(quote.get(key))
        if value is not None:
            return value
    volume = _non_negative(quote.get("volume"))
    if volume is not None and reference_price > 0:
        return volume * reference_price
    return None


def _market_impact_pct(
    quote: dict[str, Any],
    *,
    order_value: float | None,
    liquidity_value: float | None,
) -> tuple[float, float | None]:
    explicit = _non_negative(quote.get("estimated_market_impact_pct"))
    participation: float | None = None
    notional = _positive(order_value)
    if notional is not None and liquidity_value is not None and liquidity_value > 0:
        participation = max(0.0, notional / liquidity_value)
    if explicit is not None:
        return explicit, participation
    if participation is None:
        return 0.0, None

    # Deterministic square-root market-impact approximation. At 1% market
    # participation this adds roughly 10 bps; at larger participation the
    # penalty rises non-linearly instead of granting fantasy full fills.
    coefficient = max(0.0, _finite(os.getenv("PAPER_MARKET_IMPACT_COEFFICIENT")) or 0.01)
    cap = max(0.0, _finite(os.getenv("PAPER_MAX_MARKET_IMPACT_PCT")) or 0.03)
    return min(cap, coefficient * math.sqrt(max(0.0, participation))), participation


def simulate_fill(
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
) -> SimulatedFill:
    """Return a deterministic, adverse paper fill.

    The model deliberately never improves on an executable bid/ask reference.
    Buys pay the ask (or half-spread proxy) plus slippage, fees, impact and a
    small latency penalty. Sells receive the bid (or half-spread proxy) minus
    those same frictions. This keeps paper performance conservative and fully
    reproducible for tests and backtests.
    """
    side_text = str(side or "").upper().strip()
    if side_text not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    reference = _positive(reference_price)
    if reference is None:
        raise ValueError("reference_price must be positive and finite")

    quote_data = dict(quote or {})
    defaults = _market_defaults(market)
    spread = max(
        0.0,
        _non_negative(spread_pct)
        if _non_negative(spread_pct) is not None
        else _spread_pct(reference, quote_data, defaults["spread_pct"]),
    )

    bid = _positive(quote_data.get("bid"))
    ask = _positive(quote_data.get("ask"))
    if side_text == "BUY":
        executable = max(reference, ask) if ask is not None else reference * (1.0 + spread / 2.0)
    else:
        executable = min(reference, bid) if bid is not None else reference * max(0.000001, 1.0 - spread / 2.0)

    slip_source = _non_negative(slippage_pct)
    if slip_source is None:
        slip_source = _non_negative(quote_data.get("estimated_slippage_pct"))
    if slip_source is None:
        slip_source = _non_negative(quote_data.get("slippage_pct"))
    slippage = defaults["slippage_pct"] if slip_source is None else slip_source

    fee_source = _non_negative(fee_pct)
    if fee_source is None:
        fee_source = _non_negative(quote_data.get("estimated_fees_pct"))
    fee = defaults["fee_pct"] if fee_source is None else fee_source

    liquidity = _liquidity_value(reference, quote_data)
    derived_impact, participation = _market_impact_pct(
        quote_data,
        order_value=order_value,
        liquidity_value=liquidity,
    )
    impact_source = _non_negative(market_impact_pct)
    impact = derived_impact if impact_source is None else impact_source

    latency_source = _non_negative(latency_pct)
    latency = defaults["latency_pct"] if latency_source is None else latency_source

    max_adverse = max(0.0, _finite(os.getenv("PAPER_MAX_ADVERSE_FILL_PCT")) or 0.05)
    additive = min(max_adverse, slippage + fee + impact + latency)
    if side_text == "BUY":
        fill = executable * (1.0 + additive)
    else:
        fill = executable * max(0.000001, 1.0 - additive)

    adverse = abs(fill - reference) / reference
    return SimulatedFill(
        side=side_text,
        market=str(market or "").lower(),
        reference_price=reference,
        executable_reference=round(executable, 12),
        fill_price=round(fill, 12),
        spread_pct=round(spread, 10),
        slippage_pct=round(slippage, 10),
        fee_pct=round(fee, 10),
        market_impact_pct=round(impact, 10),
        latency_pct=round(latency, 10),
        adverse_cost_pct=round(adverse, 10),
        participation_rate=round(participation, 10) if participation is not None else None,
        liquidity_value=round(liquidity, 8) if liquidity is not None else None,
    )


def _signal_value(signal: Any, name: str, default: Any = None) -> Any:
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)


def _execution_quote(
    *,
    market: str,
    side: str,
    reference_price: float,
    quote: dict[str, Any] | None,
    signal: Any | None = None,
    order_value: float | None = None,
) -> tuple[dict[str, Any], SimulatedFill]:
    data = dict(quote or {})
    for key in (
        "estimated_slippage_pct",
        "slippage_pct",
        "estimated_fees_pct",
        "estimated_market_impact_pct",
        "spread_pct",
        "volume",
        "liquidity_value",
        "dollar_volume",
        "avg_dollar_volume",
        "average_dollar_volume",
    ):
        if data.get(key) in (None, "") and signal is not None:
            value = _signal_value(signal, key, None)
            if value not in (None, ""):
                data[key] = value
    fill = simulate_fill(
        side=side,
        market=market,
        reference_price=reference_price,
        quote=data,
        order_value=order_value,
    )
    data["paper_reference_price"] = fill.reference_price
    data["paper_executable_reference"] = fill.executable_reference
    data["paper_fill_price"] = fill.fill_price
    data["paper_adverse_cost_pct"] = fill.adverse_cost_pct
    data["paper_market_impact_pct"] = fill.market_impact_pct
    data["paper_participation_rate"] = fill.participation_rate
    # oracle_bot's final execution guard requires metadata.price to match the
    # actual execution price. Pre-trade signal/forecast validation already ran
    # against the unmodified provider quote before this final fill step.
    data["price"] = fill.fill_price
    return data, fill


def install_paper_execution_reality(market_worker_module: Any | None = None) -> None:
    """Patch the final paper mutation points without weakening quote gates.

    market_worker imports process_signals/risk_exits from oracle_bot by value,
    but those functions resolve _buy/_execute_close_position in oracle_bot's
    module globals at call time. Replacing only those final mutation functions
    preserves every existing provider, forecast and risk gate while making the
    resulting simulated fill economically adverse.
    """
    import oracle_bot

    if getattr(oracle_bot, "_paper_execution_reality_installed", False):
        return

    original_buy = oracle_bot._buy
    original_close = oracle_bot._execute_close_position

    def realistic_buy(
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
        estimated_notional = _positive(target_trade_value)
        if estimated_notional is None:
            estimated_notional = _positive(
                _signal_value(
                    signal,
                    "planned_trade_value",
                    _signal_value(signal, "v39_optimizer_approved_amount", None),
                )
            )
        adjusted_quote, incoming_fill = _execution_quote(
            market=market,
            side="BUY",
            reference_price=price,
            quote=verified_quote,
            signal=signal,
            order_value=estimated_notional,
        )

        adjusted_rotation = dict(rotation_candidate) if rotation_candidate else None
        adjusted_rotation_quote = dict(rotation_verified_quote or {}) if rotation_verified_quote else None
        outgoing_fill: SimulatedFill | None = None
        if adjusted_rotation:
            source = adjusted_rotation_quote or dict(adjusted_rotation.get("_verified_rotation_quote") or {})
            reference = _positive(source.get("price"))
            quantity = _positive(adjusted_rotation.get("quantity"))
            if reference is not None:
                source, outgoing_fill = _execution_quote(
                    market=market,
                    side="SELL",
                    reference_price=reference,
                    quote=source,
                    order_value=(quantity * reference) if quantity is not None else None,
                )
                adjusted_rotation_quote = source
                adjusted_rotation["_verified_rotation_quote"] = source
                action = dict(adjusted_rotation.get("_rotation_action") or {})
                if action:
                    action["paper_reference_price"] = reference
                    action["price"] = outgoing_fill.fill_price
                    adjusted_rotation["_rotation_action"] = action

        result = original_buy(
            market,
            symbol,
            incoming_fill.fill_price,
            signal,
            quant_assessment=quant_assessment,
            target_trade_value=target_trade_value,
            rotation_candidate=adjusted_rotation,
            verified_quote=adjusted_quote,
            rotation_verified_quote=adjusted_rotation_quote,
        )
        if result and bool(result[0]):
            log.info(
                "PAPER_FILL | side=BUY | market=%s | symbol=%s | reference=%.8f | fill=%.8f | adverse=%.4f%% | impact=%.4f%% | participation=%s",
                market,
                symbol,
                incoming_fill.reference_price,
                incoming_fill.fill_price,
                incoming_fill.adverse_cost_pct * 100.0,
                incoming_fill.market_impact_pct * 100.0,
                "n/a" if incoming_fill.participation_rate is None else f"{incoming_fill.participation_rate:.6f}",
            )
            if outgoing_fill is not None:
                log.info(
                    "PAPER_FILL | side=SELL | market=%s | symbol=%s | reference=%.8f | fill=%.8f | adverse=%.4f%% | reason=rotation",
                    market,
                    str(adjusted_rotation.get("symbol") or ""),
                    outgoing_fill.reference_price,
                    outgoing_fill.fill_price,
                    outgoing_fill.adverse_cost_pct * 100.0,
                )
        return result

    def realistic_close(
        market: str,
        position: dict[str, Any],
        price: float,
        reason: str,
        quote_metadata: dict[str, Any] | None = None,
    ) -> bool:
        quantity = _positive(position.get("quantity"))
        adjusted_quote, fill = _execution_quote(
            market=market,
            side="SELL",
            reference_price=price,
            quote=quote_metadata,
            order_value=(quantity * price) if quantity is not None else None,
        )
        result = original_close(
            market,
            position,
            fill.fill_price,
            reason,
            quote_metadata=adjusted_quote,
        )
        if result:
            log.info(
                "PAPER_FILL | side=SELL | market=%s | symbol=%s | reference=%.8f | fill=%.8f | adverse=%.4f%% | impact=%.4f%% | reason=%s",
                market,
                str(position.get("symbol") or ""),
                fill.reference_price,
                fill.fill_price,
                fill.adverse_cost_pct * 100.0,
                fill.market_impact_pct * 100.0,
                reason,
            )
        return result

    oracle_bot._buy = realistic_buy
    oracle_bot._execute_close_position = realistic_close
    oracle_bot._paper_execution_reality_installed = True
    log.info("Installed deterministic paper execution realism layer")
