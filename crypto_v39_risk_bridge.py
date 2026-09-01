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


def _quant_safety_to_v39_risk(safety_score: Any) -> float | None:
    """Convert quant_trade_standard safety (high=good) to V39 risk (low=good)."""
    safety = _finite(safety_score)
    if safety is None:
        return None
    safety = max(0.0, min(100.0, safety))
    return 100.0 - safety


def install_crypto_v39_risk_bridge(worker: Any) -> None:
    """Attach measured/canonical risk evidence before V39 capital qualification.

    Production OracleSignal objects carry volatility_20d and atr_pct calculated
    from source history. quant_trade_standard converts those into a 0..100 safety
    score where higher is safer. V39/global-pit uses the inverse convention for
    ``risk_score``: 0 is lowest risk and 100 is highest risk. The bridge therefore
    converts ``v39_risk = 100 - quant_safety`` while preserving the original
    assessment as audit evidence.

    This repairs metadata semantics only. It does not create an authorization and
    does not alter downstream hard-risk, liquidity, concentration, reserve,
    margin, drawdown, quote, or execution gates. If measured volatility/ATR inputs
    are unavailable or invalid, risk remains unknown and V39 fails closed.
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
                    quant_safety_score = _finite(assessment.risk_score)
                    risk_score = _quant_safety_to_v39_risk(quant_safety_score)
                    if risk_score is not None:
                        setattr(signal, "risk_score", risk_score)
                        setattr(signal, "v39_risk_source", "quant_trade_standard:inverse_safety")
                        setattr(signal, "v39_quant_safety_score", quant_safety_score)
                        setattr(signal, "v39_risk_assessment", assessment.to_dict())
                        worker.log.info(
                            "CRYPTO | V39 RISK EVIDENCE | symbol=%s | risk_score=%.4f | quant_safety=%.4f | "
                            "volatility_20d=%.6f | atr_pct=%.6f | concentration=%.6f | "
                            "source=quant_trade_standard:inverse_safety",
                            symbol,
                            risk_score,
                            quant_safety_score,
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
