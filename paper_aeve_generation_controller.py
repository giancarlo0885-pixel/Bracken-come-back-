from __future__ import annotations

"""Paper-only AEVE generation controller.

Every completed 1,000-trade research window, diagnose the dominant failure mode
and create the next challenger configuration. This module never changes Council,
position sizing, cooldowns, broker submission, or live-trading state.
"""

from dataclasses import dataclass, asdict, replace
import hashlib
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
PROVENANCE_VERSION = 2
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


def generation_config_payload(config: AEVEGenerationConfig | dict[str, Any]) -> dict[str, Any]:
    """Return the canonical, scoring-only configuration used for provenance."""
    raw = asdict(config) if isinstance(config, AEVEGenerationConfig) else dict(config or {})
    return {
        key: raw[key]
        for key in (
            "min_edge_pct", "min_profit_factor", "min_mfe_mae_ratio",
            "min_mfe_cost_multiple", "max_loss_streak", "score_gate",
            "rebound_gate", "require_positive_regime",
        )
        if key in raw
    }


def generation_config_hash(config: AEVEGenerationConfig | dict[str, Any]) -> str:
    canonical = json.dumps(
        generation_config_payload(config), sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decode_generation_row(row: Any) -> tuple[AEVEGenerationConfig, str]:
    raw = row.get("config_json") if row else {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    cfg = AEVEGenerationConfig(**{**asdict(AEVEGenerationConfig()), **(raw or {})})
    persisted_generation = (row or {}).get("generation")
    if persisted_generation is not None and int(persisted_generation) != cfg.generation:
        raise ValueError("AEVE generation identity mismatch")
    calculated = generation_config_hash(cfg)
    persisted = str((row or {}).get("config_hash") or "").strip().lower()
    if persisted and persisted != calculated:
        raise ValueError("AEVE generation config hash mismatch")
    return cfg, calculated


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
                config_hash TEXT,
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
                config_hash TEXT,
                provenance_version SMALLINT NOT NULL DEFAULT 1,
                UNIQUE(generation, trade_id)
            )
        """)
        conn.execute("ALTER TABLE paper_aeve_generations ADD COLUMN IF NOT EXISTS config_hash TEXT")
        conn.execute("ALTER TABLE paper_aeve_generation_outcomes ADD COLUMN IF NOT EXISTS config_hash TEXT")
        conn.execute(
            "ALTER TABLE paper_aeve_generation_outcomes ADD COLUMN IF NOT EXISTS provenance_version SMALLINT NOT NULL DEFAULT 1"
        )
        generation_rows = list(conn.execute(
            "SELECT generation,config_json,config_hash FROM paper_aeve_generations"
        ).fetchall())
        for generation_row in generation_rows:
            cfg, calculated_hash = _decode_generation_row(generation_row)
            if not str(generation_row.get("config_hash") or "").strip():
                conn.execute(
                    "UPDATE paper_aeve_generations SET config_hash=%s WHERE generation=%s",
                    (calculated_hash, cfg.generation),
                )
        outcome_rows = list(conn.execute(
            "SELECT id,config_json,config_hash FROM paper_aeve_generation_outcomes WHERE config_hash IS NULL OR config_hash=''"
        ).fetchall())
        for outcome_row in outcome_rows:
            raw = outcome_row.get("config_json") or {}
            if isinstance(raw, str):
                raw = json.loads(raw)
            effective = AEVEGenerationConfig(**{**asdict(AEVEGenerationConfig()), **raw})
            conn.execute(
                "UPDATE paper_aeve_generation_outcomes SET config_hash=%s WHERE id=%s",
                (generation_config_hash(effective), outcome_row.get("id")),
            )
        conn.execute("ALTER TABLE paper_aeve_generations ALTER COLUMN config_hash SET NOT NULL")
        conn.execute("ALTER TABLE paper_aeve_generation_outcomes ALTER COLUMN config_hash SET NOT NULL")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_paper_aeve_generation_outcomes_generation_observed
            ON paper_aeve_generation_outcomes(generation, observed_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_paper_aeve_generation_outcomes_identity_observed
            ON paper_aeve_generation_outcomes(generation, config_hash, provenance_version, observed_at)
        """)
        row = conn.execute("SELECT generation FROM paper_aeve_generations ORDER BY generation DESC LIMIT 1").fetchone()
        if not row:
            cfg = AEVEGenerationConfig()
            conn.execute(
                "INSERT INTO paper_aeve_generations(generation,config_json,config_hash,diagnosis,status) VALUES (%s,%s::jsonb,%s,%s,'ACTIVE')",
                (cfg.generation, json.dumps(asdict(cfg)), generation_config_hash(cfg), "initial_aeve_v1"),
            )


