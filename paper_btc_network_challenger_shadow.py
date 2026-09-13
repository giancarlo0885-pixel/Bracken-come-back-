from __future__ import annotations

import json
import logging
import math
import os
import threading
from typing import Any


log = logging.getLogger("btc-network-shadow-challenger")
_SCHEMA_VERSION = "btc-network-technical-shadow-v1"
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
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _feature_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def classify_cohort(features: dict[str, Any]) -> str:
    """Stable entry-time cohort using only persisted network/technical evidence."""
    if not features or not features.get("btc_network_source"):
        return "network_unavailable"

    security = _num(features.get("btc_network_security_score"))
    activity = _num(features.get("btc_network_activity_score"))
    fees = _num(features.get("btc_network_fee_pressure"))
    mempool = _num(features.get("btc_network_mempool_pressure"))
    ta = _num(features.get("ta_consensus"), _num(features.get("ta_consensus_score")))
    schwager = _num(features.get("schwager_setup_score"))
    dip = _num(features.get("dip_rebound_score"))

    network_score = (security + activity - fees - mempool) / 4.0
    technical_score = (ta + schwager + dip) / 3.0

    if network_score >= 0.15:
        network = "network_favorable"
    elif network_score <= -0.15:
        network = "network_adverse"
    else:
        network = "network_mixed"

    if technical_score >= 0.20:
        technical = "technical_bullish"
    elif technical_score <= -0.20:
        technical = "technical_bearish"
    else:
        technical = "technical_mixed"
    return f"{network}__{technical}"


def mean_ci95(values: list[float]) -> tuple[float, float, float]:
    clean = [float(v) for v in values if math.isfinite(float(v))]
    if not clean:
        return 0.0, 0.0, 0.0
    mean = sum(clean) / len(clean)
    if len(clean) < 2:
        return mean, mean, mean
    variance = sum((v - mean) ** 2 for v in clean) / (len(clean) - 1)
    se = math.sqrt(max(0.0, variance) / len(clean))
    margin = 1.96 * se
    return mean, mean - margin, mean + margin


def mature_negative(values: list[float], minimum_samples: int = 25) -> bool:
    if len(values) < max(2, int(minimum_samples)):
        return False
    _mean, _low, high = mean_ci95(values)
    return high < 0.0


def ensure_schema() -> None:
    if not active():
        return
    from database import connect
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_btc_network_trade_metrics (
                trade_id TEXT PRIMARY KEY,
                entry_time TIMESTAMPTZ,
                exit_time TIMESTAMPTZ,
                cohort TEXT NOT NULL,
                net_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
                fees DOUBLE PRECISION NOT NULL DEFAULT 0,
                mfe_pct DOUBLE PRECISION,
                mae_pct DOUBLE PRECISION,
                feature_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                schema_version TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_btc_network_shadow_cohort_exit ON paper_btc_network_trade_metrics(cohort, exit_time)"
        )


def finalize_closed_trades(limit: int = 500) -> int:
    """Persist closed BTC outcomes with immutable entry-time features only."""
    if not active():
        return 0
    from database import connect
    created = 0
    with connect() as conn:
        rows = list(conn.execute(
            """
            SELECT t.trade_id,
                   NULLIF(t.entry_time,'')::timestamptz AS entry_time,
                   NULLIF(t.exit_time,'')::timestamptz AS exit_time,
                   t.net_pnl, t.fees, t.feature_snapshot,
                   r.mfe_pct, r.mae_pct
            FROM trade_ledger t
            LEFT JOIN paper_regime_trade_metrics r ON r.trade_id=t.trade_id
            WHERE t.market='crypto' AND t.side='SELL'
              AND UPPER(REPLACE(t.symbol,'/','-')) IN ('BTC-USD','BTCUSD')
              AND t.exit_time IS NOT NULL
            ORDER BY NULLIF(t.exit_time,'')::timestamptz DESC
            LIMIT %s
            """,
            (max(1, int(limit)),),
        ).fetchall())
        for row in rows:
            trade_id = str(row.get("trade_id") or "").strip()
            if not trade_id:
                continue
            exists = conn.execute(
                "SELECT 1 FROM paper_btc_network_trade_metrics WHERE trade_id=%s LIMIT 1",
                (trade_id,),
            ).fetchone()
            if exists:
                continue
            features = _feature_obj(row.get("feature_snapshot"))
            cohort = classify_cohort(features)
            conn.execute(
                """
                INSERT INTO paper_btc_network_trade_metrics(
                    trade_id,entry_time,exit_time,cohort,net_pnl,fees,mfe_pct,mae_pct,feature_snapshot,schema_version
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                ON CONFLICT (trade_id) DO NOTHING
                """,
                (
                    trade_id, row.get("entry_time"), row.get("exit_time"), cohort,
                    _num(row.get("net_pnl")), max(0.0, _num(row.get("fees"))),
                    row.get("mfe_pct"), row.get("mae_pct"),
                    json.dumps(features, sort_keys=True, default=str), _SCHEMA_VERSION,
                ),
            )
            created += 1
    return created


