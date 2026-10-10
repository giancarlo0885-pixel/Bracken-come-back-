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
    """Use one cost convention: realized wins/losses are already net of all costs.

    Output is a hypothetical fraction of equity, not risk per trade or an order.
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
    if independent_episodes < min_episodes or completed_trades < min_trades:
        return abstain("insufficient_independent_evidence")
    b = average_net_win_pct / abs(average_net_loss_pct)
    full = lower_bound_win_probability - (1 - lower_bound_win_probability) / b
    if full <= 0:
        return abstain("nonpositive_conservative_edge")
    return KellyProposal(True, "shadow_only", full, min(max_allocation, fraction_of_kelly * full))