def _load_active(conn: Any) -> tuple[AEVEGenerationConfig, Any]:
    row = conn.execute(
        "SELECT generation,started_at,config_json,config_hash FROM paper_aeve_generations WHERE status='ACTIVE' ORDER BY generation DESC LIMIT 1"
    ).fetchone()
    if not row:
        return AEVEGenerationConfig(), row
    cfg, calculated_hash = _decode_generation_row(row)
    row = dict(row)
    row["config_hash"] = calculated_hash
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
        active_cfg, active_generation_row = _load_active(conn)
        if not active_generation_row:
            return 0
        log.info(
            "AEVE EVALUATOR HANDSHAKE | generation=%s | config_hash=%s | provenance_version=%s | config=%s | mode=shadow | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
            active_cfg.generation,
            active_generation_row.get("config_hash"),
            PROVENANCE_VERSION,
            json.dumps(generation_config_payload(active_cfg), sort_keys=True, separators=(",", ":")),
        )
        rows = list(conn.execute("""
            SELECT m.trade_id,m.regime,m.entry_time,m.exit_time,m.net_pnl,m.mfe_pct,m.mae_pct,
                   m.excursion_sample_count,l.quantity,l.entry_price,l.fees,l.feature_snapshot,
                   g.generation AS aeve_generation,g.config_json AS aeve_config_json,
                   g.config_hash AS aeve_config_hash
            FROM paper_regime_trade_metrics m
            JOIN trade_ledger l ON l.trade_id=m.trade_id
            JOIN LATERAL (
                SELECT generation,config_json,config_hash
                FROM paper_aeve_generations
                WHERE started_at <= m.entry_time
                ORDER BY started_at DESC,generation DESC
                LIMIT 1
            ) g ON TRUE
            WHERE m.strategy='oracle_council_v3'
              AND m.entry_time IS NOT NULL AND m.exit_time IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM paper_aeve_generation_outcomes o
                  WHERE o.generation=g.generation AND o.config_hash=g.config_hash
                    AND o.trade_id=m.trade_id
              )
            ORDER BY m.exit_time ASC LIMIT %s
        """, (max(1, int(limit)),)).fetchall())

        for row in rows:
            generation_row = {
                "generation": row.get("aeve_generation"),
                "config_json": row.get("aeve_config_json"),
                "config_hash": row.get("aeve_config_hash"),
            }
            cfg, config_hash = _decode_generation_row(generation_row)
            config_snapshot = asdict(cfg)
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
            # Reconstruct the consecutive Council loss streak strictly as of the
            # candidate entry. Rows closing at/after entry_time are excluded so
            # AEVE cannot learn from the candidate outcome or any future trade.
            prior_results = list(conn.execute("""
                SELECT net_pnl
                FROM paper_regime_trade_metrics
                WHERE strategy='oracle_council_v3' AND exit_time < %s
                ORDER BY exit_time DESC
                LIMIT %s
            """, (entry_time, max(1, int(cfg.max_loss_streak) + 1))).fetchall())
            loss_streak = 0
            for prior_result in prior_results:
                if _f(prior_result.get("net_pnl")) < 0:
                    loss_streak += 1
                else:
                    break
            gross_loss = _f(prior.get("gross_loss"))
            pf = (_f(prior.get("gross_win")) / gross_loss) if gross_loss > 0 else 0.0
            cost_pct = 0.0
            qty, price = _f(row.get("quantity")), _f(row.get("entry_price"))
            if qty > 0 and price > 0:
                cost_pct = max(0.0, (_f(row.get("fees")) / (qty * price)) * 100.0)
            features = row.get("feature_snapshot") if isinstance(row.get("feature_snapshot"), dict) else {}
            edge = None
            for key in ("net_expected_value_pct","expected_return_pct","forecast_return_pct","possible_move_pct","expected_move_pct","edge_pct"):
                if key in features and features.get(key) is not None:
                    edge = _f(features.get(key))
                    break
            dip_depth = features.get("dip_depth_pct")
            rebound = features.get("rebound_pct")
            # Fail closed when immutable entry-time AEVE evidence is absent. Never
            # substitute post-entry excursion or realized P&L for candidate inputs.
            entry_evidence_complete = edge is not None and dip_depth is not None and rebound is not None
            decision = score_entry(
                expected_net_edge_pct=edge if entry_evidence_complete else 0.0,
                mfe_pct=max(0.0, _f(prior.get("mfe"))),
                mae_pct=min(0.0, _f(prior.get("mae"))),
                round_trip_cost_pct=cost_pct,
                loss_streak=loss_streak,
                price_above_recent_low_pct=(max(0.0, _f(rebound)) * 100.0) if entry_evidence_complete else 0.0,
                rebound_from_low_pct=(max(0.0, _f(rebound)) * 100.0) if entry_evidence_complete else 0.0,
                rsi=(_f(features.get("rsi_14"), 50.0) if features.get("rsi_14") is not None else None),
                trend_confirmed=False,
                regime_expectancy_positive=bool(samples >= 30 and _f(prior.get("expectancy")) > 0),
                profit_factor=pf,
                min_samples=samples,
                config=config_snapshot,
            )
            conn.execute("""
                INSERT INTO paper_aeve_generation_outcomes(
                    generation,trade_id,observed_at,net_pnl,mfe_pct,mae_pct,
                    excursion_sample_count,cost_pct,would_trade,score,config_json,config_hash,
                    provenance_version
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                ON CONFLICT (generation,trade_id) DO NOTHING
            """, (
                cfg.generation,row.get("trade_id"),row.get("exit_time"),_f(row.get("net_pnl")),
                row.get("mfe_pct"),row.get("mae_pct"),int(row.get("excursion_sample_count") or 0),
                cost_pct,(decision.would_trade if entry_evidence_complete else False),decision.score,
                json.dumps(config_snapshot),config_hash,PROVENANCE_VERSION,
            ))
            log.info(
                "AEVE SHADOW RESULT | trade_id=%s | generation=%s | config_hash=%s | provenance_version=%s | would_trade=%s | score=%.6f | mode=shadow | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
                row.get("trade_id"), cfg.generation, config_hash, PROVENANCE_VERSION,
                bool(decision.would_trade if entry_evidence_complete else False), decision.score,
            )
            created += 1
    return created


