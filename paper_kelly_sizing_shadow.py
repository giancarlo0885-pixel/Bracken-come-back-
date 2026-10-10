"""Research-only Kelly sizing. No broker or Council execution integration."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class KellyProposal:
    eligible: bool
    reason: str
    full_fraction: float
    proposed_fraction: float


def propose_kelly_size(
    *,
    win_probability: float,
    average_net_win_pct: float,
    average_net_loss_pct: float,
    independent_episodes: int,
    completed_trades: int,
    lower_bound_win_probability: float | None = None,
    fraction_of_kelly: float = 0.25,
    max_allocation: float = 0.10,
    min_episodes: int = 25,
    min_trades: int = 50,
) -> KellyProposal:
    """Binary-payoff approximation to log-optimal fraction of equity notional.

    Both payoff inputs are percentage RETURNS on position entry notional, net
    of ALL costs exactly once. Unlike a fixed-stake binary bet, this requires
    dividing the classical Kelly risk fraction by the loss return magnitude.
    Unlevered, paper-only proposals are capped separately at max_allocation.
    This does not model the full empirical return distribution or execution.
    The caller must use only entry-time-available, generation-isolated evidence.
    """
    abstain = lambda reason: KellyProposal(False, reason, 0.0, 0.0)
    vals = (win_probability, average_net_win_pct, average_net_loss_pct,
            fraction_of_kelly, max_allocation)
    if lower_bound_win_probability is not None:
        vals += (lower_bound_win_probability,)
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in vals):
        return abstain("invalid_numeric_input")
    if (not 0 <= win_probability <= 1 or
        lower_bound_win_probability is None or
        not 0 <= lower_bound_win_probability <= win_probability <= 1):
        return abstain("uncalibrated_probability")
    if average_net_win_pct <= 0 or average_net_loss_pct >= 0:
        return abstain("invalid_net_payoffs")
    if not 0 < fraction_of_kelly <= 1 or not 0 <= max_allocation <= 1:
        return abstain("invalid_sizing_limits")
    counts = (independent_episodes, completed_trades, min_episodes, min_trades)
    if any(type(n) is not int or n < 0 for n in counts):
        return abstain("invalid_sample_counts")
    if min_episodes == 0 or min_trades == 0 or independent_episodes > completed_trades:
        return abstain("invalid_sample_counts")
    if independent_episodes < min_episodes or completed_trades < min_trades:
        return abstain("insufficient_independent_evidence")
    win_return = average_net_win_pct / 100.0
    loss_return = abs(average_net_loss_pct) / 100.0
    if loss_return > 1.0:
        return abstain("loss_exceeds_unlevered_notional")
    # Expected return must be positive even under conservative probability.
    expected = lower_bound_win_probability * win_return - (1.0 - lower_bound_win_probability) * loss_return
    if expected <= 0:
        return abstain("nonpositive_conservative_edge")
    denominator = win_return * loss_return
    if not math.isfinite(denominator) or denominator <= 0:
        return abstain("invalid_numeric_input")
    full = expected / denominator
    if not math.isfinite(full):
        return abstain("invalid_numeric_input")
    # full is the unconstrained log-optimal *notional* fraction for a two-point
    # approximation, not a percent. No margin/leverage is authorized.
    return KellyProposal(True, "shadow_only", full, min(max_allocation, fraction_of_kelly * full))
