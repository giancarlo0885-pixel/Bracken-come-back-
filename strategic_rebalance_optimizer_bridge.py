from __future__ import annotations

from typing import Any

import global_adaptive_engine as adaptive
import runtime_integrity_patch as patch
from config import MIN_TRADE_VALUE


_TACTICAL_AUTHORIZATION_REASONS = {
    "at least three core signals must support the trade",
    "confidence below trade threshold",
}
_STRATEGIC_ENTRY_ACTIONS = {"HOLD", "BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}
_MEANINGFUL_ENTRY_PCT = 0.01


def _explicit_strategic_rebalance(item: dict[str, Any]) -> bool:
    return bool(
        item.get("core_rebalance_candidate") is True
        and str(item.get("portfolio_intent") or "").strip().upper() == patch.CORE_REBALANCE_CANDIDATE_INTENT
        and str(item.get("tactical_action") or "").strip().upper() in _STRATEGIC_ENTRY_ACTIONS
    )


def _strategic_rebalance_gate(item: dict[str, Any]) -> dict[str, Any]:
    """Reuse the existing hard gate while separating tactical authorization.

    Strategic core rebalance authorization comes from an explicit configured
    portfolio target gap, not solely from a tactical vote. All hard-risk reasons
    remain blocking. Only the two tactical authorization checks (three-core-signal
    agreement and tactical confidence) may be waived, and only for an explicitly
    marked entry-compatible core-rebalance candidate. SELL/exit actions are never
    strategic rebalance candidates.
    """
    gate = adaptive.hard_risk_gate(item)
    if gate.get("allowed"):
        return gate
    if not _explicit_strategic_rebalance(item):
        return gate

    reasons = [str(reason) for reason in (gate.get("reasons") or [])]
    remaining = [reason for reason in reasons if reason not in _TACTICAL_AUTHORIZATION_REASONS]
    return {
        **gate,
        "allowed": not remaining,
        "reasons": remaining,
        "tactical_authorization_reasons": [reason for reason in reasons if reason in _TACTICAL_AUTHORIZATION_REASONS],
        "authorization_basis": "explicit_core_rebalance_target_gap" if not remaining else "blocked_by_execution_safety",
    }


def _entry_candidate(item: dict[str, Any]) -> bool:
    return str(item.get("action") or item.get("tactical_action") or "").strip().upper() in {
        "BUY",
        "STRONG_BUY",
        "STRONG BUY",
        "ACCUMULATE",
        "LONG",
    }


def _log_optimizer_decision(worker: Any, symbol: str, *, status: str, reason: str, **details: Any) -> None:
    """Expose capital decisions without changing eligibility, sizing, or execution."""
    rendered = " | ".join(f"{key}={value}" for key, value in details.items() if value is not None)
    worker.log.info(
        "CRYPTO_OPTIMIZER_DECISION | symbol=%s | status=%s | reason=%s%s",
        symbol or "missing",
        status,
        reason,
        f" | {rendered}" if rendered else "",
    )


def install_strategic_rebalance_optimizer_bridge(worker: Any) -> None:
    original = worker.adaptive_portfolio_optimizer
    if getattr(original, "_oracle_strategic_rebalance_gate", False):
        return

    def optimizer(
        opportunities: list[dict[str, Any]],
        portfolio: dict[str, Any],
        positions: list[dict[str, Any]],
        *,
        engine: str,
    ) -> dict[str, Any]:
        # Stock behavior remains exactly on the repository optimizer.
        if str(engine or "").strip().lower() != "crypto":
            return original(opportunities, portfolio, positions, engine=engine)

        state = adaptive.capital_engine_state(portfolio, positions, opportunities, engine)
        cash = state["cash"]
        equity = state["equity"]
        reserve = state["reserve_cash_required"]
        exposure_by_symbol = {
            adaptive._upper(p.get("symbol")): adaptive._finite(
                p.get("market_value")
                or adaptive._finite(p.get("quantity")) * adaptive._finite(p.get("current_price"))
            )
            for p in positions
        }
        sector_exposure: dict[str, float] = {}
        for position in positions:
            sector = str(position.get("sector") or "").strip() or "Unknown"
            sector_exposure[sector] = sector_exposure.get(sector, 0.0) + adaptive._finite(
                position.get("market_value")
                or adaptive._finite(position.get("quantity")) * adaptive._finite(position.get("current_price"))
            )

        allocations: list[dict[str, Any]] = []
        rejections: list[dict[str, Any]] = []
        recalc_count = 0
        minimum_notional = max(0.0, float(MIN_TRADE_VALUE))
        # Paper execution should learn from economically meaningful entries, not
        # penny-sized fills. A qualified idea below 1% of current paper equity is
        # retained as a momentum/watch candidate and is not sent to execution.
        meaningful_entry_floor = max(minimum_notional, equity * _MEANINGFUL_ENTRY_PCT)

        for item in sorted(
            opportunities,
            key=lambda row: adaptive._finite(row.get("soft_score") or row.get("opportunity_score")),
            reverse=True,
        ):
            symbol = adaptive._upper(item.get("symbol"))
            capital_engine = adaptive.classify_capital_engine(item)
            qualified = item.get("qualified_for_capital") is True
            if capital_engine != engine or not qualified:
                if _entry_candidate(item):
                    reason = "capital_engine_mismatch" if capital_engine != engine else "not_capital_qualified"
                    _log_optimizer_decision(
                        worker,
                        symbol,
                        status="REJECTED",
                        reason=reason,
                        engine=capital_engine,
                        qualified=qualified,
                        score=round(adaptive._finite(item.get("soft_score") or item.get("opportunity_score")), 4),
                        confidence=round(adaptive._finite(item.get("confidence")), 4),
                    )
                continue

            gate = _strategic_rebalance_gate(item)
            if not gate.get("allowed"):
                risk_reasons = gate.get("reasons") or []
                rejections.append({"symbol": symbol, "reason": "hard risk gate", "risk_reasons": risk_reasons})
                _log_optimizer_decision(
                    worker,
                    symbol,
                    status="REJECTED",
                    reason="hard_risk_gate",
                    risk_reasons=",".join(str(reason) for reason in risk_reasons) or "unspecified",
                )
                continue

            current = exposure_by_symbol.get(symbol, 0.0)
            max_position = equity * adaptive.GLOBAL_PIT_MAX_POSITION_PCT
            if current >= max_position:
                rejections.append({"symbol": symbol, "reason": "max position reached"})
                _log_optimizer_decision(
                    worker,
                    symbol,
                    status="REJECTED",
                    reason="max_position_reached",
                    current=round(current, 2),
                    max_position=round(max_position, 2),
                )
                continue

            candidate_amount = min(
                cash - reserve,
                max_position - current,
                equity * adaptive.GLOBAL_PIT_PREFERRED_POSITION_PCT,
            )

            # An explicit configured-core target gap is an authorization ceiling,
            # not merely a reason to enter. Never let the generic optimizer buy
            # more than the remaining gap that produced the authorization.
            strategic_target_gap = adaptive._finite(item.get("core_target_amount"))
            if _explicit_strategic_rebalance(item) and strategic_target_gap > 0:
                candidate_amount = min(candidate_amount, strategic_target_gap)

            if candidate_amount <= 0:
                rejections.append({"symbol": symbol, "reason": "no capital capacity"})
                _log_optimizer_decision(
                    worker,
                    symbol,
                    status="REJECTED",
                    reason="no_capital_capacity",
                    cash=round(cash, 2),
                    reserve=round(reserve, 2),
                    current=round(current, 2),
                    max_position=round(max_position, 2),
                )
                continue

            capacity = adaptive.liquidity_capacity(item, candidate_amount)
            if not capacity["allowed"]:
                capacity_reason = str(capacity.get("reason") or "liquidity_capacity_blocked")
                rejections.append({"symbol": symbol, "reason": "liquidity capacity", "capacity_reason": capacity_reason})
                _log_optimizer_decision(
                    worker,
                    symbol,
                    status="REJECTED",
                    reason="liquidity_capacity",
                    capacity_reason=capacity_reason,
                    candidate_amount=round(candidate_amount, 2),
                    executable=round(adaptive._finite(capacity.get("executable_order_value")), 2),
                )
                continue
            executable_amount = min(candidate_amount, adaptive._finite(capacity.get("executable_order_value")))
            if executable_amount <= 0:
                rejections.append({"symbol": symbol, "reason": "zero executable liquidity"})
                _log_optimizer_decision(
                    worker,
                    symbol,
                    status="REJECTED",
                    reason="zero_executable_liquidity",
                    candidate_amount=round(candidate_amount, 2),
                )
                continue

            # Do not turn weak/tiny sizing into a trade. Preserve it as a watch
            # candidate so momentum can continue to be observed until the same
            # opportunity can justify a meaningful, risk-calculated allocation.
            if executable_amount + 1e-9 < meaningful_entry_floor:
                rejections.append(
                    {
                        "symbol": symbol,
                        "reason": "watch_momentum_candidate",
                        "watch_only": True,
                        "proposed_amount": round(executable_amount, 8),
                        "minimum_notional": round(minimum_notional, 8),
                        "meaningful_entry_floor": round(meaningful_entry_floor, 2),
                        "opportunity_score": round(adaptive._finite(item.get("soft_score") or item.get("opportunity_score")), 4),
                        "authorization_basis": gate.get("authorization_basis") or "hard_risk_gate",
                    }
                )
                _log_optimizer_decision(
                    worker,
                    symbol,
                    status="WATCH",
                    reason="below_meaningful_entry_floor",
                    proposed_amount=round(executable_amount, 2),
                    meaningful_entry_floor=round(meaningful_entry_floor, 2),
                )
                continue

            sector = str(item.get("sector") or "").strip() or "Unknown"
            if equity and (sector_exposure.get(sector, 0.0) + executable_amount) / equity > adaptive.MAX_SECTOR_EXPOSURE_PCT:
                rejections.append({"symbol": symbol, "reason": "sector concentration limit"})
                _log_optimizer_decision(
                    worker,
                    symbol,
                    status="REJECTED",
                    reason="sector_concentration_limit",
                    sector=sector,
                    proposed_amount=round(executable_amount, 2),
                )
                continue

            allocations.append(
                {
                    "symbol": symbol,
                    "amount": round(executable_amount, 2),
                    "sector": sector,
                    "liquidity": capacity,
                    "authorization_basis": gate.get("authorization_basis") or "hard_risk_gate",
                    "core_target_gap": round(strategic_target_gap, 2) if strategic_target_gap > 0 else None,
                    "meaningful_entry_floor": round(meaningful_entry_floor, 2),
                }
            )
            _log_optimizer_decision(
                worker,
                symbol,
                status="APPROVED",
                reason="capital_allocated",
                amount=round(executable_amount, 2),
                cash_before=round(cash, 2),
                reserve=round(reserve, 2),
            )
            cash -= executable_amount
            exposure_by_symbol[symbol] = current + executable_amount
            sector_exposure[sector] = sector_exposure.get(sector, 0.0) + executable_amount
            recalc_count += 1

        return {
            **state,
            "allocations": allocations,
            "recalculations": recalc_count,
            "cash_after_plan": round(cash, 2),
            "meaningful_entry_floor": round(meaningful_entry_floor, 2),
            "rejections": rejections,
        }

    optimizer._oracle_strategic_rebalance_gate = True
    worker.adaptive_portfolio_optimizer = optimizer
