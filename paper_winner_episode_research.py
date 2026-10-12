"""Read-only, point-in-time winner phenotype research with episode holdout.

Rows must already be reconciled to canonical paper fills and explicit fees.
This module neither reconstructs missing evidence nor changes trade decisions.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from typing import Any


def _time(value: Any) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt.astimezone(timezone.utc)


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite evidence")
    return result


def qualified_trade(row: dict[str, Any]) -> dict[str, Any] | None:
    """Fail closed unless fees, timestamps and entry-time feature provenance exist."""
    try:
        entry = _time(row["entry_time"])
        exit_time = _time(row["exit_time"])
        observed = _time(row["feature_observed_at"])
        if observed > entry or exit_time <= entry:
            return None
        if row.get("fill_reconciled") is not True or row.get("fees_verified") is not True:
            return None
        if not row.get("episode_id") or not row.get("entry_signal_id"):
            return None
        if not row.get("entry_forecast_id"):
            return None
        pnl = _finite(row["net_pnl_after_fees"])
        phenotype = str(row["entry_phenotype"]).strip()
        if not phenotype:
            return None
        return {"entry_time": entry, "exit_time": exit_time,
                "episode_id": str(row["episode_id"]), "phenotype": phenotype,
                "pnl": pnl, "symbol": str(row.get("symbol") or "")}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    gains = sum(max(t["pnl"], 0.0) for t in trades)
    losses = -sum(min(t["pnl"], 0.0) for t in trades)
    episodes = {t["episode_id"] for t in trades}
    return {"trades": len(trades), "episodes": len(episodes),
            "net_pnl": round(gains - losses, 8),
            "expectancy": round((gains - losses) / len(trades), 8) if trades else None,
            "profit_factor": round(gains / losses, 8) if losses else None,
            "profit_factor_defined": losses > 0}


def chronological_episode_holdout(rows: list[dict[str, Any]], *,
                                  cutoff: Any, min_training_episodes: int = 25,
                                  min_holdout_episodes: int = 25) -> dict[str, Any]:
    """Choose candidate phenotypes from training only; never promote from this report."""
    boundary = _time(cutoff)
    training: dict[str, list[dict[str, Any]]] = defaultdict(list)
    holdout: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded = 0
    for row in rows:
        trade = qualified_trade(row)
        if trade is None:
            excluded += 1
            continue
        # Trades straddling the cutoff are excluded, never assigned to training.
        if trade["exit_time"] <= boundary:
            training[trade["phenotype"]].append(trade)
        elif trade["entry_time"] > boundary:
            holdout[trade["phenotype"]].append(trade)
        else:
            excluded += 1
    # Entire market episodes cannot appear on both sides, even across symbols.
    train_episodes = {t["episode_id"] for group in training.values() for t in group}
    test_episodes = {t["episode_id"] for group in holdout.values() for t in group}
    overlap = train_episodes & test_episodes
    if overlap:
        for groups in (training, holdout):
            for key in groups:
                kept = [t for t in groups[key] if t["episode_id"] not in overlap]
                excluded += len(groups[key]) - len(kept)
                groups[key] = kept
    results = {}
    for phenotype in sorted(set(training) | set(holdout)):
        train = _metrics(training[phenotype])
        test = _metrics(holdout[phenotype])
        discovered = (train["episodes"] >= min_training_episodes
                      and train["expectancy"] is not None and train["expectancy"] > 0
                      and train["profit_factor_defined"] and train["profit_factor"] > 1)
        validated = (discovered and test["episodes"] >= min_holdout_episodes
                     and test["expectancy"] is not None and test["expectancy"] > 0
                     and test["profit_factor_defined"] and test["profit_factor"] > 1)
        results[phenotype] = {"training": train, "holdout": test,
                              "training_candidate": discovered,
                              "holdout_positive": validated,
                              "promotion_authorized": False}
    return {"cutoff": boundary.isoformat(), "excluded_unqualified_or_leaking": excluded,
            "phenotypes": results, "execution_impact": "NONE",
            "broker_submission": "NONE", "promotion_authorized": False}


def episode_risk_report(rows: list[dict[str, Any]], *, confidence: float = 0.95) -> dict[str, Any]:
    """Descriptive episode-level tail risk; no IID-trade significance claims.

    Episodes are ordered by their last close. The report uses net realized P&L,
    not unobserved intra-trade equity, so drawdown is a close-to-close proxy.
    """
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")
    trades = [t for row in rows if (t := qualified_trade(row)) is not None]
    by_episode: dict[str, dict[str, Any]] = {}
    for trade in trades:
        episode = by_episode.setdefault(trade["episode_id"],
                                        {"pnl": 0.0, "closed": trade["exit_time"]})
        episode["pnl"] += trade["pnl"]
        episode["closed"] = max(episode["closed"], trade["exit_time"])
    ordered = sorted(by_episode.values(), key=lambda e: (e["closed"], e["pnl"]))
    equity = peak = max_drawdown = 0.0
    for episode in ordered:
        equity += episode["pnl"]
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    outcomes = sorted(e["pnl"] for e in ordered)
    if not outcomes:
        return {"episodes": 0, "var_loss": None, "cvar_loss": None,
                "max_closed_episode_drawdown": None, "qualified_trades": 0}
    # Lower-tail empirical quantile. Negative outcomes become positive loss values.
    tail_count = max(1, math.ceil((1 - confidence) * len(outcomes)))
    tail = outcomes[:tail_count]
    return {"episodes": len(outcomes), "qualified_trades": len(trades),
            "var_loss": round(max(0.0, -tail[-1]), 8),
            "cvar_loss": round(max(0.0, -sum(tail) / len(tail)), 8),
            "max_closed_episode_drawdown": round(max_drawdown, 8),
            "confidence": confidence,
            "tail_episode_count": tail_count,
            "risk_scope": "realized-episode-close-only",
            "promotion_authorized": False}


def matched_exit_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare immutable paired exits only; reject unmatched or fabricated edges."""
    pairs = []
    rejected = 0
    for row in rows:
        try:
            if row.get("matched_forward_observation") is not True:
                raise ValueError("unmatched")
            if row.get("fees_verified") is not True:
                raise ValueError("unverified fees")
            decision = _time(row["decision_time"])
            quote = _time(row["quote_observed_at"])
            if quote > decision:
                raise ValueError("lookahead quote")
            champion = _finite(row["champion_net_pnl"])
            challenger = _finite(row["challenger_net_pnl"])
            if not row.get("episode_id") or not row.get("trade_id"):
                raise ValueError("missing identity")
            pairs.append({"episode": str(row["episode_id"]),
                          "delta": challenger - champion,
                          "avoided_loss": champion < 0 <= challenger,
                          "missed_winner": champion > 0 and challenger < champion})
        except (KeyError, ValueError, TypeError, OverflowError):
            rejected += 1
    episodes = {p["episode"] for p in pairs}
    return {"matched_pairs": len(pairs), "independent_episodes": len(episodes),
            "unqualified_pairs": rejected,
            "average_net_delta": round(sum(p["delta"] for p in pairs) / len(pairs), 8)
            if pairs else None,
            "avoided_losses": sum(p["avoided_loss"] for p in pairs),
            "missed_winners": sum(p["missed_winner"] for p in pairs),
            "promotion_authorized": False}
