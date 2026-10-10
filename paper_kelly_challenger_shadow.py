"""Forward-only, crypto paper-trade Kelly benchmark. Observation only; no orders."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import os
import threading
from typing import Any

from paper_kelly_evidence_shadow import CompletedPaperOutcome, evaluate_prior_generation_evidence
from paper_kelly_sizing_shadow import KellyProposal

log = logging.getLogger("paper-kelly-shadow")
_VERSION = "kelly-v1-forward-council-benchmark"
_THREAD: threading.Thread | None = None
_STOP = threading.Event()


def _truthy(name: str) -> bool:
    return str(os.getenv(name, "false")).strip().lower() in {"1", "true", "yes", "on"}


def active() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "paper")).strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _finite(value: Any) -> float | None:
    try:
        val = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return val if math.isfinite(val) else None


def _episode(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timezone-aware entry timestamp required")
    # Conservatively cluster all crypto assets traded on the same UTC date.
    return value.astimezone(timezone.utc).date().isoformat()


def _net_return_pct(row: dict[str, Any]) -> float | None:
    """Exact-lot round-trip P&L divided by entry notional; already net of costs."""
    pnl = _finite(row.get("round_trip_net_pnl"))
    qty = _finite(row.get("quantity"))
    price = _finite(row.get("entry_price"))
    fees = _finite(row.get("round_trip_fees"))
    if (row.get("cost_provenance") != "exact_lot" or
        pnl is None or qty is None or price is None or fees is None or
        qty == 0 or price <= 0 or fees < 0):
        return None
    result = 100.0 * pnl / (abs(qty) * price)
    return result if math.isfinite(result) and result >= -100.0 else None


def episode_win_lower_bound(outcomes: list[CompletedPaperOutcome]) -> float | None:
    """One-sided Hoeffding 95% bound on episode-average realized win fractions.

    Each UTC day has equal weight, even if it contains many correlated trades.
    This is a deliberately conservative research bound, NOT a validated model
    calibration or proof that daily episodes are truly independent.
    """
    if not outcomes:
        return None
    episodes: dict[str, list[float]] = {}
    for o in outcomes:
        if not math.isfinite(o.net_return_pct):
            return None
        episodes.setdefault(o.episode_id, []).append(1.0 if o.net_return_pct > 0 else 0.0)
    rate = sum(sum(v) / len(v) for v in episodes.values()) / len(episodes)
    bound = max(0.0, rate - math.sqrt(math.log(20.0) / (2.0 * len(episodes))))
    trade_rate = sum(o.net_return_pct > 0 for o in outcomes) / len(outcomes)
    return min(trade_rate, bound)


def evaluate_paper_candidate(
    *,
    candidate: dict[str, Any],
    prior_rows: list[dict[str, Any]],
) -> tuple[KellyProposal, dict[str, Any]]:
    """A candidate's realized close is used ONLY as the subsequent outcome label."""
    def abstain(reason: str) -> KellyProposal:
        return KellyProposal(False, reason, 0.0, 0.0)

    entry = candidate.get("entry_time")
    exit_time = candidate.get("exit_time")
    started = candidate.get("generation_started_at")
    generation = candidate.get("generation")
    config_hash = str(candidate.get("config_hash") or "")
    if (not isinstance(entry, datetime) or entry.tzinfo is None or
        not isinstance(exit_time, datetime) or exit_time.tzinfo is None or
        not isinstance(started, datetime) or started.tzinfo is None or
        not isinstance(generation, int) or generation < 1 or not config_hash or
        not started <= entry < exit_time):
        return abstain("invalid_generation_or_trade_timestamps"), {}
    outcome_return = _net_return_pct(candidate)
    if outcome_return is None:
        return abstain("missing_exact_lot_return"), {}
    candidate_episode = _episode(entry)
    outcomes: list[CompletedPaperOutcome] = []
    seen: set[str] = set()
    for r in prior_rows:
        prior_entry, prior_exit = r.get("entry_time"), r.get("exit_time")
        trade_id = str(r.get("trade_id") or "")
        # Reject, rather than quietly repair, incoherent prior evidence.
        if (not trade_id or trade_id in seen or
            not isinstance(prior_entry, datetime) or prior_entry.tzinfo is None or
            not isinstance(prior_exit, datetime) or prior_exit.tzinfo is None or
            not started <= prior_entry < prior_exit < entry):
            return abstain("invalid_prior_provenance"), {}
        seen.add(trade_id)
        ret = _net_return_pct(r)
        if ret is None:
            return abstain("incomplete_prior_cost_provenance"), {}
        outcomes.append(CompletedPaperOutcome(
            trade_id=trade_id,
            generation_id=f"{generation}:{config_hash}",
            episode_id=_episode(prior_entry),
            entry_time=prior_entry,
            exit_time=prior_exit,
            net_return_pct=ret,
        ))
    eligible_prior = [o for o in outcomes if o.episode_id != candidate_episode]
    lower = episode_win_lower_bound(eligible_prior)
    wins = [o.net_return_pct for o in eligible_prior if o.net_return_pct > 0]
    losses = [o.net_return_pct for o in eligible_prior if o.net_return_pct < 0]
    diagnostics = {
        "prior_trades": len(eligible_prior),
        "prior_episodes": len({o.episode_id for o in eligible_prior}),
        "win_probability": len(wins) / len(eligible_prior) if eligible_prior else None,
        "win_lower_bound": lower,
        "mean_net_win_pct": sum(wins) / len(wins) if wins else None,
        "mean_net_loss_pct": sum(losses) / len(losses) if losses else None,
        "council_net_return_pct": outcome_return,
        "episode_id": candidate_episode,
    }
    if not eligible_prior:
        return abstain("no_prior_independent_episodes"), diagnostics
    result = evaluate_prior_generation_evidence(
        outcomes=eligible_prior,
        generation_id=f"{generation}:{config_hash}",
        candidate_entry_time=entry,
        candidate_episode_id=candidate_episode,
        lower_bound_win_probability=lower,
    )
    return result, diagnostics