def generation_research_report(conn: Any, generation: int, config_hash: str) -> dict[str, Any]:
    """Freeze auditable post-cost evidence for a completed AEVE research generation."""
    row = conn.execute("""
        SELECT COUNT(*)::int AS window_trades,
               COUNT(*) FILTER (WHERE would_trade)::int AS accepted_trades,
               AVG(net_pnl) FILTER (WHERE would_trade) AS expectancy,
               SUM(net_pnl) FILTER (WHERE would_trade) AS net_pnl,
               SUM(CASE WHEN would_trade AND net_pnl>0 THEN net_pnl ELSE 0 END) AS gross_win,
               ABS(SUM(CASE WHEN would_trade AND net_pnl<0 THEN net_pnl ELSE 0 END)) AS gross_loss,
               AVG(mfe_pct) FILTER (WHERE would_trade AND excursion_sample_count>0) AS avg_mfe_pct,
               AVG(mae_pct) FILTER (WHERE would_trade AND excursion_sample_count>0) AS avg_mae_pct,
               AVG(cost_pct) FILTER (WHERE would_trade) AS avg_cost_pct,
               COUNT(*) FILTER (WHERE NOT would_trade)::int AS rejected_candidates,
               COUNT(*) FILTER (WHERE NOT would_trade AND net_pnl<0)::int AS avoided_losses,
               COUNT(*) FILTER (WHERE NOT would_trade AND net_pnl>0)::int AS missed_winners
        FROM paper_aeve_generation_outcomes
        WHERE generation=%s AND config_hash=%s AND provenance_version=%s
    """, (generation, config_hash, PROVENANCE_VERSION)).fetchone() or {}
    gross_loss = _f(row.get("gross_loss"))
    gross_win = _f(row.get("gross_win"))
    return {
        "generation": generation,
        "config_hash": config_hash,
        "provenance_version": PROVENANCE_VERSION,
        "window_trades": int(row.get("window_trades") or 0),
        "accepted_trades": int(row.get("accepted_trades") or 0),
        "expectancy": _f(row.get("expectancy")),
        "net_pnl": _f(row.get("net_pnl")),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "avg_mfe_pct": _f(row.get("avg_mfe_pct")),
        "avg_mae_pct": _f(row.get("avg_mae_pct")),
        "avg_cost_pct": _f(row.get("avg_cost_pct")),
        "rejected_candidates": int(row.get("rejected_candidates") or 0),
        "avoided_losses": int(row.get("avoided_losses") or 0),
        "missed_winners": int(row.get("missed_winners") or 0),
        "execution_impact": "NONE",
    }


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
        config_hash = row.get("config_hash")
        batch = conn.execute("""
            WITH x AS (
                SELECT net_pnl,mfe_pct,mae_pct,excursion_sample_count,cost_pct,would_trade
                FROM paper_aeve_generation_outcomes
                WHERE generation=%s AND config_hash=%s AND provenance_version=%s
                ORDER BY observed_at ASC
                LIMIT %s
            )
            SELECT COUNT(*) AS window_samples,
                   COUNT(*) FILTER (WHERE would_trade) AS samples,
                   AVG(net_pnl) FILTER (WHERE would_trade) AS expectancy,
                   SUM(CASE WHEN would_trade AND net_pnl>0 THEN net_pnl ELSE 0 END) AS gross_win,
                   ABS(SUM(CASE WHEN would_trade AND net_pnl<0 THEN net_pnl ELSE 0 END)) AS gross_loss,
                   AVG(CASE WHEN would_trade THEN CASE WHEN net_pnl>0 THEN 1.0 ELSE 0.0 END END) AS win_rate,
                   AVG(mfe_pct) FILTER (WHERE would_trade AND excursion_sample_count>0) AS avg_mfe,
                   AVG(mae_pct) FILTER (WHERE would_trade AND excursion_sample_count>0) AS avg_mae,
                   AVG(cost_pct) FILTER (WHERE would_trade) AS avg_cost
            FROM x
        """, (cfg.generation, config_hash, PROVENANCE_VERSION, BATCH_SIZE)).fetchone() or {}
        window_samples = int(batch.get("window_samples") or 0)
        samples = int(batch.get("samples") or 0)
        if window_samples < BATCH_SIZE:
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
        if nxt.generation == cfg.generation:
            # A complete forward window with too few accepted AEVE samples is still
            # a completed research generation. Advance the generation identity
            # without relaxing any gate or manufacturing challenger acceptance.
            nxt = replace(cfg, generation=cfg.generation + 1)
            diagnosis = f"{diagnosis}_hold_formula"
        conn.execute("UPDATE paper_aeve_generations SET status='SUPERSEDED' WHERE generation=%s", (cfg.generation,))
        conn.execute("""
            INSERT INTO paper_aeve_generations(
                generation,config_json,config_hash,diagnosis,source_samples,source_expectancy,source_profit_factor,status
            ) VALUES (%s,%s::jsonb,%s,%s,%s,%s,%s,'ACTIVE')
        """, (
            nxt.generation, json.dumps(asdict(nxt)), generation_config_hash(nxt), diagnosis,
            window_samples, d.expectancy, d.profit_factor,
        ))
        log.info(
            "AEVE GENERATION ADVANCE | from=%s | from_config_hash=%s | to=%s | to_config_hash=%s | diagnosis=%s | window_samples=%s | accepted_samples=%s | expectancy=%.6f | pf=%.4f | mode=shadow | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
            cfg.generation, config_hash, nxt.generation, generation_config_hash(nxt),
            diagnosis, window_samples, d.samples, d.expectancy, d.profit_factor,
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
