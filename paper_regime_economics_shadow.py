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
_SCHEMA_VERSION = "regime-economics-v2-round-trip-cost"
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


def _normalize_market(market: Any) -> str:
    value = str(market or "crypto").strip().lower()
    if value == "stock":
        value = "cash"
    if value not in {"cash", "crypto"}:
        raise ValueError("market must be cash or crypto")
    return value


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


def _excursion_percentages(entry_price: Any, prices: list[float]) -> tuple[float | None, float | None]:
    """Return long-position MFE/MAE anchored to the factual entry point.

    Entry itself is a zero-percent excursion. Consequently long MFE can never be
    negative and long MAE can never be positive, even when all forward samples
    remain on one side of entry. The observed samples remain the only forward
    path evidence; this anchor does not invent an unobserved price.
    """
    entry = _num(entry_price)
    observed = [_num(price) for price in prices if _num(price) > 0]
    if entry <= 0 or not observed:
        return None, None
    returns = [((price / entry) - 1.0) * 100.0 for price in observed]
    return max(0.0, max(returns)), min(0.0, min(returns))


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
        conn.execute("ALTER TABLE paper_regime_trade_metrics ADD COLUMN IF NOT EXISTS round_trip_net_pnl DOUBLE PRECISION")
        conn.execute("ALTER TABLE paper_regime_trade_metrics ADD COLUMN IF NOT EXISTS round_trip_fees DOUBLE PRECISION")
        conn.execute("ALTER TABLE paper_regime_trade_metrics ADD COLUMN IF NOT EXISTS cost_provenance TEXT NOT NULL DEFAULT 'legacy_unknown'")


def repair_excursion_anchors() -> int:
    """Correct only impossible sign states produced by the initial shadow build."""
    if not active():
        return 0
    from database import connect

    with connect() as conn:
        result = conn.execute(
            """
            UPDATE paper_regime_trade_metrics
            SET mfe_pct=GREATEST(0.0,mfe_pct),
                mae_pct=LEAST(0.0,mae_pct),
                schema_version=%s
            WHERE (mfe_pct < 0.0 OR mae_pct > 0.0)
            """,
            (_SCHEMA_VERSION,),
        )
        return max(0, int(getattr(result, "rowcount", 0) or 0))


def _round_trip_accounting(conn: Any, trade: dict[str, Any]) -> tuple[float | None,float | None,str]:
    """Recover exact round-trip costs from the immutable entry lot when unique."""
    market = _normalize_market(trade.get("market"))
    symbol = str(trade.get("symbol") or "").upper()
    entry_time = trade.get("entry_time")
    entry_price = _num(trade.get("entry_price"))
    quantity = abs(_num(trade.get("quantity")))
    signal_id = str(trade.get("entry_signal_id") or "").strip()
    decision_id = str(trade.get("entry_decision_id") or "").strip()
    if not symbol or entry_time is None or entry_price <= 0 or quantity <= 0 or not signal_id:
        return None,None,"incomplete_entry_provenance"
    rows=list(conn.execute(
        """
        SELECT entry_fees,quantity_opened
        FROM position_lots
        WHERE market=%s AND symbol=%s
          AND NULLIF(opened_at,'')::timestamptz=%s
          AND entry_signal_id=%s
          AND (%s='' OR COALESCE(entry_decision_id,'')=%s)
          AND ABS(entry_price-%s) <= GREATEST(1e-10,ABS(%s)*1e-9)
        ORDER BY id ASC LIMIT 2
        """,
        (market,symbol,entry_time,signal_id,decision_id,decision_id,entry_price,entry_price),
    ).fetchall())
    if len(rows) != 1:
        return None,None,"ambiguous_or_missing_entry_lot"
    lot=rows[0]
    opened_qty=abs(_num(lot.get("quantity_opened")))
    if opened_qty <= 0 or quantity-opened_qty > max(1e-10,opened_qty*1e-9):
        return None,None,"invalid_entry_lot_quantity"
    entry_fee=max(0.0,_num(lot.get("entry_fees"))) * (quantity/opened_qty)
    exit_fee=max(0.0,_num(trade.get("fees")))
    gross=_num(trade.get("gross_pnl"))
    total_fee=entry_fee+exit_fee
    return gross-total_fee,total_fee,"exact_lot"


