"""Research-only post-cost winner cohort evaluation.

This module never authorizes orders. It uses completed outcomes only for
retrospective analysis; callers must supply genuinely entry-time features and
chronologically separate future episodes for forward evaluation.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Mapping


@dataclass(frozen=True)
class CohortMetrics:
    label: str
    trades: int
    episodes: int
    wins: int
    net_pnl: float
    expectancy: float
    profit_factor: float | None
    qualified_for_research: bool


def analyze_completed_cohorts(rows: Iterable[Mapping[str, object]], *, min_trades: int = 50, min_episodes: int = 25) -> list[CohortMetrics]:
    """Summarize unique completed fills by pre-entry phenotype and episode.

    Missing identity, provenance, episode, or finite net P&L fails closed.
    No claims of a validated edge are made by this descriptive report.
    """
    seen: set[str] = set()
    groups: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in rows:
        trade_id = str(row.get("trade_id") or "").strip()
        label = str(row.get("entry_phenotype") or "").strip()
        episode = str(row.get("market_episode_id") or "").strip()
        if not (trade_id and label and episode and row.get("entry_provenance_valid") is True):
            continue
        try:
            net = float(row["net_pnl"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isfinite(net):
            continue
        if trade_id in seen:
            continue
        seen.add(trade_id)
        groups[label].append((episode, net))
    result = []
    for label, items in sorted(groups.items()):
        gains = sum(max(p, 0.0) for _, p in items)
        losses = -sum(min(p, 0.0) for _, p in items)
        net = sum(p for _, p in items)
        episodes = len({e for e, _ in items})
        result.append(CohortMetrics(
            label=label, trades=len(items), episodes=episodes,
            wins=sum(p > 0 for _, p in items), net_pnl=net,
            expectancy=net / len(items),
            profit_factor=(gains / losses if losses > 0 else None),
            qualified_for_research=(len(items) >= min_trades and episodes >= min_episodes),
        ))
    return result
