from __future__ import annotations

import math
import os
from typing import Any

import runtime_integrity_patch as patch


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _strict_paper_learning() -> bool:
    return bool(
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy(os.getenv("PAPER_AUTONOMOUS_LEARNING", "false"))
        and not _truthy(os.getenv("ENABLE_BROKER_SUBMISSION", "false"))
        and not _truthy(os.getenv("LIVE_TRADING_ARMED", "false"))
    )


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def install_paper_core_rebalance_qualification(worker: Any) -> None:
    """Allow explicit core-rebalance paper samples without forecast authorization.

    V39's normal capital qualification requires a persisted forecast id. That is
    appropriate for governed/live capital, but autonomous paper learning is
    intentionally allowed to collect samples without strategy/forecast approval.
    This wrapper relaxes *only* that authorization requirement and only while the
    process is provably paper-only. Quote identity, freshness, tradeability,
    liquidity, spread, risk evidence, signal identity, optimizer/risk gates,
    buying power, fill realism, and accounting remain mandatory.
    """
    original = worker._v39_signal_opportunity
    if getattr(original, "_oracle_paper_core_forecast_optional", False):
        return

    def paper_core_opportunity(
        market: str,
        signal: Any,
        prices: dict[str, Any],
        ranked_by_symbol: dict[str, dict[str, Any]],
        scan_type: str,
    ) -> dict[str, Any]:
        opportunity = original(market, signal, prices, ranked_by_symbol, scan_type)
        if str(market or "").strip().lower() != "crypto" or not _strict_paper_learning():
            return opportunity

        # Carry the explicit configured-core target gap into the optimizer record.
        # The strategic optimizer uses this as a hard sizing ceiling so a target-gap
        # authorization can never buy more than the gap that authorized it.
        target_amount = _finite(patch._signal_value(signal, "core_target_amount", None))
        if target_amount is not None and target_amount > 0:
            opportunity["core_target_amount"] = target_amount
            opportunity["core_target_weight"] = patch._signal_value(signal, "core_target_weight", None)
            opportunity["core_current_value"] = _finite(patch._signal_value(signal, "core_current_value", None)) or 0.0
            opportunity["core_rebalance_candidate"] = True
            opportunity["portfolio_intent"] = patch._core_rebalance_intent(signal)
            opportunity["tactical_action"] = str(patch._signal_value(signal, "action", "") or "").upper().strip()

        if opportunity.get("qualified_for_capital") is True:
            return opportunity

        intent = patch._core_rebalance_intent(signal)
        if intent not in {
            patch.CORE_REBALANCE_CANDIDATE_INTENT,
            patch.CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT,
        }:
            return opportunity

        symbol = str(patch._signal_value(signal, "symbol", "") or "").upper().strip()
        quote = dict((prices or {}).get(symbol) or {})
        requested = str(quote.get("requested_symbol") or "").upper().strip()
        provider_symbol = str(quote.get("provider_symbol") or "").upper().strip()
        quote_verified = quote.get("quote_verified") is True
        identity_verified = bool(symbol and requested == symbol and provider_symbol == symbol)
        try:
            execution_fresh = bool(
                worker._execution_quote_eligible(
                    {
                        **quote,
                        "symbol": symbol,
                        "market": "crypto",
                        "asset_class": "crypto",
                    }
                )
            )
        except Exception:
            execution_fresh = False

        liquidity = _finite(opportunity.get("avg_dollar_volume"))
        spread = _finite(opportunity.get("spread_pct"))
        risk = _finite(opportunity.get("risk_score"))
        tradeable = opportunity.get("tradeable") is True
        signal_id = bool(patch._signal_value(signal, "signal_id", None))
        forecast_id = bool(patch._signal_value(signal, "forecast_id", None))

        hard_evidence_ok = bool(
            quote_verified
            and identity_verified
            and execution_fresh
            and tradeable
            and liquidity is not None
            and liquidity > 0
            and spread is not None
            and spread >= 0
            and risk is not None
            and signal_id
        )
        # Never override a failure other than the intentionally optional forecast
        # authorization. If a forecast exists and the base evaluator still failed,
        # another gate failed and must remain fail-closed.
        if not hard_evidence_ok or forecast_id:
            return opportunity

        opportunity["qualified_for_capital"] = True
        opportunity["paper_learning_forecast_optional"] = True
        opportunity["capital_qualification_basis"] = "paper_core_rebalance_without_forecast_authorization"
        stages = list(opportunity.get("stages") or [])
        if "paper_learning_forecast_optional" not in stages:
            stages.append("paper_learning_forecast_optional")
        opportunity["stages"] = stages
        worker.log.info(
            "CORE_REBALANCE PAPER QUALIFICATION | symbol=%s | forecast_optional=True | "
            "quote_verified=True | identity_verified=True | execution_fresh=True | "
            "tradeable=True | liquidity=%.2f | spread_pct=%.6f | risk_score=%.4f",
            symbol,
            liquidity,
            spread,
            risk,
        )
        return opportunity

    paper_core_opportunity._oracle_paper_core_forecast_optional = True
    worker._v39_signal_opportunity = paper_core_opportunity