def sample_open_positions(market: str = "crypto") -> int:
    """Persist observed canonical paper prices for forward MFE/MAE measurement."""
    if not active():
        return 0
    from database import connect

    normalized_market = _normalize_market(market)
    inserted = 0
    now = datetime.now(timezone.utc)
    with connect() as conn:
        positions = list(
            conn.execute(
                """
                SELECT symbol, current_price, average_price, entry_price
                FROM positions
                WHERE market=%s AND COALESCE(quantity,0) > 0
                """,
                (normalized_market,),
            ).fetchall()
        )
        for row in positions:
            price = _num(row.get("current_price")) or _num(row.get("average_price")) or _num(row.get("entry_price"))
            if price <= 0:
                continue
            conn.execute(
                """
                INSERT INTO paper_regime_price_samples(market,symbol,observed_at,price,source,schema_version)
                VALUES (%s,%s,%s,%s,'canonical_position',%s)
                """,
                (normalized_market, str(row.get("symbol") or "").upper(), now, price, _SCHEMA_VERSION),
            )
            inserted += 1
    return inserted


def _memory_regime(conn: Any, market: str, symbol: str, entry_time: datetime | None) -> str | None:
    if not entry_time:
        return None
    try:
        item = conn.execute(
            """
            SELECT regime FROM market_memory_observations
            WHERE market=%s AND symbol=%s
              AND NULLIF(created_at,'')::timestamptz <= %s
            ORDER BY NULLIF(created_at,'')::timestamptz DESC LIMIT 1
            """,
            (market, symbol, entry_time),
        ).fetchone()
        return str(item.get("regime") or "").strip() if item else None
    except Exception:
        return None


def finalize_closed_trades(limit: int = 250, market: str = "crypto") -> int:
    """Materialize regime economics from canonical closes without changing execution."""
    if not active():
        return 0
    from database import connect
    from paper_strategy_economics import normalize_strategy_identity

    normalized_market = _normalize_market(market)
    created = 0
    with connect() as conn:
        trades = list(
            conn.execute(
                """
                SELECT trade_id, market, symbol, strategy, quantity, gross_pnl, net_pnl, fees,
                       entry_signal_id,entry_decision_id,
                       NULLIF(entry_time,'')::timestamptz AS entry_time,
                       NULLIF(exit_time,'')::timestamptz AS exit_time,
                       entry_price, exit_price, feature_snapshot
                FROM trade_ledger
                WHERE market=%s AND side='SELL' AND exit_time IS NOT NULL
                ORDER BY NULLIF(exit_time,'')::timestamptz DESC
                LIMIT %s
                """,
                (normalized_market, max(1, int(limit))),
            ).fetchall()
        )
        for trade in trades:
            trade_id = str(trade.get("trade_id") or "").strip()
            if not trade_id:
                continue
            existing = conn.execute(
                "SELECT trade_id,cost_provenance FROM paper_regime_trade_metrics WHERE trade_id=%s LIMIT 1",
                (trade_id,),
            ).fetchone() or {}

            symbol = str(trade.get("symbol") or "").upper()
            entry_time = trade.get("entry_time")
            exit_time = trade.get("exit_time")
            entry_price = _num(trade.get("entry_price"))
            exit_price = _num(trade.get("exit_price"))
            regime = classify_regime(
                feature_snapshot=trade.get("feature_snapshot"),
                memory_regime=_memory_regime(conn, normalized_market, symbol, entry_time),
            )

            prices: list[float] = []
            if entry_time and exit_time:
                sampled = conn.execute(
                    """
                    SELECT price FROM paper_regime_price_samples
                    WHERE market=%s AND symbol=%s AND observed_at BETWEEN %s AND %s
                    ORDER BY observed_at ASC
                    """,
                    (normalized_market, symbol, entry_time, exit_time),
                ).fetchall()
                prices = [_num(item.get("price")) for item in sampled if _num(item.get("price")) > 0]

            # Entry is the factual 0% excursion anchor; forward samples supply the path.
            excursion_count = len(prices)
            mfe_pct, mae_pct = _excursion_percentages(entry_price, prices)
            round_trip_net,round_trip_fees,cost_provenance=_round_trip_accounting(conn,dict(trade))
            if existing:
                if cost_provenance == "exact_lot" and str(existing.get("cost_provenance") or "") != "exact_lot":
                    conn.execute(
                        """UPDATE paper_regime_trade_metrics
                           SET round_trip_net_pnl=%s,round_trip_fees=%s,cost_provenance=%s,schema_version=%s
                           WHERE trade_id=%s""",
                        (round_trip_net,round_trip_fees,cost_provenance,_SCHEMA_VERSION,trade_id),
                    )
                    created += 1
                continue

            conn.execute(
                """
                INSERT INTO paper_regime_trade_metrics(
                    trade_id,market,symbol,strategy,regime,entry_time,exit_time,
                    entry_price,exit_price,net_pnl,fees,mfe_pct,mae_pct,
                    excursion_sample_count,schema_version,round_trip_net_pnl,round_trip_fees,cost_provenance
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (trade_id) DO NOTHING
                """,
                (
                    trade_id, normalized_market, symbol, normalize_strategy_identity(trade.get("strategy")), regime,
                    entry_time, exit_time, entry_price or None, exit_price or None,
                    _num(trade.get("net_pnl")), max(0.0, _num(trade.get("fees"))),
                    mfe_pct, mae_pct, excursion_count, _SCHEMA_VERSION,
                    round_trip_net,round_trip_fees,cost_provenance,
                ),
            )
            created += 1
    return created


