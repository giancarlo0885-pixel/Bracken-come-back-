from __future__ import annotations

from typing import Any

from global_adaptive_engine import capital_engine_state
from runtime_integrity_patch import (
    CORE_REBALANCE_BUY_INTENT,
    CORE_REBALANCE_CANDIDATE_INTENT,
    _core_rebalance_intent,
    _numeric,
    _signal_value,
)


_ENTRY_ACTIONS = {"BUY", "STRONG_BUY", "STRONG BUY", "ACCUMULATE", "LONG"}


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def install_core_rebalance_observability(worker: Any) -> None:
    """Expose why crypto opportunities do or do not reach capital deployment.

    This wrapper is read-only. It does not modify signals, opportunities,
    allocations, thresholds, execution mode, or any safety gate.
    """
    original = worker._v39_prioritize_signals
    if getattr(original, "_oracle_core_rebalance_observed", False):
        return

    def observed_prioritize(
        market: str,
        signals: list[Any],
        prices: dict[str, Any],
        ranked: list[dict[str, Any]],
        scan_type: str,
    ) -> list[Any]:
        ordered = original(market, signals, prices, ranked, scan_type)
        if str(market or "").strip().lower() != "crypto":
            return ordered

        try:
            portfolio, positions = worker._v39_position_rows(market)
            state = capital_engine_state(portfolio, positions, [], "crypto")
            deployment_gap = max(0.0, _numeric(state.get("deployment_gap")))
        except Exception:
            deployment_gap = 0.0

        candidates = [
            signal for signal in signals or []
            if _core_rebalance_intent(signal) in {CORE_REBALANCE_CANDIDATE_INTENT, CORE_REBALANCE_BUY_INTENT}
        ]
        buys = [signal for signal in candidates if _core_rebalance_intent(signal) == CORE_REBALANCE_BUY_INTENT]
        decisions = dict(getattr(worker, "_core_rebalance_optimizer_decisions", {}) or {})

        entry_signals = [
            signal for signal in signals or []
            if _upper(_signal_value(signal, "action", "")) in _ENTRY_ACTIONS
        ]

        if candidates or entry_signals or deployment_gap > 0:
            if candidates:
                sample_source = candidates
            elif entry_signals:
                sample_source = sorted(
                    entry_signals,
                    key=lambda signal: _numeric(_signal_value(signal, "score", 0.0)),
                    reverse=True,
                )[:5]
            else:
                sample_source = sorted(
                    list(signals or []),
                    key=lambda signal: _numeric(_signal_value(signal, "score", 0.0)),
                    reverse=True,
                )[:5]

            sample = []
            for signal in sample_source[:5]:
                symbol = str(_signal_value(signal, "symbol", "") or "").upper()
                action = _upper(_signal_value(signal, "action", ""))
                intent = _core_rebalance_intent(signal)
                quote = dict((prices or {}).get(symbol) or {})
                decision = dict(decisions.get(symbol) or {})

                if not decision and action in _ENTRY_ACTIONS:
                    decision = {
                        "status": "NOT_ROUTED",
                        "reason": (
                            "not_routed_to_capital_optimizer"
                            if intent not in {CORE_REBALANCE_CANDIDATE_INTENT, CORE_REBALANCE_BUY_INTENT}
                            else "optimizer_decision_not_observed"
                        ),
                    }

                sample.append(
                    {
                        "symbol": symbol,
                        "action": action,
                        "score": _numeric(_signal_value(signal, "score", 0.0)),
                        "confidence": _numeric(_signal_value(signal, "confidence", 0.0)),
                        "intent": intent,
                        "signal_id": bool(_signal_value(signal, "signal_id", None)),
                        "forecast_id": bool(_signal_value(signal, "forecast_id", None)),
                        "approved_amount": _numeric(_signal_value(signal, "v39_optimizer_approved_amount", 0.0)),
                        "optimizer_status": decision.get("status"),
                        "optimizer_reason": decision.get("reason"),
                        "optimizer_risk_reasons": decision.get("risk_reasons") or [],
                        "optimizer_capacity_reason": decision.get("capacity_reason"),
                        "optimizer_watch_only": bool(decision.get("watch_only")),
                        "optimizer_proposed_amount": decision.get("proposed_amount"),
                        "meaningful_entry_floor": decision.get("meaningful_entry_floor"),
                        "entry_floor_mode": decision.get("entry_floor_mode"),
                        "quote_verified": quote.get("quote_verified") is True,
                        "tradeable": quote.get("tradeable") is True,
                        "spread_pct": quote.get("spread_pct"),
                        "liquidity": quote.get("avg_dollar_volume"),
                    }
                )
            worker.log.info(
                "CORE_REBALANCE_TRACE | scan=%s | deployment_gap=%.2f | signals=%d | candidates=%d | buys=%d | entry_signals=%d | sample=%s",
                scan_type,
                deployment_gap,
                len(signals or []),
                len(candidates),
                len(buys),
                len(entry_signals),
                sample,
            )
        return ordered

    observed_prioritize._oracle_core_rebalance_observed = True
    worker._v39_prioritize_signals = observed_prioritize
