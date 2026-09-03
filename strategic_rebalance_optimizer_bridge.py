from __future__ import annotations

from typing import Any

import global_adaptive_engine as adaptive
import runtime_integrity_patch as patch


_TACTICAL_AUTHORIZATION_REASONS = {
    "at least three core signals must support the trade",
    "confidence below trade threshold",
}


def _explicit_strategic_rebalance(item: dict[str, Any]) -> bool:
    return bool(
        item.get("core_rebalance_candidate") is True
        and str(item.get("portfolio_intent") or "").strip().upper() == patch.CORE_REBALANCE_CANDIDATE_INTENT
        and str(item.get("tactical_action") or "").strip().upper() == "HOLD"
    )


def _strategic_rebalance_gate(item: dict[str, Any]) -> dict[str, Any]:
    """Reuse the existing hard gate while separating tactical authorization.

    Strategic core rebalance authorization comes from an explicit portfolio
    target-gap intent, not from a tactical BUY vote. All hard-risk reasons remain
    blocking except the two tactical authorization checks (3-core-signal agreement
    and 70% tactical confidence), and only for an explicitly marked HOLD-based
    core-rebalance candidate.
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

        for item in sorted(
            opportunities,
            key=lambda row: adaptive._finite(row.get("soft_score") or row.get("opportunity_score")),
            reverse=True,
        ):
            if adaptive.classify_capital_engine(item) != engine or item.get("qualified_for_capital") is not True:
                continue

            symbol = adaptive._upper(item.get("symbol"))
            gate = _strategic_rebalance_gate(item)
            if not gate.get("allowed"):
                rejections.append({"symbol": symbol, "reason": "hard risk gate", "risk_reasons": gate.get("reasons") or []})
                continue

            current = exposure_by_symbol.get(symbol, 0.0)
            max_position = equity * adaptive.GLOBAL_PIT_MAX_POSITION_PCT
            if current >= max_position:
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
                break

            capacity = adaptive.liquidity_capacity(item, candidate_amount)
            if not capacity["allowed"]:
                continue
            executable_amount = min(candidate_amount, adaptive._finite(capacity.get("executable_order_value")))
            if executable_amount <= 0:
                continue

            sector = str(item.get("sector") or "").strip() or "Unknown"
            if equity and (sector_exposure.get(sector, 0.0) + executable_amount) / equity > adaptive.MAX_SECTOR_EXPOSURE_PCT:
                rejections.append({"symbol": symbol, "reason": "sector concentration limit"})
                continue

            allocations.append(
                {
                    "symbol": symbol,
                    "amount": round(executable_amount, 2),
                    "sector": sector,
                    "liquidity": capacity,
                    "authorization_basis": gate.get("authorization_basis") or "hard_risk_gate",
                    "core_target_gap": round(strategic_target_gap, 2) if strategic_target_gap > 0 else None,
                }
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
            "rejections": rejections,
        }

    optimizer._oracle_strategic_rebalance_gate = True
    worker.adaptive_portfolio_optimizer = optimizer
