from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import os
import threading
import time
from typing import Any


log = logging.getLogger("paper-regime-economics")
_SCHEMA_VERSION = "regime-economics-v1-shadow"
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


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def classify_regime(*, feature_snapshot: Any = None, memory_regime: Any = None) -> str:
    """Return a stable shadow-only market-regime label.

    Existing market-memory provenance wins when present. Otherwise classify only
    from entry-time features already persisted with the canonical trade ledger.
    No P&L or future information is used.
    """
    existing = str(memory_regime or "").strip().lower().replace(" ", "_")
    if existing:
        return existing[:80]

    features = _json_obj(feature_snapshot)
    trend = _num(features.get("trend_strength", features.get("trend", 0.0)))
    momentum = _num(features.get("momentum_20d", 0.0))
    volatility = _num(features.get("volatility_20d", features.get("volatility", 0.0)))

    directional = trend if abs(trend) >= abs(momentum) else momentum
    if directional >= 0.05:
        direction = "trend_up"
    elif directional <= -0.05:
        direction = "trend_down"
    else:
        direction = "range"

    if volatility <= 0:
        vol = "vol_unknown"
    elif volatility >= 0.60:
        vol = "high_vol"
    else:
        vol = "low_vol"
    return f"{direction}__{vol}"


def ensure_schema() -> None:
    if not active():
        return
    from database import connect

    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_regime_price_samples (
                id BIGSERIAL PRIMARY KEY,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                source TEXT NOT NULL DEFAULT 'canonical_position',
                schema_version TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paper_regime_samples_symbol_time
            ON paper_regime_price_samples(market, symbol, observed_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_regime_trade_metrics (
                trade_id TEXT PRIMARY KEY,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                regime TEXT NOT NULL,
                entry_time TIMESTAMPTZ,
                exit_time TIMESTAMPTZ,
                entry_price DOUBLE PRECISION,
                exit_price DOUBLE PRECISION,
                net_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
                fees DOUBLE PRECISION NOT NULL DEFAULT 0,
                mfe_pct DOUBLE PRECISION,
                mae_pct DOUBLE PRECISION,
                excursion_sample_count INTEGER NOT NULL DEFAULT 0,
                schema_version TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_paper_regime_metrics_strategy_regime
            ON paper_regime_trade_metrics(strategy, regime, exit_time)
            """
        )


def sample_open_positions() -> int:
    """Persist observed canonical paper prices for forward MFE/MAE measurement."""
    if not active():
        return 0
    from database import connect

    inserted = 0
    now = datetime.now(timezone.utc)
    with connect() as conn:
        positions = list(
            conn.execute(
                """
                SELECT symbol, current_price, average_price, entry_price
                FROM positions
                WHERE market='crypto' AND COALESCE(quantity,0) > 0
                """
            ).fetchall()
        )
        for row in positions:
            price = _num(row.get("current_price")) or _num(row.get("average_price")) or _num(row.get("entry_price"))
            if price <= 0:
                continue
            conn.execute(
                """
                INSERT INTO paper_regime_price_samples(market,symbol,observed_at,price,source,schema_version)
                VALUES ('crypto',%s,%s,%s,'canonical_position',%s)
                """,
                (str(row.get("symbol") or "").upper(), now, price, _SCHEMA_VERSION),
            )
            inserted += 1
    return inserted


def _memory_regime(conn: Any, symbol: str, entry_time: datetime | None) -> str | None:
    if not entry_time:
        return None
    try:
        item = conn.execute(
            """
            SELECT regime FROM market_memory_observations
            WHERE market='crypto' AND symbol=%s
              AND NULLIF(created_at,'')::timestamptz <= %s
            ORDER BY NULLIF(created_at,'')::timestamptz DESC LIMIT 1
            """,
            (symbol, entry_time),
        ).fetchone()
        return str(item.get("regime") or "").strip() if item else None
    except Exception:
        return None


def finalize_closed_trades(limit: int = 250) -> int:
    """Materialize regime economics from canonical closes without changing execution."""
    if not active():
        return 0
    from database import connect
    from paper_strategy_economics import normalize_strategy_identity

    created = 0
    with connect() as conn:
        trades = list(
            conn.execute(
                """
                SELECT trade_id, symbol, strategy, net_pnl, fees,
                       NULLIF(entry_time,'')::timestamptz AS entry_time,
                       NULLIF(exit_time,'')::timestamptz AS exit_time,
                       entry_price, exit_price, feature_snapshot
                FROM trade_ledger
                WHERE market='crypto' AND side='SELL' AND exit_time IS NOT NULL
                ORDER BY NULLIF(exit_time,'')::timestamptz DESC
                LIMIT %s
                """,
                (max(1, int(limit)),),
            ).fetchall()
        )
        for trade in trades:
            trade_id = str(trade.get("trade_id") or "").strip()
            if not trade_id:
                continue
            exists = conn.execute(
                "SELECT 1 FROM paper_regime_trade_metrics WHERE trade_id=%s LIMIT 1",
                (trade_id,),
            ).fetchone()
            if exists:
                continue

            symbol = str(trade.get("symbol") or "").upper()
            entry_time = trade.get("entry_time")
            exit_time = trade.get("exit_time")
            entry_price = _num(trade.get("entry_price"))
            exit_price = _num(trade.get("exit_price"))
            regime = classify_regime(
                feature_snapshot=trade.get("feature_snapshot"),
                memory_regime=_memory_regime(conn, symbol, entry_time),
            )

            prices: list[float] = []
            if entry_time and exit_time:
                sampled = conn.execute(
                    """
                    SELECT price FROM paper_regime_price_samples
                    WHERE market='crypto' AND symbol=%s AND observed_at BETWEEN %s AND %s
                    ORDER BY observed_at ASC
                    """,
                    (symbol, entry_time, exit_time),
                ).fetchall()
                prices = [_num(item.get("price")) for item in sampled if _num(item.get("price")) > 0]

            # Entry/exit endpoints are canonical facts, not excursion samples.
            excursion_count = len(prices)
            mfe_pct = None
            mae_pct = None
            if entry_price > 0 and prices:
                mfe_pct = ((max(prices) / entry_price) - 1.0) * 100.0
                mae_pct = ((min(prices) / entry_price) - 1.0) * 100.0

            conn.execute(
                """
                INSERT INTO paper_regime_trade_metrics(
                    trade_id,market,symbol,strategy,regime,entry_time,exit_time,
                    entry_price,exit_price,net_pnl,fees,mfe_pct,mae_pct,
                    excursion_sample_count,schema_version
                ) VALUES (%s,'crypto',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (trade_id) DO NOTHING
                """,
                (
                    trade_id, symbol, normalize_strategy_identity(trade.get("strategy")), regime,
                    entry_time, exit_time, entry_price or None, exit_price or None,
                    _num(trade.get("net_pnl")), max(0.0, _num(trade.get("fees"))),
                    mfe_pct, mae_pct, excursion_count, _SCHEMA_VERSION,
                ),
            )
            created += 1
    return created


def emit_summary() -> None:
    if not active():
        return
    try:
        from database import connect
        with connect() as conn:
            rows = list(
                conn.execute(
                    """
                    SELECT strategy, regime, COUNT(*) AS samples,
                           SUM(net_pnl) AS net_pnl, SUM(fees) AS fees,
                           AVG(net_pnl) AS expectancy,
                           AVG(mfe_pct) FILTER (WHERE excursion_sample_count > 0) AS avg_mfe_pct,
                           AVG(mae_pct) FILTER (WHERE excursion_sample_count > 0) AS avg_mae_pct,
                           SUM(CASE WHEN excursion_sample_count > 0 THEN 1 ELSE 0 END) AS excursion_trades
                    FROM paper_regime_trade_metrics
                    GROUP BY strategy, regime
                    ORDER BY samples DESC
                    LIMIT 20
                    """
                ).fetchall()
            )
        for row in rows:
            log.info(
                "PAPER REGIME ECONOMICS | strategy=%s | regime=%s | samples=%s | net_pnl=%.4f | fees=%.4f | expectancy=%.6f | avg_mfe_pct=%s | avg_mae_pct=%s | excursion_trades=%s | mode=shadow | execution_impact=NONE | live_trading=DISARMED",
                row.get("strategy"), row.get("regime"), row.get("samples"),
                _num(row.get("net_pnl")), _num(row.get("fees")), _num(row.get("expectancy")),
                "NA" if row.get("avg_mfe_pct") is None else f"{_num(row.get('avg_mfe_pct')):.4f}",
                "NA" if row.get("avg_mae_pct") is None else f"{_num(row.get('avg_mae_pct')):.4f}",
                row.get("excursion_trades"),
            )
    except Exception as exc:
        log.warning("PAPER REGIME ECONOMICS | status=UNAVAILABLE | reason=%s", exc.__class__.__name__)


def _loop(interval_seconds: float) -> None:
    cycles = 0
    while not _STOP.wait(interval_seconds):
        if not active():
            return
        try:
            sample_open_positions()
            finalize_closed_trades()
            cycles += 1
            if cycles == 1 or cycles % 10 == 0:
                emit_summary()
        except Exception as exc:
            log.warning("PAPER REGIME ECONOMICS | sampler=ERROR | reason=%s", exc.__class__.__name__)


def install_paper_regime_economics_shadow() -> bool:
    """Start shadow-only regime/MFE/MAE telemetry; never mutates execution state."""
    global _THREAD
    if not active():
        return False
    ensure_schema()
    # Capture immediately so fresh positions have an initial observed point.
    try:
        sample_open_positions()
        finalize_closed_trades()
        emit_summary()
    except Exception as exc:
        log.warning("PAPER REGIME ECONOMICS | startup=DEGRADED | reason=%s", exc.__class__.__name__)
    if _THREAD and _THREAD.is_alive():
        return True
    interval = max(15.0, _num(os.getenv("PAPER_REGIME_SAMPLE_SECONDS", "60"), 60.0))
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, args=(interval,), name="paper-regime-shadow", daemon=True)
    _THREAD.start()
    log.info(
        "PAPER REGIME ECONOMICS | active=True | version=%s | mode=shadow | sample_seconds=%.1f | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
        _SCHEMA_VERSION, interval,
    )
    return True
