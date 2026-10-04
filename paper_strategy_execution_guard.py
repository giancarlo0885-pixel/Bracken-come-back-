from __future__ import annotations

import copy
from datetime import datetime, timezone
import logging
import os
from typing import Any

import paper_model_validation_gate as regime_gate
import paper_strategy_economics as economics


log = logging.getLogger("paper-strategy-execution-guard")
_INSTALLED = False
_RESEARCH_ENTRY_CONFIRMATIONS: dict[str, dict[str, Any]] = {}


def _value(signal: Any, name: str, default: Any = None) -> Any:
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed else default


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _blocked_buy(reason: str) -> tuple[bool, str, None]:
    """Match oracle_bot._buy's public return contract on paper-only rejection."""
    return False, str(reason or "paper_strategy_execution_guard_rejected"), None


def _open_position_accumulation_allows(symbol: str) -> tuple[bool, str]:
    """Rate-limit repeated paper adds while a same-symbol position is already open.

    The optimizer amount is an executable allocation, not a durable target. Without
    a final cadence guard, a persistent BUY idea can therefore spend that allocation
    again on every worker cycle. This check is deliberately downstream of every
    optimizer/core-rebalance path and reads persisted position/trade state. It does
    not modify historical records, strategy attribution, or any live-money control.
    """
    interval = max(
        0.0,
        min(
            120.0,
            _number(os.getenv("PAPER_CRYPTO_OPEN_POSITION_ACCUMULATION_COOLDOWN_MINUTES", "5"), 5.0),
        ),
    )
    if interval <= 0:
        return True, "open_position_accumulation_cooldown_disabled"

    try:
        import oracle_bot

        position = oracle_bot.row(
            "SELECT symbol FROM positions WHERE market='crypto' AND symbol=%s LIMIT 1",
            (str(symbol or "").upper(),),
        )
        if not position:
            return True, "no_open_position"
        latest_buy = oracle_bot.row(
            """
            SELECT created_at
            FROM trades
            WHERE market='crypto' AND symbol=%s AND side='BUY'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(symbol or "").upper(),),
        ) or {}
    except Exception as exc:
        log.warning(
            "PAPER OPEN POSITION ACCUMULATION | symbol=%s | state=UNAVAILABLE | reason=%s | action=ALLOW_EXISTING_GUARDS",
            str(symbol or "").upper(),
            exc.__class__.__name__,
        )
        return True, "open_position_state_unavailable"

    last_buy = _parse_time(latest_buy.get("created_at"))
    if last_buy is None:
        return True, "open_position_buy_timestamp_unavailable"

    age_minutes = max(0.0, (datetime.now(timezone.utc) - last_buy).total_seconds() / 60.0)
    if age_minutes < interval:
        return False, f"open_position_accumulation_cooldown:{age_minutes:.2f}/{interval:.2f}m"
    return True, f"open_position_accumulation_cooldown_elapsed:{age_minutes:.2f}/{interval:.2f}m"



def _research_entry_confirmation_allows(
    symbol: str,
    signal: Any,
    verified_quote: dict[str, Any] | None,
    edge: float | None,
    cost: float,
    scorecard: Any,
) -> tuple[bool, str]:
    """Require two distinct verified observations for mature losing research tiers.

    This is paper-only. It does not lower any economics threshold. The extra
    confirmation applies only after a strategy has enough closed samples to show
    negative post-cost economics while still being classified RESEARCH.
    """
    key = str(symbol or "").upper().strip()
    tier = str(getattr(scorecard, "model_tier", "RESEARCH") or "RESEARCH").upper()
    sample_count = int(getattr(scorecard, "sample_count", 0) or 0)
    expectancy = _number(getattr(scorecard, "expectancy", 0.0))
    profit_factor = _number(getattr(scorecard, "profit_factor", 0.0))
    min_samples = max(
        2,
        int(_number(os.getenv("PAPER_RESEARCH_CONFIRMATION_MIN_SAMPLES", "30"), 30.0)),
    )

    mature_losing_research = (
        tier == "RESEARCH"
        and sample_count >= min_samples
        and (expectancy < 0.0 or profit_factor < 1.0)
    )
    if not mature_losing_research:
        _RESEARCH_ENTRY_CONFIRMATIONS.pop(key, None)
        return True, "research_entry_confirmation_not_required"

    action = str(_value(signal, "action", "BUY") or "BUY").upper().strip()
    if action not in {"BUY", "ACCUMULATE"}:
        _RESEARCH_ENTRY_CONFIRMATIONS.pop(key, None)
        return True, "research_entry_confirmation_non_entry_action"

    if edge is None or edge <= cost:
        _RESEARCH_ENTRY_CONFIRMATIONS.pop(key, None)
        return False, "research_entry_confirmation_requires_positive_post_cost_edge"

    quote = verified_quote if isinstance(verified_quote, dict) else {}
    observation_id = str(
        quote.get("quote_timestamp")
        or quote.get("timestamp")
        or quote.get("reference_timestamp")
        or ""
    ).strip()
    if not observation_id:
        _RESEARCH_ENTRY_CONFIRMATIONS.pop(key, None)
        return False, "research_entry_confirmation_requires_verified_quote_timestamp"

    previous = _RESEARCH_ENTRY_CONFIRMATIONS.get(key)
    now = datetime.now(timezone.utc)
    max_gap_minutes = max(
        1.0,
        min(
            60.0,
            _number(os.getenv("PAPER_RESEARCH_CONFIRMATION_MAX_GAP_MINUTES", "20"), 20.0),
        ),
    )
    if previous:
        seen_at = previous.get("seen_at")
        if isinstance(seen_at, datetime):
            age_minutes = max(0.0, (now - seen_at).total_seconds() / 60.0)
        else:
            age_minutes = max_gap_minutes + 1.0
        if age_minutes <= max_gap_minutes and previous.get("observation_id") != observation_id:
            _RESEARCH_ENTRY_CONFIRMATIONS.pop(key, None)
            return True, "research_entry_confirmation_passed:2/2"

    _RESEARCH_ENTRY_CONFIRMATIONS[key] = {
        "observation_id": observation_id,
        "seen_at": now,
        "action": action,
        "edge": float(edge),
        "cost": float(cost),
    }
    return False, "research_entry_confirmation_pending:1/2"


def _remaining_cumulative_tier_capacity(symbol: str, tier_target: float) -> tuple[float | None, float]:
    """Return remaining open cost-basis capacity for a sub-baseline paper tier.

    Tier sizing is a total open-exposure cap, not a fresh order allowance on each
    scan. None means persisted position state could not be read and callers must
    fail closed rather than refresh the cap.
    """
    cap = max(0.0, _number(tier_target))
    try:
        import oracle_bot

        position = oracle_bot.row(
            """
            SELECT quantity, current_price, average_price, entry_price
            FROM positions
            WHERE market='crypto' AND symbol=%s
            LIMIT 1
            """,
            (str(symbol or "").upper(),),
        )
    except Exception as exc:
        log.warning(
            "PAPER TIER CUMULATIVE CAP | symbol=%s | state=UNAVAILABLE | reason=%s | action=BLOCK",
            str(symbol or "").upper(),
            exc.__class__.__name__,
        )
        return None, 0.0

    if not position:
        return round(cap, 2), 0.0

    quantity = max(0.0, _number(position.get("quantity")))
    basis_price = max(
        0.0,
        _number(
            position.get("average_price")
            or position.get("entry_price")
            or position.get("current_price")
        ),
    )
    current_value = max(0.0, quantity * basis_price)
    return max(0.0, round(cap - current_value, 2)), current_value


def _with_entry_economics_provenance(
    signal: Any,
    verified_quote: dict[str, Any] | None,
    quant_assessment: Any | None = None,
) -> Any:
    """Build an immutable paper-only economics view from entry-time evidence.

    QuantTradeAssessment stores percentage fields as decimal ratios (0.01 == 1%),
    while paper_strategy_economics uses percentage points (1.0 == 1%). Keep
    gross edge and execution cost separate. net_expected_value_pct is already
    post-cost, so it must never be handed to a later cost gate as gross edge.
    When the optimizer has already resolved a cost for this exact candidate,
    preserve that cost through final execution so planning and execution use the
    same economics.
    """
    updates: dict[str, float] = {}

    # Prefer the exact optimizer cost already used to allocate this candidate.
    allocation = _value(signal, "v39_optimizer_allocation", None)
    if _value(signal, "estimated_cost_pct", None) is None and isinstance(allocation, dict):
        raw_optimizer_cost = allocation.get("estimated_round_trip_cost_pct")
        try:
            optimizer_cost = float(raw_optimizer_cost)
        except (TypeError, ValueError):
            optimizer_cost = float("nan")
        if optimizer_cost == optimizer_cost and optimizer_cost >= 0 and optimizer_cost != float("inf"):
            updates["estimated_cost_pct"] = optimizer_cost

    if quant_assessment is not None:
        raw_quant_cost = getattr(quant_assessment, "estimated_cost_pct", None)
        try:
            quant_cost = float(raw_quant_cost)
        except (TypeError, ValueError):
            quant_cost = float("nan")
        quant_cost_valid = (
            quant_cost == quant_cost and quant_cost >= 0 and quant_cost != float("inf")
        )

        # If no optimizer/signal cost exists, preserve the exact cost that the
        # quant standard used when computing its net expected value.
        if (
            _value(signal, "estimated_cost_pct", None) is None
            and "estimated_cost_pct" not in updates
            and quant_cost_valid
        ):
            updates["estimated_cost_pct"] = quant_cost * 100.0

        if economics.expected_edge_pct(signal) is None:
            raw_gross_edge = getattr(quant_assessment, "gross_expected_value_pct", None)
            try:
                gross_edge = float(raw_gross_edge)
            except (TypeError, ValueError):
                gross_edge = float("nan")

            if gross_edge == gross_edge and abs(gross_edge) != float("inf"):
                updates["expected_edge_pct"] = gross_edge * 100.0
            else:
                # Compatibility for older quant objects that expose only net EV:
                # reconstruct gross edge only when the exact deducted cost is
                # also present. Never treat net EV alone as gross edge.
                raw_net_edge = getattr(quant_assessment, "net_expected_value_pct", None)
                try:
                    net_edge = float(raw_net_edge)
                except (TypeError, ValueError):
                    net_edge = float("nan")
                if (
                    net_edge == net_edge
                    and abs(net_edge) != float("inf")
                    and quant_cost_valid
                ):
                    updates["expected_edge_pct"] = (net_edge + quant_cost) * 100.0

    if isinstance(verified_quote, dict) and _value(signal, "spread_pct", None) is None:
        observed_spread = verified_quote.get("spread_pct")
        try:
            spread = float(observed_spread)
        except (TypeError, ValueError):
            spread = float("nan")
        if spread == spread and spread >= 0 and spread != float("inf"):
            updates["spread_pct"] = spread

    if not updates:
        return signal
    if isinstance(signal, dict):
        clone = dict(signal)
        clone.update(updates)
        return clone
    try:
        clone = copy.copy(signal)
        for key, value in updates.items():
            setattr(clone, key, value)
        return clone
    except Exception:
        return signal


def _with_optimizer_target(signal: Any, target: float) -> Any:
    """Return a shallow signal copy with the paper optimizer target adjusted.

    The source signal is never mutated, preserving decision/audit provenance.
    """
    if isinstance(signal, dict):
        clone = dict(signal)
        if "v39_optimizer_approved_amount" in clone:
            clone["v39_optimizer_approved_amount"] = target
        allocation = clone.get("v39_optimizer_allocation")
        if isinstance(allocation, dict):
            copied = dict(allocation)
            copied["amount"] = min(float(copied.get("amount") or target), target)
            clone["v39_optimizer_allocation"] = copied
        return clone

    try:
        clone = copy.copy(signal)
        if hasattr(clone, "v39_optimizer_approved_amount"):
            setattr(clone, "v39_optimizer_approved_amount", target)
        allocation = getattr(clone, "v39_optimizer_allocation", None)
        if isinstance(allocation, dict):
            copied = dict(allocation)
            copied["amount"] = min(float(copied.get("amount") or target), target)
            setattr(clone, "v39_optimizer_allocation", copied)
        return clone
    except Exception:
        return signal


def install_paper_strategy_execution_guard() -> bool:
    """Install paper-only fee-aware entry filtering and adaptive strategy sizing.

    This wrapper is intentionally installed after the optimizer-size handoff so it
    governs the final BUY/ACCUMULATE entry path. It never changes SELL/EXIT/CLOSE,
    quote integrity, accounting, or live-capital controls.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if not economics.active():
        return False

    import oracle_bot

    original_buy = oracle_bot._buy

    def guarded_buy(
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
        if not economics.active() or str(market or "").strip().lower() != "crypto":
            return original_buy(
                market, symbol, price, signal,
                quant_assessment=quant_assessment,
                target_trade_value=target_trade_value,
                rotation_candidate=rotation_candidate,
                verified_quote=verified_quote,
                rotation_verified_quote=rotation_verified_quote,
            )

        accumulation_allowed, accumulation_reason = _open_position_accumulation_allows(symbol)
        if not accumulation_allowed:
            log.info(
                "PAPER OPEN POSITION ACCUMULATION | symbol=%s | action=%s | allowed=False | reason=%s | "
                "mode=paper | broker_submission=NONE | live_trading=DISARMED",
                str(symbol or "").upper(),
                str(_value(signal, "action", "BUY") or "BUY").upper(),
                accumulation_reason,
            )
            return _blocked_buy(accumulation_reason)

        economics_signal = _with_entry_economics_provenance(signal, verified_quote, quant_assessment)
        allowed, edge_reason, edge, cost = economics.fee_edge_allows_entry(economics_signal)
        if not allowed:
            log.info(
                "PAPER FEE AWARE ENTRY | symbol=%s | allowed=False | expected_edge_pct=%s | "
                "estimated_round_trip_cost_pct=%.4f | reason=%s | mode=paper | broker_submission=NONE | live_trading=DISARMED",
                str(symbol or "").upper(),
                "unknown" if edge is None else f"{edge:.4f}",
                cost,
                edge_reason,
            )
            return _blocked_buy(edge_reason)

        optimizer_target = float(_value(signal, "v39_optimizer_approved_amount", 0.0) or 0.0)
        base_target = optimizer_target if optimizer_target > 0 else float(target_trade_value or 0.0)
        # Carry the exact economics view used by the final fee gate into the
        # immutable entry snapshot. This changes provenance only: sizing below
        # still evaluates the original signal, and broker/live controls are
        # unchanged.
        adjusted_signal = economics_signal
        adjusted_target = target_trade_value
        if base_target > 0:
            sized, scorecard, size_reason = economics.adjusted_optimizer_target(signal, base_target)
            regime_ok, regime_reason = regime_gate.regime_validation_ok(signal)
            if sized > base_target and not regime_ok:
                sized = base_target
                size_reason = f"positive_boost_withheld:{regime_reason}"
            if scorecard.size_multiplier < 1.0:
                remaining, current_value = _remaining_cumulative_tier_capacity(symbol, sized)
                if remaining is None:
                    return _blocked_buy("paper_tier_position_state_unavailable")
                sized = min(sized, remaining)
                size_reason = (
                    f"{size_reason}:cumulative_remaining={sized:.2f}:"
                    f"current={current_value:.2f}"
                )
            economics.log_economics(
                scorecard,
                symbol=symbol,
                original_target=base_target,
                adjusted_target=sized,
                reason=size_reason,
            )
            if sized <= 0:
                _RESEARCH_ENTRY_CONFIRMATIONS.pop(str(symbol or "").upper().strip(), None)
                return _blocked_buy("strategy_economics_zero_target")
            confirmation_allowed, confirmation_reason = _research_entry_confirmation_allows(
                symbol,
                economics_signal,
                verified_quote,
                edge,
                cost,
                scorecard,
            )
            if not confirmation_allowed:
                log.info(
                    "PAPER RESEARCH ENTRY CONFIRMATION | symbol=%s | allowed=False | reason=%s | "
                    "expected_edge_pct=%s | estimated_round_trip_cost_pct=%.4f | samples=%s | "
                    "expectancy=%s | profit_factor=%s | mode=paper | broker_submission=NONE | live_trading=DISARMED",
                    str(symbol or "").upper(),
                    confirmation_reason,
                    "unknown" if edge is None else f"{edge:.4f}",
                    cost,
                    getattr(scorecard, "sample_count", 0),
                    getattr(scorecard, "expectancy", 0.0),
                    getattr(scorecard, "profit_factor", 0.0),
                )
                return _blocked_buy(confirmation_reason)
            if optimizer_target > 0:
                adjusted_signal = _with_optimizer_target(economics_signal, sized)
            else:
                adjusted_target = sized

        log.info(
            "PAPER FEE AWARE ENTRY | symbol=%s | allowed=True | expected_edge_pct=%s | estimated_round_trip_cost_pct=%.4f | "
            "reason=%s | accumulation_reason=%s | mode=paper | broker_submission=NONE | live_trading=DISARMED",
            str(symbol or "").upper(),
            "unknown" if edge is None else f"{edge:.4f}",
            cost,
            edge_reason,
            accumulation_reason,
        )
        return original_buy(
            market, symbol, price, adjusted_signal,
            quant_assessment=quant_assessment,
            target_trade_value=adjusted_target,
            rotation_candidate=rotation_candidate,
            verified_quote=verified_quote,
            rotation_verified_quote=rotation_verified_quote,
        )

    oracle_bot._buy = guarded_buy
    _INSTALLED = True
    accumulation_interval = max(
        0.0,
        min(120.0, _number(os.getenv("PAPER_CRYPTO_OPEN_POSITION_ACCUMULATION_COOLDOWN_MINUTES", "5"), 5.0)),
    )
    log.info(
        "PAPER STRATEGY EXECUTION GUARD | active=True | fee_aware_entry=ENFORCED_WHEN_EDGE_AVAILABLE | "
        "adaptive_strategy_sizing=ENABLED | model_regime_boost_gate=ENABLED | mature_losing_research_confirmation=2_SCANS | open_position_accumulation_cooldown=%.2fm | "
        "exploration_floor=PRESERVED | final_churn_and_capacity_guards=DOWNSTREAM | broker_submission=NONE | live_trading=DISARMED",
        accumulation_interval,
    )
    return True