def emit_summary() -> None:
    if not active():
        return
    try:
        from database import connect
        minimum = max(10, int(_num(os.getenv("BTC_NETWORK_SHADOW_MIN_SAMPLES", "25"), 25.0)))
        with connect() as conn:
            cohorts = list(conn.execute(
                """
                SELECT cohort, COUNT(*) AS samples, SUM(net_pnl) AS net_pnl,
                       SUM(fees) AS fees, AVG(net_pnl) AS expectancy,
                       AVG(mfe_pct) AS avg_mfe_pct, AVG(mae_pct) AS avg_mae_pct
                FROM paper_btc_network_trade_metrics
                WHERE cohort <> 'network_unavailable'
                GROUP BY cohort ORDER BY samples DESC
                """
            ).fetchall())
            for row in cohorts:
                pnl_rows = list(conn.execute(
                    "SELECT net_pnl FROM paper_btc_network_trade_metrics WHERE cohort=%s ORDER BY exit_time ASC",
                    (row.get("cohort"),),
                ).fetchall())
                values = [_num(item.get("net_pnl")) for item in pnl_rows]
                mean, low, high = mean_ci95(values)
                abstain = mature_negative(values, minimum)
                avoided_loss = max(0.0, -sum(values)) if abstain else 0.0
                log.info(
                    "BTC NETWORK SHADOW | cohort=%s | samples=%s | net_pnl=%.4f | fees=%.4f | expectancy=%.6f | ci95=[%.6f,%.6f] | avg_mfe_pct=%s | avg_mae_pct=%s | challenger=%s | historical_loss_avoided_if_abstained=%.4f | execution_impact=NONE | live_trading=DISARMED",
                    row.get("cohort"), row.get("samples"), _num(row.get("net_pnl")),
                    _num(row.get("fees")), mean, low, high,
                    "NA" if row.get("avg_mfe_pct") is None else f"{_num(row.get('avg_mfe_pct')):.4f}",
                    "NA" if row.get("avg_mae_pct") is None else f"{_num(row.get('avg_mae_pct')):.4f}",
                    "SHADOW_ABSTAIN" if abstain else "OBSERVE", avoided_loss,
                )
    except Exception as exc:
        log.warning("BTC NETWORK SHADOW | status=UNAVAILABLE | reason=%s", exc.__class__.__name__)


def _loop(interval_seconds: float) -> None:
    cycles = 0
    while not _STOP.wait(interval_seconds):
        if not active():
            return
        try:
            finalize_closed_trades()
            cycles += 1
            if cycles == 1 or cycles % 10 == 0:
                emit_summary()
        except Exception as exc:
            log.warning("BTC NETWORK SHADOW | sampler=ERROR | reason=%s", exc.__class__.__name__)


def install_btc_network_shadow_challenger() -> bool:
    """Start measurement-only BTC network+technical cohort analysis."""
    global _THREAD
    if not active():
        return False
    ensure_schema()
    try:
        finalize_closed_trades()
        emit_summary()
    except Exception as exc:
        log.warning("BTC NETWORK SHADOW | startup=DEGRADED | reason=%s", exc.__class__.__name__)
    if _THREAD and _THREAD.is_alive():
        return True
    interval = max(60.0, _num(os.getenv("BTC_NETWORK_SHADOW_SECONDS", "300"), 300.0))
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, args=(interval,), name="btc-network-shadow", daemon=True)
    _THREAD.start()
    log.info(
        "BTC NETWORK SHADOW | active=True | version=%s | min_samples=%s | mode=shadow | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
        _SCHEMA_VERSION, os.getenv("BTC_NETWORK_SHADOW_MIN_SAMPLES", "25"),
    )
    return True
