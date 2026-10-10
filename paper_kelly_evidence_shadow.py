"""Strict provenance contract for offline Kelly research; never submits orders."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from paper_kelly_sizing_shadow import KellyProposal, propose_kelly_size


@dataclass(frozen=True)
class CompletedPaperOutcome:
    trade_id: str
    generation_id: str
    episode_id: str
    entry_time: datetime
    exit_time: datetime
    net_return_pct: float


def evaluate_prior_generation_evidence(
    *,
    outcomes: Sequence[CompletedPaperOutcome],
    generation_id: str,
    candidate_entry_time: datetime,
    lower_bound_win_probability: float | None,
) -> KellyProposal:
    """Only fully closed prior trades from the same generation are eligible.

    net_return_pct must already include fees/spread/slippage/impact once.
    No outcomes from the candidate's own episode are used by this interface;
    the caller must exclude its episode and provide a calibrated probability
    lower bound from an independent, time-valid model.
    """
    def abstain(reason: str) -> KellyProposal:
        return KellyProposal(False, reason, 0.0, 0.0)

    if not generation_id or candidate_entry_time.tzinfo is None:
        return abstain("invalid_provenance")
    if any(
        not o.trade_id or not o.episode_id or
        o.entry_time.tzinfo is None or o.exit_time.tzinfo is None or
        o.entry_time >= o.exit_time or
        o.exit_time >= candidate_entry_time
        for o in outcomes
    ):
        return abstain("invalid_or_future_outcome")
    prior = [o for o in outcomes if o.generation_id == generation_id]
    if len({o.trade_id for o in prior}) != len(prior):
        return abstain("duplicate_trade")
    import math
    if any(not math.isfinite(o.net_return_pct) for o in prior):
        return abstain("invalid_net_return")
    wins = [o.net_return_pct for o in prior if o.net_return_pct > 0]
    losses = [o.net_return_pct for o in prior if o.net_return_pct < 0]
    if not wins or not losses:
        return abstain("insufficient_win_loss_distribution")
    return propose_kelly_size(
        win_probability=len(wins) / len(prior),
        lower_bound_win_probability=lower_bound_win_probability,
        average_net_win_pct=sum(wins) / len(wins),
        average_net_loss_pct=sum(losses) / len(losses),
        independent_episodes=len({o.episode_id for o in prior}),
        completed_trades=len(prior),
    )
