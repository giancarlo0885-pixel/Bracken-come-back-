from __future__ import annotations

import math
import os
from typing import Any

import runtime_integrity_patch as patch


_ENTRY_ACTIONS = {"BUY", "STRONG_BUY", "STRONG BUY", "ACCUMULATE", "LONG"}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _strict_paper_learning() -> bool:
    return bool(
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy(os.getenv("PAPER_AUTONOMOUS_LEARNING", "false"))
        and not _truthy(os.getenv("ENABLE_BROKER_SUBMISSION", "false"))
        and not _truthy(os.getenv("LIVE_TRADING_ARMED", "false"))
    )


def _unbounded_paper_learning() -> bool:
    return _strict_paper_learning() and _truthy(os.getenv("PAPER_UNBOUNDED_LEARNING", "false"))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _signal_action(signal: Any) -> str:
    return str(patch._signal_value(signal, "action", "") or "").upper().strip()


def _hard_execution_evidence(
    worker: Any,
    opportunity: dict[str, Any],
    signal: Any,
    prices: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
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
    return hard_evidence_ok, {
        "symbol": symbol,
        "quote_verified": quote_verified,
        "identity_verified": identity_verified,
        "execution_fresh": execution_fresh,
        "tradeable": tradeable,
        "liquidity": liquidity,
        "spread": spread,
        "risk": risk,
        "signal_id": signal_id,
        "forecast_id": forecast_id,
    }


def _install_unbounded_optimizer_policy() -> None:
    if not _unbounded_paper_learning():
        return

    import global_adaptive_engine as adaptive
    import strategic_rebalance_optimizer_bridge as bridge

    original_hard_gate = adaptive.hard_risk_gate
    if not getattr(original_hard_gate, "_paper_unbounded_learning_gate", False):
        def paper_unbounded_hard_gate(item: dict[str, Any]) -> dict[str, Any]:
            gate = dict(original_hard_gate(item) or {})
            if not _unbounded_paper_learning():
                return gate
            action = str(item.get("action") or item.get("tactical_action") or "").upper().strip()
            explicit_core = bool(
                item.get("core_rebalance_candidate") is True
                and str(item.get("portfolio_intent") or "").upper().strip()
                in {patch.CORE_REBALANCE_CANDIDATE_INTENT, patch.CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT}
            )
            if action not in _ENTRY_ACTIONS and not explicit_core:
                return gate
            observed = [str(reason) for reason in (gate.get("reasons") or [])]
            return {
                **gate,
                "allowed": True,
                "reasons": [],
                "paper_observed_risk_reasons": observed,
                "authorization_basis": "paper_unbounded_learning",
            }

        paper_unbounded_hard_gate._paper_unbounded_learning_gate = True  # type: ignore[attr-defined]
        adaptive.hard_risk_gate = paper_unbounded_hard_gate

    # Remove portfolio throttles in the isolated paper process. These values are
    # process-local and do not alter the stock worker or any live broker policy.
    adaptive.GLOBAL_PIT_RESERVE_PCT = 0.0
    adaptive.GLOBAL_PIT_TARGET_INVESTED_PCT = 1.0
    adaptive.GLOBAL_PIT_MAX_POSITION_PCT = 1.0
    adaptive.MAX_SECTOR_EXPOSURE_PCT = 1.0

    original_floor = bridge._adaptive_meaningful_entry_floor
    if not getattr(original_floor, "_paper_unbounded_learning_floor", False):
        def paper_unbounded_floor(item: dict[str, Any], *, equity: float, minimum_notional: float) -> float:
            if not _unbounded_paper_learning():
                return original_floor(item, equity=equity, minimum_notional=minimum_notional)
            configured = _finite(os.getenv("PAPER_LEARNING_MIN_NOTIONAL", minimum_notional))
            return round(max(float(minimum_notional), float(configured or minimum_notional)), 2)

        paper_unbounded_floor._paper_unbounded_learning_floor = True  # type: ignore[attr-defined]
        bridge._adaptive_meaningful_entry_floor = paper_unbounded_floor


def install_paper_core_rebalance_qualification(worker: Any) -> None:
    """Open valid crypto BUY samples for autonomous paper learning.

    Bounded paper mode keeps the historical core-rebalance forecast exception.
    Unbounded paper mode additionally treats model qualification and hard-risk
    thresholds as observations rather than vetoes for genuine BUY signals, while
    still requiring verified/fresh identity-correct quotes, tradeability,
    liquidity, finite risk evidence and a real signal id. Invalid/missing market
    data remains fail-closed because fabricated fills would corrupt learning.
    """
    _install_unbounded_optimizer_policy()

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

        target_amount = _finite(patch._signal_value(signal, "core_target_amount", None))
        if target_amount is not None and target_amount > 0:
            opportunity["core_target_amount"] = target_amount
            opportunity["core_target_weight"] = patch._signal_value(signal, "core_target_weight", None)
            opportunity["core_current_value"] = _finite(patch._signal_value(signal, "core_current_value", None)) or 0.0
            opportunity["core_rebalance_candidate"] = True
            opportunity["portfolio_intent"] = patch._core_rebalance_intent(signal)
            opportunity["tactical_action"] = _signal_action(signal)

        if opportunity.get("qualified_for_capital") is True:
            return opportunity

        hard_evidence_ok, evidence = _hard_execution_evidence(worker, opportunity, signal, prices)
        action = _signal_action(signal)

        if _unbounded_paper_learning() and action in _ENTRY_ACTIONS and hard_evidence_ok:
            opportunity["qualified_for_capital"] = True
            opportunity["paper_unbounded_learning"] = True
            opportunity["capital_qualification_basis"] = "paper_unbounded_valid_buy_signal"
            stages = list(opportunity.get("stages") or [])
            if "paper_unbounded_learning" not in stages:
                stages.append("paper_unbounded_learning")
            opportunity["stages"] = stages
            worker.log.info(
                "PAPER UNBOUNDED QUALIFICATION | symbol=%s | action=%s | qualified=True | "
                "quote_verified=True | identity_verified=True | execution_fresh=True | tradeable=True | "
                "liquidity=%.2f | spread_pct=%.6f | risk_score=%.4f | model_veto=OBSERVE_ONLY | "
                "broker_submission=NONE | live_trading=DISARMED",
                evidence["symbol"],
                action,
                evidence["liquidity"],
                evidence["spread"],
                evidence["risk"],
            )
            return opportunity

        intent = patch._core_rebalance_intent(signal)
        if intent not in {
            patch.CORE_REBALANCE_CANDIDATE_INTENT,
            patch.CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT,
        }:
            return opportunity

        # Existing bounded-paper exception: only forecast authorization may be
        # absent; all hard execution evidence must still be present.
        if not hard_evidence_ok or evidence["forecast_id"]:
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
            evidence["symbol"],
            evidence["liquidity"],
            evidence["spread"],
            evidence["risk"],
        )
        return opportunity

    paper_core_opportunity._oracle_paper_core_forecast_optional = True
    worker._v39_signal_opportunity = paper_core_opportunity
