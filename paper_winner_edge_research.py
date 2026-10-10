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
# Predeclared before prospective outcomes; no automatically rolling holdout.
FORWARD_CUTOFF = datetime(2026, 10, 11, tzinfo=timezone.utc)


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
        elif not math.isfinite(net / notional):
            counts["nonfinite_normalized_return"] += 1
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
        # Bound working memory for long histories; preserve whole-window draws.
        means = np.empty(4000)
        batch_size = max(1, min(128, 1_000_000 // len(sums)))
        for start in range(0, 4000, batch_size):
            end = min(4000, start + batch_size)
            indexes = rng.integers(0, len(sums), (end - start, len(sums)))
            means[start:end] = sums[indexes].sum(axis=1) / sizes[indexes].sum(axis=1)
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
    if cutoff.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("cutoff and as_of must be timezone-aware")
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


def verified_ledger_inputs(ledger, fills, *, as_of, fifo_report=None):
    """Reconcile full history, then use BUY-side snapshots for entry labels only."""
    from paper_exit_research import attach_fill_fee_evidence, reconcile_fifo

    report = fifo_report if fifo_report is not None else reconcile_fifo(
        attach_fill_fee_evidence(ledger, fills), as_of=as_of, require_fill_fee_evidence=True)
    buys = {r["trade_id"]: r for r in ledger if r.get("side") == "BUY"}
    rows, exclusions = [], Counter()
    grouped = defaultdict(list)
    for close in report["closes"]:
        if len(close["members"]) != 1:
            exclusions["mixed_entry_close"] += 1
            continue
        grouped[close["members"][0]["buy_trade_id"]].append(close)
    for buy_id, parts in grouped.items():
        opened = _finite(buys.get(buy_id, {}).get("quantity"))
        closed = sum(part["members"][0]["quantity"] for part in parts)
        if opened is None or opened <= 0 or not math.isclose(closed, opened, rel_tol=1e-9, abs_tol=1e-12):
            exclusions["open_or_partial_entry_lot"] += 1
            continue
        if any(not part["fifo_complete"] for part in parts):
            exclusions["unreconciled_fifo"] += 1
            continue
        close = {**parts[0], "trade_id": buy_id,
                 "exit_time": max(part["exit_time"] for part in parts),
                 "net_pnl": sum(part["net_pnl"] for part in parts),
                 "members": [{**parts[0]["members"][0], "quantity": closed}],
                 "sell_trade_ids": [part["trade_id"] for part in parts]}
        if not close["fifo_complete"]:
            exclusions["unreconciled_fifo"] += 1
            continue
        labels, evidence_times, notional = [], [], 0.0
        for member in close["members"]:
            buy = buys.get(member["buy_trade_id"], {})
            snapshot = buy.get("feature_snapshot") or {}
            if isinstance(snapshot, str):
                try:
                    snapshot = json.loads(snapshot)
                except ValueError:
                    snapshot = {}
            if not isinstance(snapshot, dict):
                snapshot = {}
            regime = snapshot.get("regime")
            if not regime:
                # Classify only when both directional and volatility evidence
                # exist; the shared classifier's default zeros are not data.
                directional = any(_finite(snapshot.get(f)) is not None
                                  for f in ("trend_strength", "trend", "momentum_20d"))
                volatility = _finite(snapshot.get("volatility_20d", snapshot.get("volatility")))
                if directional and volatility is not None and volatility > 0:
                    from paper_regime_economics_shadow import classify_regime
                    regime = classify_regime(feature_snapshot=snapshot)
            labels.append((buy.get("strategy"), regime,
                           snapshot.get("entry_pattern") or snapshot.get("schwager_pattern_tag")))
            evidence_times.append(_time(buy.get("decision_timestamp")))
            price = _finite(buy.get("entry_price"))
            quantity = _finite(member.get("quantity"))
            if price is None or price <= 0 or quantity is None or quantity <= 0:
                notional = float("nan")
            else:
                notional += price * quantity
        entry = _time(close["entry_time"])
        if (not labels or any(label != labels[0] for label in labels)
                or any(not isinstance(value, str) or not value
                       or value in {"unknown", "unclassified"} for value in labels[0])
                or any(t is None or entry is None or t > entry for t in evidence_times)
                or not math.isfinite(notional) or notional <= 0):
            exclusions["unverified_or_mixed_entry_snapshot"] += 1
            continue
        strategy, regime, pattern = labels[0]
        rows.append({**close, "strategy": strategy, "regime": regime,
                     "entry_pattern": pattern, "entry_notional": notional,
                     "entry_evidence_time": max(evidence_times).isoformat(),
                     "entry_cohort_verified": True})
    return rows, {"fifo": report["diagnostics"], "adapter": dict(exclusions),
                  "observed_closes": report["observed_closes"], "exported_trades": len(rows)}


def runtime_report(rows, *, as_of, previous=None, coverage=None):
    """Freeze once at cutoff; later outcome updates cannot rerank discovery."""
    provisional = discover(rows, cutoff=FORWARD_CUTOFF, as_of=as_of)
    previous = previous if isinstance(previous, dict) else {}
    matching = (previous.get("version") == VERSION
                and previous.get("cutoff") == FORWARD_CUTOFF.isoformat())
    if matching and previous.get("discovery_frozen") is True:
        # A schema/version change requires a new declared experiment, never an
        # implicit replacement of the existing holdout's selection manifest.
        candidates = previous.get("candidates", [])
        provisional["candidates"] = candidates
        provisional["search_cohorts"] = previous.get("search_cohorts", 0)
        provisional["discovery_alpha"] = previous.get("discovery_alpha")
        selected = {_key(c["cohort"]) for c in candidates if c.get("research_candidate")}
        valid, _ = prepare(rows, as_of)
        forward = [r for r in valid if r["entry"] >= FORWARD_CUTOFF]
        accepted = [r for r in forward if _key(r) in selected]
        rejected = [r for r in forward if _key(r) not in selected]
        provisional["forward"] = {"baseline": summarize(forward), "selected": summarize(accepted),
            "avoided_loss_pnl": -sum(r["net_pnl"] for r in rejected if r["net_pnl"] < 0),
            "rejected_winner_pnl": sum(r["net_pnl"] for r in rejected if r["net_pnl"] > 0),
            "rejected_winners": sum(r["net_pnl"] > 0 for r in rejected),
            "selection_pnl_delta": -sum(r["net_pnl"] for r in rejected)}
    provisional["discovery_frozen"] = as_of >= FORWARD_CUTOFF
    provisional["phase"] = "forward_validation" if provisional["discovery_frozen"] else "training_only"
    provisional["history_coverage"] = coverage or {}
    return provisional


def read_verified_history(market, *, as_of):
    """Read-only snapshot; never truncates opening inventory or writes reports."""
    from database import connect
    if market not in {"cash", "crypto"}:
        raise ValueError("market must be cash or crypto")
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        ledger = [dict(r) for r in conn.execute(
            """SELECT id,trade_id,market,symbol,side,quantity,entry_price,exit_price,
                      entry_time,exit_time,fees,gross_pnl,net_pnl,strategy,order_id,
                      feature_snapshot,decision_timestamp
               FROM trade_ledger WHERE market=%s AND broker_mode='PAPER'
                 AND account_environment='PAPER' AND side IN ('BUY','SELL')""",
            (market,)).fetchall()]
        fills = [dict(r) for r in conn.execute(
            """SELECT fill_id,market,symbol,side,quantity,fill_price,fee_amount,created_at
               FROM paper_fills WHERE market=%s""", (market,)).fetchall()]
    return verified_ledger_inputs(ledger, fills, as_of=as_of)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, nargs="?", help="JSON array of verified round trips")
    parser.add_argument("--database-market", choices=("cash", "crypto"))
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    cutoff, as_of = _time(args.cutoff), _time(args.as_of)
    if cutoff is None or as_of is None:
        parser.error("timestamps require explicit timezones")
    if bool(args.input) == bool(args.database_market):
        parser.error("choose exactly one JSON input or --database-market")
    coverage = None
    if args.database_market:
        rows, coverage = read_verified_history(args.database_market, as_of=as_of)
    else:
        rows = json.loads(args.input.read_text())
    result = discover(rows, cutoff=cutoff, as_of=as_of)
    if coverage is not None:
        result["history_coverage"] = coverage
    print(json.dumps(result, allow_nan=False))
