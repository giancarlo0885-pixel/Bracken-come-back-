"""Winner-led, cost-aware cohort discovery. Research only; no execution imports.

Input is one verified completed round trip per trade_id. Entry cohort labels
must be immutable entry-time evidence, not labels reconstructed after exit.
Net return = canonical net P&L / allocated entry notional (fees already paid).
Score = lower episode-bootstrap bound of p(win)*mean(win)-(1-p(win))*mean(loss).
The empirical expectation includes zero outcomes. No second cost deduction.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np

VERSION = "winner-edge-research-v1"
COHORT_FIELDS = ("market", "strategy", "regime", "entry_pattern")


def _time(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def _finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _key(row):
    return tuple(str(row.get(field) or "unknown") for field in COHORT_FIELDS)


def _episode(row):
    seconds = 86400 if row["market"] == "cash" else 14400
    return (row["market"], int(row["entry"].timestamp()) // seconds)


def prepare(rows, as_of):
    """Exclude incomplete, conflicting, future and post-hoc evidence explicitly."""
    counts = Counter()
    identities = defaultdict(list)
    for row in rows:
        identities[str(row.get("trade_id") or "")].append(row)
    valid = []
    for identity, versions in identities.items():
        row = versions[0]
        if not identity or any(version != row for version in versions):
            counts["missing_or_conflicting_identity"] += len(versions)
            continue
        counts["duplicate"] += len(versions) - 1
        entry, exit_, evidence = (_time(row.get(field)) for field in
                                  ("entry_time", "exit_time", "entry_evidence_time"))
        net, notional = _finite(row.get("net_pnl")), _finite(row.get("entry_notional"))
        if (entry is None or exit_ is None or evidence is None or evidence > entry
                or exit_ < entry):
            counts["invalid_entry_time_provenance"] += 1
        elif exit_ > as_of:
            counts["future_or_open_outcome"] += 1
        elif (row.get("fifo_complete") is not True
              or row.get("fill_fee_evidence_verified") is not True
              or row.get("entry_cohort_verified") is not True):
            counts["unverified_accounting_or_cohort"] += 1
        elif (net is None or notional is None or notional <= 0
              or row.get("market") not in {"cash", "crypto"}
              or any(label in {"unknown", "unclassified", ""} for label in _key(row))):
            counts["missing_features_or_economics"] += 1
        else:
            valid.append({**row, "net_pnl": net, "entry": entry, "exit": exit_,
                          "net_return": net / notional})
    return valid, dict(counts)


def summarize(rows, *, alpha=0.05):
    values = np.array([row["net_return"] for row in rows], dtype=float)
    clusters = defaultdict(list)
    for row in rows:
        clusters[_episode(row)].append(row["net_return"])
    bound = None
    if len(clusters) >= 25:
        sums = np.array([sum(v) for v in clusters.values()])
        sizes = np.array([len(v) for v in clusters.values()])
        rng = np.random.default_rng(42)
        indexes = rng.integers(0, len(sums), (4000, len(sums)))
        means = sums[indexes].sum(axis=1) / sizes[indexes].sum(axis=1)
        bound = float(np.quantile(means, alpha))
    wins, losses = values[values > 0], values[values < 0]
    return {"trades": len(rows), "episodes": len(clusters),
            "win_rate": float(len(wins) / len(values)) if len(values) else None,
            "mean_win_return": float(wins.mean()) if len(wins) else None,
            "mean_loss_return": float(losses.mean()) if len(losses) else None,
            "mean_net_return": float(values.mean()) if len(values) else None,
            "net_pnl": sum(row["net_pnl"] for row in rows),
            "score_lower_bound": bound}


def discover(rows, *, cutoff, as_of):
    """Freeze discovery before cutoff, then measure later completed outcomes.

    Entire episode windows touching the split are purged. Forward records never
    influence cohort selection, weights, costs or candidate scores.
    """
    if cutoff.tzinfo is None or as_of.tzinfo is None or cutoff >= as_of:
        raise ValueError("timezone-aware cutoff must precede as_of")
    valid, exclusions = prepare(rows, as_of)
    train, forward = defaultdict(list), []
    for row in valid:
        seconds = 86400 if row["market"] == "cash" else 14400
        episode_end = (_episode(row)[1] + 1) * seconds
        if row["exit"] < cutoff and episode_end <= cutoff.timestamp():
            train[_key(row)].append(row)
        elif row["entry"] >= cutoff and _episode(row)[1] * seconds >= cutoff.timestamp():
            forward.append(row)
        else:
            exclusions["boundary_or_open_training_episode"] = exclusions.get("boundary_or_open_training_episode", 0) + 1
    # Correct discovery confidence for the number of cohorts searched.
    alpha = 0.05 / max(1, len(train))
    candidates, selected = [], set()
    for key, history in sorted(train.items()):
        stats = summarize(history, alpha=alpha)
        eligible = (stats["trades"] >= 50 and stats["episodes"] >= 25
                    and stats["score_lower_bound"] is not None
                    and stats["score_lower_bound"] > 0)
        candidates.append({"cohort": dict(zip(COHORT_FIELDS, key)), **stats,
                           "research_candidate": eligible})
        if eligible:
            selected.add(key)
    accepted = [r for r in forward if _key(r) in selected]
    rejected = [r for r in forward if _key(r) not in selected]
    return {"version": VERSION, "cutoff": cutoff.isoformat(), "as_of": as_of.isoformat(),
            "exclusions": exclusions, "search_cohorts": len(train),
            "discovery_alpha": alpha,
            "candidates": sorted(candidates, key=lambda r: r["score_lower_bound"] if r["score_lower_bound"] is not None else -math.inf, reverse=True),
            "forward": {"baseline": summarize(forward), "selected": summarize(accepted),
                        "avoided_loss_pnl": -sum(r["net_pnl"] for r in rejected if r["net_pnl"] < 0),
                        "rejected_winner_pnl": sum(r["net_pnl"] for r in rejected if r["net_pnl"] > 0),
                        "rejected_winners": sum(r["net_pnl"] > 0 for r in rejected),
                        "selection_pnl_delta": -sum(r["net_pnl"] for r in rejected)},
            "promotion_action": "NONE", "execution_impact": "NONE",
            "limitations": "Observed completed trades only; unexecuted signals and continuous price paths are not measured. Four-hour/day windows are dependence proxies, not certified independent episodes. Requires separate chronological validation and risk review before promotion."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON array of verified round trips")
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    cutoff, as_of = _time(args.cutoff), _time(args.as_of)
    if cutoff is None or as_of is None:
        parser.error("timestamps require explicit timezones")
    print(json.dumps(discover(json.loads(args.input.read_text()), cutoff=cutoff, as_of=as_of), allow_nan=False))