def emit_summary(market: str | None = None) -> None:
    if not active():
        return
    try:
        from database import connect
        normalized_market = _normalize_market(market) if market is not None else None
        with connect() as conn:
            rows = list(
                conn.execute(
                    """
                    SELECT market, strategy, regime, COUNT(*) AS samples,
                           SUM(COALESCE(round_trip_net_pnl,net_pnl)) AS net_pnl,
                           SUM(COALESCE(round_trip_fees,fees)) AS fees,
                           AVG(COALESCE(round_trip_net_pnl,net_pnl)) AS expectancy,
                           AVG(mfe_pct) FILTER (WHERE excursion_sample_count > 0) AS avg_mfe_pct,
                           AVG(mae_pct) FILTER (WHERE excursion_sample_count > 0) AS avg_mae_pct,
                           SUM(CASE WHEN excursion_sample_count > 0 THEN 1 ELSE 0 END) AS excursion_trades
                    FROM paper_regime_trade_metrics
                    WHERE (%s::text IS NULL OR market=%s::text)
                    GROUP BY market, strategy, regime
                    ORDER BY samples DESC
                    LIMIT 20
                    """,
                    (normalized_market, normalized_market),
                ).fetchall()
            )
        for row in rows:
            log.info(
                "PAPER REGIME ECONOMICS | market=%s | strategy=%s | regime=%s | samples=%s | net_pnl=%.4f | fees=%.4f | expectancy=%.6f | avg_mfe_pct=%s | avg_mae_pct=%s | excursion_trades=%s | mode=shadow | execution_impact=NONE | live_trading=DISARMED",
                row.get("market"), row.get("strategy"), row.get("regime"), row.get("samples"),
                _num(row.get("net_pnl")), _num(row.get("fees")), _num(row.get("expectancy")),
                "NA" if row.get("avg_mfe_pct") is None else f"{_num(row.get('avg_mfe_pct')):.4f}",
                "NA" if row.get("avg_mae_pct") is None else f"{_num(row.get('avg_mae_pct')):.4f}",
                row.get("excursion_trades"),
            )
    except Exception as exc:
        log.warning("PAPER REGIME ECONOMICS | status=UNAVAILABLE | reason=%s", exc.__class__.__name__)


def _loop(interval_seconds: float, market: str) -> None:
    cycles = 0
    while not _STOP.wait(interval_seconds):
        if not active():
            return
        try:
            sample_open_positions(market)
            finalize_closed_trades(market=market)
            cycles += 1
            if cycles == 1 or cycles % 10 == 0:
                emit_summary(market)
        except Exception as exc:
            log.warning("PAPER REGIME ECONOMICS | sampler=ERROR | reason=%s", exc.__class__.__name__)


def install_paper_regime_economics_shadow(market: str = "crypto") -> bool:
    """Start shadow-only regime/MFE/MAE telemetry; never mutates execution state."""
    global _THREAD
    if not active():
        return False
    normalized_market = _normalize_market(market)
    ensure_schema()
    repaired = repair_excursion_anchors()
    if repaired:
        log.info(
            "PAPER REGIME ECONOMICS | excursion_anchor_repair=%s | execution_impact=NONE | live_trading=DISARMED",
            repaired,
        )
    # Capture immediately so fresh positions have an initial observed point.
    try:
        sample_open_positions(normalized_market)
        finalize_closed_trades(market=normalized_market)
        emit_summary(normalized_market)
    except Exception as exc:
        log.warning("PAPER REGIME ECONOMICS | startup=DEGRADED | reason=%s", exc.__class__.__name__)
    if _THREAD and _THREAD.is_alive():
        return True
    interval = max(15.0, _num(os.getenv("PAPER_REGIME_SAMPLE_SECONDS", "60"), 60.0))
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, args=(interval, normalized_market), name=f"paper-regime-shadow-{normalized_market}", daemon=True)
    _THREAD.start()
    log.info(
        "PAPER REGIME ECONOMICS | market=%s | active=True | version=%s | mode=shadow | sample_seconds=%.1f | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
        normalized_market, _SCHEMA_VERSION, interval,
    )
    return True