def ensure_schema() -> None:
    if not active():
        return
    from database import connect
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("paper_kelly_v1_schema",))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_kelly_shadow_epoch (
                version TEXT PRIMARY KEY,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_kelly_shadow_results (
                version TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                config_hash TEXT NOT NULL,
                regime TEXT NOT NULL,
                entry_time TIMESTAMPTZ NOT NULL,
                exit_time TIMESTAMPTZ NOT NULL,
                episode_id TEXT NOT NULL,
                prior_trades INTEGER NOT NULL,
                prior_episodes INTEGER NOT NULL,
                win_probability DOUBLE PRECISION,
                win_lower_bound DOUBLE PRECISION,
                mean_net_win_pct DOUBLE PRECISION,
                mean_net_loss_pct DOUBLE PRECISION,
                eligible BOOLEAN NOT NULL,
                reason TEXT NOT NULL,
                proposed_fraction DOUBLE PRECISION NOT NULL,
                council_net_pnl DOUBLE PRECISION NOT NULL,
                council_net_return_pct DOUBLE PRECISION NOT NULL,
                shadow_return_on_equity_pct DOUBLE PRECISION NOT NULL,
                fixed10_return_on_equity_pct DOUBLE PRECISION NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (version,trade_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_kelly_shadow_exclusions (
                version TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (version,trade_id)
            )
        """)
        conn.execute("INSERT INTO paper_kelly_shadow_epoch(version) VALUES (%s) ON CONFLICT (version) DO NOTHING", (_VERSION,))


def evaluate_new_closes(limit: int = 30) -> int:
    if not active():
        return 0
    from database import connect

    created = 0
    with connect() as conn:
        epoch = conn.execute("SELECT started_at FROM paper_kelly_shadow_epoch WHERE version=%s", (_VERSION,)).fetchone() or {}
        if epoch.get("started_at") is None:
            return 0
        candidates = list(conn.execute("""
            SELECT m.trade_id,m.market,m.regime,m.entry_time,m.exit_time,
                   m.round_trip_net_pnl,m.round_trip_fees,m.cost_provenance,
                   l.quantity,l.entry_price,
                   g.generation,g.config_hash,g.started_at AS generation_started_at
            FROM paper_regime_trade_metrics m
            JOIN trade_ledger l ON l.trade_id=m.trade_id
            JOIN LATERAL (
                SELECT generation,config_hash,started_at
                FROM paper_aeve_generations
                WHERE started_at <= m.entry_time
                ORDER BY started_at DESC,generation DESC LIMIT 1
            ) g ON TRUE
            WHERE m.market='crypto' AND m.strategy='oracle_council_v3'
              AND l.side='SELL' AND l.broker_mode='PAPER' AND l.account_environment='PAPER'
              AND m.entry_time >= %s AND m.exit_time IS NOT NULL
              AND m.cost_provenance='exact_lot'
              AND m.round_trip_net_pnl IS NOT NULL AND m.round_trip_fees IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM paper_kelly_shadow_results x
                  WHERE x.version=%s AND x.trade_id=m.trade_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM paper_kelly_shadow_exclusions e
                  WHERE e.version=%s AND e.trade_id=m.trade_id
              )
            ORDER BY m.exit_time ASC,m.trade_id ASC LIMIT %s
        """, (epoch["started_at"], _VERSION, _VERSION, max(1, min(int(limit), 100)))).fetchall())
        for raw in candidates:
            candidate = dict(raw)
            # Same market/regime, SAME generation start, strictly prior completed closes.
            # Historical comparisons never use the candidate's close for its estimate.
            prior = list(conn.execute("""
                SELECT m.trade_id,m.entry_time,m.exit_time,m.round_trip_net_pnl,
                       m.round_trip_fees,m.cost_provenance,l.quantity,l.entry_price
                FROM paper_regime_trade_metrics m
                JOIN trade_ledger l ON l.trade_id=m.trade_id
                WHERE m.market='crypto' AND m.strategy='oracle_council_v3'
                  AND m.regime=%s AND m.entry_time >= %s
                  AND m.exit_time < %s AND m.entry_time < %s
                  AND m.cost_provenance='exact_lot'
                  AND m.round_trip_net_pnl IS NOT NULL AND m.round_trip_fees IS NOT NULL
                  AND l.side='SELL' AND l.broker_mode='PAPER' AND l.account_environment='PAPER'
                ORDER BY m.exit_time DESC,m.trade_id DESC LIMIT 2500
            """, (candidate["regime"], candidate["generation_started_at"],
                  candidate["entry_time"], candidate["entry_time"])).fetchall())
            proposal, diagnostics = evaluate_paper_candidate(candidate=candidate, prior_rows=[dict(r) for r in prior])
            observed = diagnostics.get("council_net_return_pct")
            # Every row is exact-lot filtered, but no result is manufactured if
            # a corrupted trade fails the in-memory notional/provenance check.
            if observed is None:
                conn.execute("""
                    INSERT INTO paper_kelly_shadow_exclusions(version,trade_id,reason)
                    VALUES (%s,%s,%s) ON CONFLICT(version,trade_id) DO NOTHING
                """, (_VERSION,candidate["trade_id"],proposal.reason))
                log.warning("KELLY SHADOW | excluded invalid canonical trade=%s | reason=%s",
                            candidate.get("trade_id"), proposal.reason)
                continue
            f = proposal.proposed_fraction
            conn.execute("""
                INSERT INTO paper_kelly_shadow_results(
                    version,trade_id,generation,config_hash,regime,entry_time,exit_time,episode_id,
                    prior_trades,prior_episodes,win_probability,win_lower_bound,
                    mean_net_win_pct,mean_net_loss_pct,eligible,reason,proposed_fraction,
                    council_net_pnl,council_net_return_pct,shadow_return_on_equity_pct,
                    fixed10_return_on_equity_pct
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(version,trade_id) DO NOTHING
            """, (
                _VERSION,candidate["trade_id"],candidate["generation"],candidate["config_hash"],
                candidate["regime"],candidate["entry_time"],candidate["exit_time"],
                diagnostics.get("episode_id") or _episode(candidate["entry_time"]),
                diagnostics.get("prior_trades",0),diagnostics.get("prior_episodes",0),
                diagnostics.get("win_probability"),diagnostics.get("win_lower_bound"),
                diagnostics.get("mean_net_win_pct"),diagnostics.get("mean_net_loss_pct"),
                proposal.eligible,proposal.reason,f,candidate["round_trip_net_pnl"],
                observed,f*observed,0.10*observed,
            ))
            created += 1
    return created


def emit_summary() -> None:
    if not active():
        return
    from database import connect
    with connect() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS compared,COUNT(*) FILTER (WHERE eligible) AS sized,
                   COUNT(DISTINCT episode_id) AS episodes,
                   AVG(proposed_fraction) AS mean_fraction,
                   AVG(council_net_return_pct) AS council_trade_return_pct,
                   SUM(shadow_return_on_equity_pct) AS sum_kelly_shadow_equity_pct,
                   SUM(fixed10_return_on_equity_pct) AS sum_fixed10_shadow_equity_pct,
                   COUNT(*) FILTER (WHERE NOT eligible AND council_net_pnl>0) AS abstained_winners
            FROM paper_kelly_shadow_results WHERE version=%s
        """, (_VERSION,)).fetchone() or {}
    log.info(
        "KELLY SHADOW | compared=%s | sized=%s | episodes=%s | mean_fraction=%s | "
        "council_trade_return_pct=%s | sum_kelly_shadow_equity_pct=%s | sum_fixed10_shadow_equity_pct=%s | "
        "abstained_winners=%s | return_basis=linear_counterfactual_not_portfolio_equity | "
        "execution_impact=NONE | live_trading=DISARMED",
        row.get("compared"),row.get("sized"),row.get("episodes"),
        row.get("mean_fraction"),row.get("council_trade_return_pct"),
        row.get("sum_kelly_shadow_equity_pct"),row.get("sum_fixed10_shadow_equity_pct"),row.get("abstained_winners"),
    )


def _loop(interval_seconds: float) -> None:
    cycle = 0
    while not _STOP.wait(interval_seconds):
        if not active():
            return
        try:
            evaluate_new_closes()
            cycle += 1
            if cycle == 1 or cycle % 10 == 0:
                emit_summary()
        except Exception as exc:
            log.warning("KELLY SHADOW | research_error=%s | execution_impact=NONE",
                        exc.__class__.__name__)


def install_paper_kelly_shadow() -> bool:
    global _THREAD
    if not active():
        return False
    try:
        ensure_schema()
        evaluate_new_closes()
        emit_summary()
    except Exception as exc:
        log.warning("KELLY SHADOW | startup=ERROR | reason=%s | execution_impact=NONE",
                    exc.__class__.__name__)
        return False
    if _THREAD and _THREAD.is_alive():
        return True
    _STOP.clear()
    interval = max(60.0, _finite(os.getenv("PAPER_KELLY_SAMPLE_SECONDS")) or 300.0)
    _THREAD = threading.Thread(target=_loop,args=(interval,),name="paper-kelly-shadow",daemon=True)
    _THREAD.start()
    return True
