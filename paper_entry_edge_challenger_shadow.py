from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import os
import threading
from typing import Any

log = logging.getLogger("paper-entry-edge-challenger")
_VERSION = "entry-edge-challenger-v1"
_THREAD: threading.Thread | None = None
_STOP = threading.Event()


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def active() -> bool:
    return bool(
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def entry_edge_score(*, mfe_pct: float, mae_pct: float, round_trip_cost_pct: float,
                     loss_streak: int, bad_regime: bool) -> float:
    """Paper-only candidate score; inputs must be known before the candidate trade.

    The 0.20 percentage-point gate is an experiment, not a promoted execution rule.
    """
    loss_penalty = min(max(int(loss_streak), 0), 6) / 6.0
    return (
        0.35 * max(0.0, _num(mfe_pct))
        - 0.45 * abs(min(0.0, _num(mae_pct)))
        - max(0.0, _num(round_trip_cost_pct))
        - 0.20 * loss_penalty
        - 0.35 * (1.0 if bad_regime else 0.0)
    )


def ensure_schema() -> None:
    if not active():
        return
    from database import connect
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_entry_edge_challenger_epoch (
                version TEXT PRIMARY KEY,
                started_at TIMESTAMPTZ NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_entry_edge_challenger_results (
                trade_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                strategy TEXT NOT NULL,
                regime TEXT NOT NULL,
                entry_time TIMESTAMPTZ NOT NULL,
                exit_time TIMESTAMPTZ NOT NULL,
                prior_samples INTEGER NOT NULL,
                prior_expectancy DOUBLE PRECISION,
                prior_profit_factor DOUBLE PRECISION,
                prior_mfe_pct DOUBLE PRECISION,
                prior_mae_pct DOUBLE PRECISION,
                prior_cost_pct DOUBLE PRECISION,
                prior_loss_streak INTEGER NOT NULL DEFAULT 0,
                edge_score DOUBLE PRECISION,
                would_trade BOOLEAN NOT NULL,
                actual_net_pnl DOUBLE PRECISION NOT NULL,
                avoided_loss DOUBLE PRECISION NOT NULL DEFAULT 0,
                missed_winner DOUBLE PRECISION NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.execute(
            "INSERT INTO paper_entry_edge_challenger_epoch(version,started_at) VALUES (%s,NOW()) ON CONFLICT (version) DO NOTHING",
            (_VERSION,),
        )


def _prior_loss_streak(conn: Any, strategy: str, regime: str, entry_time: datetime) -> int:
    rows = list(conn.execute("""
        SELECT m.net_pnl
        FROM paper_regime_trade_metrics m
        WHERE m.strategy=%s AND m.regime=%s AND m.exit_time < %s
        ORDER BY m.exit_time DESC LIMIT 6
    """, (strategy, regime, entry_time)).fetchall())
    streak = 0
    for row in rows:
        if _num(row.get("net_pnl")) < 0:
            streak += 1
        else:
            break
    return streak


def evaluate_new_closes(limit: int = 250) -> int:
    """Evaluate only forward closes after this challenger's durable epoch."""
    if not active():
        return 0
    from database import connect
    from paper_strategy_economics import normalize_strategy_identity

    created = 0
    with connect() as conn:
        epoch = conn.execute(
            "SELECT started_at FROM paper_entry_edge_challenger_epoch WHERE version=%s", (_VERSION,)
        ).fetchone()
        if not epoch:
            return 0
        rows = list(conn.execute("""
            SELECT m.trade_id,m.strategy,m.regime,m.entry_time,m.exit_time,m.net_pnl,
                   l.quantity,l.entry_price,l.fees
            FROM paper_regime_trade_metrics m
            JOIN trade_ledger l ON l.trade_id=m.trade_id
            WHERE m.exit_time >= %s AND m.strategy='oracle_council_v3'
              AND m.entry_time IS NOT NULL AND m.exit_time IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM paper_entry_edge_challenger_results r WHERE r.trade_id=m.trade_id
              )
            ORDER BY m.exit_time ASC LIMIT %s
        """, (epoch.get("started_at"), max(1, int(limit)))).fetchall())

        for row in rows:
            strategy = normalize_strategy_identity(row.get("strategy"))
            regime = str(row.get("regime") or "unknown")
            entry_time = row.get("entry_time")
            prior = conn.execute("""
                SELECT COUNT(*) AS samples,
                       AVG(m.net_pnl) AS expectancy,
                       AVG(m.mfe_pct) FILTER (WHERE m.excursion_sample_count>0) AS mfe,
                       AVG(m.mae_pct) FILTER (WHERE m.excursion_sample_count>0) AS mae,
                       SUM(CASE WHEN m.net_pnl>0 THEN m.net_pnl ELSE 0 END) AS gross_win,
                       ABS(SUM(CASE WHEN m.net_pnl<0 THEN m.net_pnl ELSE 0 END)) AS gross_loss,
                       AVG(CASE WHEN l.quantity>0 AND l.entry_price>0
                           THEN (l.fees/(l.quantity*l.entry_price))*100.0 END) AS cost_pct
                FROM paper_regime_trade_metrics m
                JOIN trade_ledger l ON l.trade_id=m.trade_id
                WHERE m.strategy=%s AND m.regime=%s AND m.exit_time < %s
            """, (strategy, regime, entry_time)).fetchone() or {}
            samples = int(prior.get("samples") or 0)
            expectancy = _num(prior.get("expectancy"))
            gross_loss = _num(prior.get("gross_loss"))
            pf = (_num(prior.get("gross_win")) / gross_loss) if gross_loss > 0 else 0.0
            mfe = _num(prior.get("mfe"))
            mae = _num(prior.get("mae"))
            cost_pct = _num(prior.get("cost_pct"))
            streak = _prior_loss_streak(conn, strategy, regime, entry_time)
            bad_regime = samples >= 25 and expectancy < 0
            score = entry_edge_score(
                mfe_pct=mfe, mae_pct=mae, round_trip_cost_pct=cost_pct,
                loss_streak=streak, bad_regime=bad_regime,
            )
            # Insufficient history always abstains. This is shadow telemetry only.
            would_trade = bool(samples >= 25 and expectancy > 0 and pf > 1.0 and score > 0.20)
            pnl = _num(row.get("net_pnl"))
            avoided_loss = -pnl if (not would_trade and pnl < 0) else 0.0
            missed_winner = pnl if (not would_trade and pnl > 0) else 0.0
            conn.execute("""
                INSERT INTO paper_entry_edge_challenger_results(
                    trade_id,version,strategy,regime,entry_time,exit_time,prior_samples,
                    prior_expectancy,prior_profit_factor,prior_mfe_pct,prior_mae_pct,
                    prior_cost_pct,prior_loss_streak,edge_score,would_trade,actual_net_pnl,
                    avoided_loss,missed_winner
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (trade_id) DO NOTHING
            """, (
                row.get("trade_id"),_VERSION,strategy,regime,entry_time,row.get("exit_time"),samples,
                expectancy,pf,mfe,mae,cost_pct,streak,score,would_trade,pnl,avoided_loss,missed_winner,
            ))
            created += 1
    return created


def emit_summary() -> None:
    if not active():
        return
    try:
        from database import connect
        with connect() as conn:
            row = conn.execute("""
                SELECT COUNT(*) AS samples,
                       SUM(CASE WHEN would_trade THEN 1 ELSE 0 END) AS accepted,
                       SUM(actual_net_pnl) AS champion_net,
                       SUM(CASE WHEN would_trade THEN actual_net_pnl ELSE 0 END) AS challenger_net,
                       SUM(avoided_loss) AS avoided_loss,
                       SUM(missed_winner) AS missed_winner
                FROM paper_entry_edge_challenger_results WHERE version=%s
            """, (_VERSION,)).fetchone() or {}
        log.info(
            "PAPER ENTRY EDGE CHALLENGER | samples=%s | accepted=%s | champion_net=%.4f | challenger_net=%.4f | avoided_loss=%.4f | missed_winner=%.4f | gate=0.20pct | mode=shadow | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
            row.get("samples") or 0,row.get("accepted") or 0,_num(row.get("champion_net")),
            _num(row.get("challenger_net")),_num(row.get("avoided_loss")),_num(row.get("missed_winner")),
        )
    except Exception as exc:
        log.warning("PAPER ENTRY EDGE CHALLENGER | status=UNAVAILABLE | reason=%s", exc.__class__.__name__)


def _loop(interval_seconds: float) -> None:
    cycles = 0
    while not _STOP.wait(interval_seconds):
        if not active():
            return
        try:
            evaluate_new_closes()
            cycles += 1
            if cycles == 1 or cycles % 10 == 0:
                emit_summary()
        except Exception as exc:
            log.warning("PAPER ENTRY EDGE CHALLENGER | sampler=ERROR | reason=%s", exc.__class__.__name__)


def install_paper_entry_edge_challenger_shadow() -> bool:
    """Start forward measurement only; never changes Council decisions or execution."""
    global _THREAD
    if not active():
        return False
    ensure_schema()
    evaluate_new_closes()
    emit_summary()
    if _THREAD and _THREAD.is_alive():
        return True
    interval = max(30.0, _num(os.getenv("PAPER_ENTRY_EDGE_SAMPLE_SECONDS", "60"), 60.0))
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop,args=(interval,),name="paper-entry-edge-shadow",daemon=True)
    _THREAD.start()
    log.info("PAPER ENTRY EDGE CHALLENGER | active=True | version=%s | mode=shadow | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED", _VERSION)
    return True
