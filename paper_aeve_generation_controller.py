from __future__ import annotations

"""Paper-only AEVE generation controller.

Every completed 1,000-trade research window, diagnose the dominant failure mode
and create the next challenger configuration. This module never changes Council,
position sizing, cooldowns, broker submission, or live-trading state.
"""

from dataclasses import dataclass, asdict, replace
import json
import logging
import math
import os
import threading
from typing import Any

log = logging.getLogger("paper-aeve-generation-controller")
_THREAD: threading.Thread | None = None
_STOP = threading.Event()
BATCH_SIZE = 1000
_REQUIRED_RESEARCH_RELATIONS = ("paper_aeve_generation_outcomes",)


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def active() -> bool:
    return bool(
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _f(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


@dataclass(frozen=True)
class AEVEGenerationConfig:
    generation: int = 1
    min_edge_pct: float = 0.05
    min_profit_factor: float = 1.00
    min_mfe_mae_ratio: float = 1.00
    min_mfe_cost_multiple: float = 2.00
    max_loss_streak: int = 8
    score_gate: float = 0.25
    rebound_gate: float = 0.50
    require_positive_regime: bool = True


@dataclass(frozen=True)
class BatchDiagnostics:
    samples: int
    expectancy: float
    profit_factor: float
    win_rate: float
    avg_mfe_pct: float
    avg_mae_pct: float
    avg_cost_pct: float

    @property
    def excursion_ratio(self) -> float:
        return self.avg_mfe_pct / max(abs(self.avg_mae_pct), 1e-9)

    @property
    def economically_positive(self) -> bool:
        return self.expectancy > 0 and self.profit_factor > 1.0


def next_generation(config: AEVEGenerationConfig, d: BatchDiagnostics) -> tuple[AEVEGenerationConfig, str]:
    """Change one major dimension only, based on the just-finished forward batch."""
    g = config.generation + 1

    if d.samples < BATCH_SIZE:
        return config, "insufficient_samples"

    if d.economically_positive and d.excursion_ratio > 1.0:
        return replace(config, generation=g), "hold_positive_formula_for_oos_confirmation"

    if d.avg_mfe_pct <= abs(d.avg_mae_pct):
        return replace(
            config,
            generation=g,
            min_mfe_mae_ratio=min(2.0, config.min_mfe_mae_ratio + 0.10),
        ), "adverse_excursion_dominates"

    if d.avg_cost_pct > 0 and d.avg_mfe_pct <= 3.0 * d.avg_cost_pct:
        return replace(
            config,
            generation=g,
            min_mfe_cost_multiple=min(4.0, config.min_mfe_cost_multiple + 0.25),
        ), "costs_consume_favorable_excursion"

    if d.profit_factor <= 1.0:
        return replace(
            config,
            generation=g,
            min_profit_factor=min(1.50, config.min_profit_factor + 0.05),
        ), "profit_factor_below_one"

    if d.expectancy <= 0:
        return replace(
            config,
            generation=g,
            min_edge_pct=min(0.30, config.min_edge_pct + 0.02),
        ), "negative_net_expectancy"

    return replace(config, generation=g, score_gate=min(0.60, config.score_gate + 0.025)), "weak_selection_quality"


def _missing_research_relations(conn: Any) -> list[str]:
    """Return required relations absent from the current PostgreSQL schema."""
    missing: list[str] = []
    for relation in _REQUIRED_RESEARCH_RELATIONS:
        row = conn.execute("SELECT to_regclass(%s) AS relation", (relation,)).fetchone() or {}
        value = row.get("relation") if hasattr(row, "get") else row[0]
        if value is None:
            missing.append(relation)
    return missing


def ensure_schema() -> None:
    if not active():
        return

    # The AEVE research query consumes Regime Economics output. Initialize that
    # paper-only schema first so service start ordering cannot produce UndefinedTable.
    from paper_regime_economics_shadow import ensure_schema as ensure_regime_schema
    ensure_regime_schema()

    from database import connect
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_aeve_generations (
                generation INTEGER PRIMARY KEY,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                config_json JSONB NOT NULL,
                diagnosis TEXT NOT NULL,
                source_samples INTEGER NOT NULL DEFAULT 0,
                source_expectancy DOUBLE PRECISION,
                source_profit_factor DOUBLE PRECISION,
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_aeve_generation_outcomes (
                id BIGSERIAL PRIMARY KEY,
                generation INTEGER NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                trade_id TEXT NOT NULL,
                net_pnl DOUBLE PRECISION NOT NULL,
                mfe_pct DOUBLE PRECISION,
                mae_pct DOUBLE PRECISION,
                excursion_sample_count INTEGER NOT NULL DEFAULT 0,
                cost_pct DOUBLE PRECISION,
                would_trade BOOLEAN NOT NULL,
                score DOUBLE PRECISION NOT NULL,
                config_json JSONB NOT NULL,
                UNIQUE(generation, trade_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_paper_aeve_generation_outcomes_generation_observed
            ON paper_aeve_generation_outcomes(generation, observed_at)
        """)
        row = conn.execute("SELECT generation FROM paper_aeve_generations ORDER BY generation DESC LIMIT 1").fetchone()
        if not row:
            cfg = AEVEGenerationConfig()
            conn.execute(
                "INSERT INTO paper_aeve_generations(generation,config_json,diagnosis,status) VALUES (%s,%s::jsonb,%s,'ACTIVE')",
                (cfg.generation, json.dumps(asdict(cfg)), "initial_aeve_v1"),
            )


def _load_active(conn: Any) -> tuple[AEVEGenerationConfig, Any]:
    row = conn.execute(
        "SELECT generation,started_at,config_json FROM paper_aeve_generations WHERE status='ACTIVE' ORDER BY generation DESC LIMIT 1"
    ).fetchone()
    raw = row.get("config_json") if row else {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    cfg = AEVEGenerationConfig(**{**asdict(AEVEGenerationConfig()), **(raw or {})})
    return cfg, row



def record_generation_outcomes(limit: int = 250) -> int:
    """Materialize forward AEVE shadow outcomes with immutable generation config.

    Council remains the factual control outcome. AEVE acceptance is recomputed only
    from information available before each Council entry; future P&L is used solely
    as the measured counterfactual outcome after the frozen decision is recorded.
    """
    if not active():
        return 0
    from database import connect
    from paper_aeve_v1_formula import score_entry

    created = 0
    with connect() as conn:
        cfg, generation_row = _load_active(conn)
        if not generation_row:
            return 0
        started_at = generation_row.get("started_at")
        config_snapshot = asdict(cfg)
        rows = list(conn.execute("""
            SELECT m.trade_id,m.regime,m.entry_time,m.exit_time,m.net_pnl,m.mfe_pct,m.mae_pct,
                   m.excursion_sample_count,l.quantity,l.entry_price,l.fees
            FROM paper_regime_trade_metrics m
            JOIN trade_ledger l ON l.trade_id=m.trade_id
            WHERE m.strategy='oracle_council_v3' AND m.exit_time >= %s
              AND m.entry_time IS NOT NULL AND m.exit_time IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM paper_aeve_generation_outcomes o
                  WHERE o.generation=%s AND o.trade_id=m.trade_id
              )
            ORDER BY m.exit_time ASC LIMIT %s
        """, (started_at, cfg.generation, max(1, int(limit)))).fetchall())

        for row in rows:
            entry_time = row.get("entry_time")
            prior = conn.execute("""
                SELECT COUNT(*) AS samples,AVG(net_pnl) AS expectancy,
                       SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) AS gross_win,
                       ABS(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END)) AS gross_loss,
                       AVG(mfe_pct) FILTER (WHERE excursion_sample_count>0) AS mfe,
                       AVG(mae_pct) FILTER (WHERE excursion_sample_count>0) AS mae
                FROM paper_regime_trade_metrics
                WHERE strategy='oracle_council_v3' AND regime=%s AND exit_time < %s
            """, (row.get("regime"), entry_time)).fetchone() or {}
            samples = int(prior.get("samples") or 0)
            gross_loss = _f(prior.get("gross_loss"))
            pf = (_f(prior.get("gross_win")) / gross_loss) if gross_loss > 0 else 0.0
            cost_pct = 0.0
            qty, price = _f(row.get("quantity")), _f(row.get("entry_price"))
            if qty > 0 and price > 0:
                cost_pct = max(0.0, (_f(row.get("fees")) / (qty * price)) * 100.0)
            # AEVE has no contemporaneous directional edge/rebound feed yet.
            # Fail closed instead of substituting future outcome data.
            decision = score_entry(
                expected_net_edge_pct=0.0,
                mfe_pct=max(0.0, _f(prior.get("mfe"))),
                mae_pct=min(0.0, _f(prior.get("mae"))),
                round_trip_cost_pct=cost_pct,
                loss_streak=0,
                price_above_recent_low_pct=0.0,
                rebound_from_low_pct=0.0,
                rsi=None,
                trend_confirmed=False,
                regime_expectancy_positive=bool(samples >= 30 and _f(prior.get("expectancy")) > 0),
                profit_factor=pf,
                min_samples=samples,
                config=config_snapshot,
            )
            conn.execute("""
                INSERT INTO paper_aeve_generation_outcomes(
                    generation,trade_id,observed_at,net_pnl,mfe_pct,mae_pct,
                    excursion_sample_count,cost_pct,would_trade,score,config_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (generation,trade_id) DO NOTHING
            """, (
                cfg.generation,row.get("trade_id"),row.get("exit_time"),_f(row.get("net_pnl")),
                row.get("mfe_pct"),row.get("mae_pct"),int(row.get("excursion_sample_count") or 0),
                cost_pct,decision.would_trade,decision.score,json.dumps(config_snapshot),
            ))
            created += 1
    return created


def maybe_advance_generation() -> bool:
    """Advance after exactly one new 1,000-trade forward window; paper telemetry only."""
    if not active():
        return False
    from database import connect
    with connect() as conn:
        missing = _missing_research_relations(conn)
        if missing:
            log.warning(
                "AEVE GENERATION CONTROLLER | status=WAITING_FOR_SCHEMA | missing_relations=%s | mode=shadow | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
                ",".join(missing),
            )
            return False

        record_generation_outcomes()
        cfg, row = _load_active(conn)
        if not row:
            return False
        started_at = row.get("started_at")
        batch = conn.execute("""
            WITH x AS (
                SELECT net_pnl,mfe_pct,mae_pct,excursion_sample_count,cost_pct
                FROM paper_aeve_generation_outcomes
                WHERE generation=%s AND observed_at >= %s AND would_trade=TRUE
                ORDER BY observed_at ASC
                LIMIT %s
            )
            SELECT COUNT(*) AS samples,
                   AVG(net_pnl) AS expectancy,
                   SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) AS gross_win,
                   ABS(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END)) AS gross_loss,
                   AVG(CASE WHEN net_pnl>0 THEN 1.0 ELSE 0.0 END) AS win_rate,
                   AVG(mfe_pct) FILTER (WHERE excursion_sample_count>0) AS avg_mfe,
                   AVG(mae_pct) FILTER (WHERE excursion_sample_count>0) AS avg_mae,
                   AVG(cost_pct) AS avg_cost
            FROM x
        """, (cfg.generation, started_at, BATCH_SIZE)).fetchone() or {}
        samples = int(batch.get("samples") or 0)
        if samples < BATCH_SIZE:
            return False
        gross_loss = _f(batch.get("gross_loss"))
        d = BatchDiagnostics(
            samples=samples,
            expectancy=_f(batch.get("expectancy")),
            profit_factor=(_f(batch.get("gross_win")) / gross_loss) if gross_loss > 0 else 0.0,
            win_rate=_f(batch.get("win_rate")),
            avg_mfe_pct=_f(batch.get("avg_mfe")),
            avg_mae_pct=_f(batch.get("avg_mae")),
            avg_cost_pct=_f(batch.get("avg_cost")),
        )
        nxt, diagnosis = next_generation(cfg, d)
        conn.execute("UPDATE paper_aeve_generations SET status='SUPERSEDED' WHERE generation=%s", (cfg.generation,))
        conn.execute("""
            INSERT INTO paper_aeve_generations(
                generation,config_json,diagnosis,source_samples,source_expectancy,source_profit_factor,status
            ) VALUES (%s,%s::jsonb,%s,%s,%s,%s,'ACTIVE')
        """, (nxt.generation, json.dumps(asdict(nxt)), diagnosis, d.samples, d.expectancy, d.profit_factor))
        log.info(
            "AEVE GENERATION ADVANCE | from=%s | to=%s | diagnosis=%s | samples=%s | expectancy=%.6f | pf=%.4f | mode=shadow | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
            cfg.generation, nxt.generation, diagnosis, d.samples, d.expectancy, d.profit_factor,
        )
        return True


def _loop(interval_seconds: float) -> None:
    while not _STOP.wait(interval_seconds):
        if not active():
            return
        try:
            maybe_advance_generation()
        except Exception as exc:
            log.warning(
                "AEVE GENERATION CONTROLLER | status=ERROR | reason=%s | detail=%s | mode=shadow | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
                exc.__class__.__name__, str(exc).replace("\n", " ")[:240],
            )


def install_aeve_generation_controller() -> bool:
    global _THREAD
    if not active():
        return False
    ensure_schema()
    maybe_advance_generation()
    if _THREAD and _THREAD.is_alive():
        return True
    interval = max(60.0, _f(os.getenv("PAPER_AEVE_GENERATION_CHECK_SECONDS", "300"), 300.0))
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, args=(interval,), name="paper-aeve-generation-controller", daemon=True)
    _THREAD.start()
    log.info("AEVE GENERATION CONTROLLER | active=True | batch=%s | mode=shadow | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED", BATCH_SIZE)
    return True
