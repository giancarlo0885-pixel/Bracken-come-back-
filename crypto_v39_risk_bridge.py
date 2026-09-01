from __future__ import annotations

import math
from typing import Any

import runtime_integrity_patch as patch
from quant_trade_standard import assess_trade


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def install_crypto_v39_risk_bridge(worker: Any) -> None:
    """Attach measured/canonical risk evidence before V39 capital qualification.

    Production OracleSignal objects already carry volatility_20d and atr_pct
    calculated from the source history. V39 requires finite risk evidence but the
    execution-path transformation currently drops it. This bridge reuses the
    existing quant_trade_standard risk model; it does not create an authorization
    and does not alter any downstream hard-risk, liquidity, concentration,
    reserve, margin, drawdown, quote, or execution gate.

    If the measured volatility/ATR inputs are unavailable or invalid, risk remains
    unknown and V39 continues to fail closed.
    """
    original = worker._v39_signal_opportunity
    if getattr(original, "_oracle_quant_risk_bridge", False):
        return

    def risk_aware_opportunity(
        market: str,
        signal: Any,
        prices: dict[str, Any],
        ranked_by_symbol: dict[str, dict[str, Any]],
        scan_type: str,
    ) -> dict[str, Any]:
        if (
            str(market or "").strip().lower() == "crypto"
            and patch._core_rebalance_intent(signal) == patch.CORE_REBALANCE_CANDIDATE_INTENT
            and _finite(patch._signal_value(signal, "risk_score", None)) is None
        ):
            volatility = _finite(patch._signal_value(signal, "volatility_20d", None))
            atr_pct = _finite(patch._signal_value(signal, "atr_pct", None))
            if volatility is not None and volatility >= 0 and atr_pct is not None and atr_pct >= 0:
                try:
                    portfolio, positions = worker._v39_position_rows(market)
                    equity = max(0.0, _finite(portfolio.get("equity") or portfolio.get("total_equity")) or 0.0)
                    symbol = str(patch._signal_value(signal, "symbol", "") or "").upper()
                    current_value = 0.0
                    for position in positions or []:
                        if str(position.get("symbol") or "").upper() != symbol:
                            continue
                        market_value = _finite(position.get("market_value"))
                        if market_value is None:
                            quantity = _finite(position.get("quantity")) or 0.0
                            current_price = _finite(position.get("current_price")) or 0.0
                            market_value = quantity * current_price
                        current_value += max(0.0, market_value or 0.0)
                    concentration = current_value / equity if equity > 0 else 0.0
                    assessment = assess_trade(
                        signal,
                        market="crypto",
                        portfolio_concentration=max(0.0, min(1.0, concentration)),
                    )
                    risk_score = _finite(assessment.risk_score)
                    if risk_score is not None:
                        setattr(signal, "risk_score", risk_score)
                        setattr(signal, "v39_risk_source", "quant_trade_standard")
                        setattr(signal, "v39_risk_assessment", assessment.to_dict())
                        worker.log.info(
                            "CRYPTO | V39 RISK EVIDENCE | symbol=%s | risk_score=%.4f | volatility_20d=%.6f | "
                            "atr_pct=%.6f | concentration=%.6f | source=quant_trade_standard",
                            symbol,
                            risk_score,
                            volatility,
                            atr_pct,
                            concentration,
                        )
                except Exception as exc:
                    worker.log.info(
                        "CRYPTO | V39 RISK EVIDENCE BLOCKED | symbol=%s | reason=%s",
                        str(patch._signal_value(signal, "symbol", "") or "").upper(),
                        exc.__class__.__name__,
                    )
            else:
                worker.log.info(
                    "CRYPTO | V39 RISK EVIDENCE BLOCKED | symbol=%s | reason=MEASURED_RISK_INPUTS_UNAVAILABLE",
                    str(patch._signal_value(signal, "symbol", "") or "").upper(),
                )

        return original(market, signal, prices, ranked_by_symbol, scan_type)

    risk_aware_opportunity._oracle_quant_risk_bridge = True
    worker._v39_signal_opportunity = risk_aware_opportunity
