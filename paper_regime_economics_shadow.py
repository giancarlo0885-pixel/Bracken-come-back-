from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import json
import hashlib
import math
import os
import threading
import time
from typing import Any


log = logging.getLogger("paper-regime-economics")
_SCHEMA_VERSION = "regime-economics-v4-quote-observations"
_ENTRY_READY_VERSION = _SCHEMA_VERSION + "-entry-ready"
_THREAD: threading.Thread | None = None
_STOP = threading.Event()
_QUOTE_LOCK = threading.Lock()
_QUOTE_OBSERVATIONS: dict[tuple[str, str], dict[str, Any]] = {}
_MAX_QUOTE_OBSERVATIONS = 64


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


def _excursion_temporal_metrics(
    entry_price: Any,
    entry_time: datetime | None,
    samples: list[dict[str, Any]],
) -> tuple[float | None, float | None, datetime | None, datetime | None, int | None, int | None]:
    """Reduce observed forward samples into anchored excursion magnitude and timing.

    The factual entry is the 0% anchor. If no forward sample crosses above (below)
    entry, MFE (MAE) is 0% at entry time. Missing valid forward observations remain
    unavailable rather than being fabricated as zero-second evidence.
    """
    entry = _num(entry_price)
    if entry <= 0 or entry_time is None:
        return None, None, None, None, None, None
    valid: list[tuple[datetime, float]] = []
    for sample in samples:
        observed_at = sample.get("observed_at")
        price = _num(sample.get("price"))
        if observed_at is not None and price > 0:
            valid.append((observed_at, ((price / entry) - 1.0) * 100.0))
    if not valid:
        return None, None, None, None, None, None

    positive = [(ts, ret) for ts, ret in valid if ret > 0.0]
    negative = [(ts, ret) for ts, ret in valid if ret < 0.0]
    if positive:
        mfe_pct = max(ret for _, ret in positive)
        mfe_time = next(ts for ts, ret in positive if ret == mfe_pct)
    else:
        mfe_pct, mfe_time = 0.0, entry_time
    if negative:
        mae_pct = min(ret for _, ret in negative)
        mae_time = next(ts for ts, ret in negative if ret == mae_pct)
    else:
        mae_pct, mae_time = 0.0, entry_time

    time_to_mfe = max(0, int((mfe_time - entry_time).total_seconds()))
    time_to_mae = max(0, int((mae_time - entry_time).total_seconds()))
    return mfe_pct, mae_pct, mfe_time, mae_time, time_to_mfe, time_to_mae


def _relation_columns(conn: Any, relation: str) -> dict[str, bool]:
    rows = list(conn.execute(
        """
        SELECT a.attname,a.attnotnull
        FROM pg_attribute a
        WHERE a.attrelid=to_regclass(%s)
          AND a.attnum>0 AND NOT a.attisdropped
        """,
        (relation,),
    ).fetchall())
    return {str(row.get("attname")): bool(row.get("attnotnull")) for row in rows}


def ensure_schema() -> None:
    if not active():
        return
    from database import connect

    with connect() as conn:
        # Stock and crypto can start concurrently. Serialize only schema checks;
        # once the columns exist, restarts avoid heavyweight ALTER TABLE locks.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("paper_regime_economics_schema_v2",),
        )
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
                entry_pattern TEXT NOT NULL DEFAULT 'unclassified',
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
                round_trip_net_pnl DOUBLE PRECISION,
                round_trip_fees DOUBLE PRECISION,
                cost_provenance TEXT NOT NULL DEFAULT 'legacy_unknown',
                time_to_mfe_sec INTEGER,
                time_to_mae_sec INTEGER,
                mfe_observed_at TIMESTAMPTZ,
                mae_observed_at TIMESTAMPTZ,
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
        columns = _relation_columns(conn, "paper_regime_trade_metrics")
        if "entry_pattern" not in columns:
            conn.execute("ALTER TABLE paper_regime_trade_metrics ADD COLUMN entry_pattern TEXT NOT NULL DEFAULT 'unclassified'")
        if "round_trip_net_pnl" not in columns:
            conn.execute("ALTER TABLE paper_regime_trade_metrics ADD COLUMN round_trip_net_pnl DOUBLE PRECISION")
        if "round_trip_fees" not in columns:
            conn.execute("ALTER TABLE paper_regime_trade_metrics ADD COLUMN round_trip_fees DOUBLE PRECISION")
        if "cost_provenance" not in columns:
            conn.execute(
                "ALTER TABLE paper_regime_trade_metrics ADD COLUMN cost_provenance TEXT NOT NULL DEFAULT 'legacy_unknown'"
            )
        if "time_to_mfe_sec" not in columns:
            conn.execute("ALTER TABLE paper_regime_trade_metrics ADD COLUMN time_to_mfe_sec INTEGER")
        if "time_to_mae_sec" not in columns:
            conn.execute("ALTER TABLE paper_regime_trade_metrics ADD COLUMN time_to_mae_sec INTEGER")
        if "mfe_observed_at" not in columns:
            conn.execute("ALTER TABLE paper_regime_trade_metrics ADD COLUMN mfe_observed_at TIMESTAMPTZ")
        if "mae_observed_at" not in columns:
            conn.execute("ALTER TABLE paper_regime_trade_metrics ADD COLUMN mae_observed_at TIMESTAMPTZ")


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


def _cost_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _round_trip_accounting(
    conn: Any,
    trade: dict[str, Any],
    *,
    allocations: list[dict[str, Any]] | None = None,
) -> tuple[float | None, float | None, str]:
    """Use durable original-lot and fill IDs; leave unlinked history unverified."""
    from paper_fill_provenance import load_fill_lot_allocations, attach_exact_fill_lot_evidence

    unknown = (None, None, "legacy_unverified_fill_lot")
    trade_id = trade.get("trade_id")
    if not isinstance(trade_id, str) or not trade_id.strip() or trade.get("side") != "SELL":
        return unknown
    if allocations is None:
        allocations = load_fill_lot_allocations(
            conn, trade_ids=[trade_id], market=_normalize_market(trade.get("market")),
        )
    linked_rows = attach_exact_fill_lot_evidence([trade], allocations)
    if len(linked_rows) != 1:
        return unknown
    linked = linked_rows[0]
    identities = (
        "_canonical_fill_id", "_canonical_lot_id", "_canonical_entry_trade_id",
        "_canonical_entry_fill_id",
    )
    if (linked.get("_fill_accounting_provenance") != "exact_fill_lot_v1"
            or not all(isinstance(linked.get(key), str) and linked[key].strip() for key in identities)):
        return unknown

    quantity = _cost_number(linked.get("quantity"))
    opened_qty = _cost_number(linked.get("_canonical_entry_quantity"))
    entry_fee = _cost_number(linked.get("_canonical_entry_fill_fee"))
    exit_fee = _cost_number(linked.get("_canonical_fill_fee"))
    ledger_fee = _cost_number(linked.get("fees"))
    gross = _cost_number(linked.get("gross_pnl"))
    if (any(value is None for value in (quantity, opened_qty, entry_fee, exit_fee, ledger_fee, gross))
            or quantity <= 0 or opened_qty <= 0 or quantity > opened_qty * (1.0 + 1e-9)
            or entry_fee < 0 or exit_fee < 0 or ledger_fee < 0):
        return unknown
    total_fee = entry_fee * (quantity / opened_qty) + exit_fee
    net = gross - total_fee
    if not math.isfinite(total_fee) or not math.isfinite(net):
        return unknown
    return net, total_fee, "exact_lot"


def observe_paper_quote(provider: Any, quote: dict[str, Any]) -> bool:
    """Capture an actual authenticated quote now; never authorize execution.

    The sampler may write this historical observation later, at its ORIGINAL
    event time. It must never claim that a cached observation is a current mark.
    """
    if not active():
        return False
    from coinbase_paper_pricing import PaperPricing
    if not isinstance(provider, PaperPricing) or not isinstance(quote, dict):
        return False
    symbol = quote.get("symbol")
    if (not symbol or quote.get("requested_symbol") != symbol or quote.get("provider_symbol") != symbol
            or quote.get("provider") != provider.source
            or quote.get("execution_authority") != "PAPER_SIMULATION_ONLY"
            or not provider.authentic(quote, symbol)):
        return False
    observed = datetime.fromisoformat(quote["quote_timestamp"].replace("Z", "+00:00"))
    captured = datetime.now(timezone.utc)
    price = _cost_number(quote.get("price"))
    sequence = quote.get("sequence")
    kraken = quote.get("provider") == "Kraken WebSocket v2 paper pricing"
    if (observed.tzinfo is None or observed > captured or (captured-observed).total_seconds() > 5 or price is None or price <= 0
            or (not kraken and (type(sequence) is not int or sequence < 0))):
        return False
    source = {key: quote[key] for key in ("provider", "quote_id", "source_identity",
                "quote_timestamp", "received_at", "timestamp_basis", "bid", "ask")}
    if kraken:
        source.update(session_id=quote["session_id"], checksum=quote["checksum"], checksum_status=quote["checksum_status"])
        event_identity = [quote["session_id"], quote["checksum"]]
    else:
        source["sequence"] = sequence
        event_identity = sequence
    source.update(execution_authority="NONE", role="OBSERVED_PAPER_QUOTE_ONLY",
                  captured_at=captured.isoformat())
    event_key = hashlib.sha256(json.dumps([quote["provider"], symbol, observed.isoformat(), event_identity]).encode()).hexdigest()
    source_prefix = "verified_quote_v1:" + event_key + "|"
    observation = {"observed_at": observed, "price": price, "sequence": sequence,
                   "source_prefix": source_prefix,
                   "source": source_prefix + json.dumps(source, sort_keys=True, allow_nan=False)}
    key = ("crypto", symbol)
    with _QUOTE_LOCK:
        old = _QUOTE_OBSERVATIONS.get(key)
        if old and (observed < old["observed_at"] or
                    (observed == old["observed_at"] and (
                        observation["source_prefix"] == old["source_prefix"] or
                        (not kraken and old["sequence"] is not None and sequence <= old["sequence"])))):
            return False
        if key not in _QUOTE_OBSERVATIONS and len(_QUOTE_OBSERVATIONS) >= _MAX_QUOTE_OBSERVATIONS:
            _QUOTE_OBSERVATIONS.pop(next(iter(_QUOTE_OBSERVATIONS)))
        _QUOTE_OBSERVATIONS[key] = observation
    return True


def _cached_observation(market: str, symbol: str) -> dict[str, Any] | None:
    # Cash marks have no equivalent original quote receipt capture yet. Keep
    # them unavailable for certified excursions rather than inventing a source.
    with _QUOTE_LOCK:
        observation = _QUOTE_OBSERVATIONS.get((market, symbol))
        return dict(observation) if observation else None


def sample_open_positions(market: str = "crypto") -> int:
    """Persist original validated quote observations, never retimestamp marks."""
    if not active():
        return 0
    from database import connect
    normalized_market = _normalize_market(market)
    inserted = unavailable = 0
    with connect() as conn:
        positions = list(conn.execute(
            "SELECT symbol FROM positions WHERE market=%s AND COALESCE(quantity,0) > 0",
            (normalized_market,),
        ).fetchall())
        for row in positions:
            symbol = str(row.get("symbol") or "").upper()
            observation = _cached_observation(normalized_market, symbol)
            if not observation:
                unavailable += 1
                continue
            # Concurrent samplers cannot insert the same original observation twice.
            owned = conn.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS owned",
                                 ("regime_quote_sample:"+normalized_market+":"+symbol,)).fetchone()
            if not owned or owned.get("owned") is not True:
                continue
            recorded = conn.execute("""
                INSERT INTO paper_regime_price_samples(market,symbol,observed_at,price,source,schema_version)
                SELECT %s,%s,%s,%s,%s,%s
                WHERE NOT EXISTS (SELECT 1 FROM paper_regime_price_samples
                    WHERE market=%s AND symbol=%s AND observed_at=%s AND left(source,%s)=%s)
                RETURNING id
                """, (normalized_market, symbol, observation["observed_at"], observation["price"],
                      observation["source"], _SCHEMA_VERSION, normalized_market, symbol,
                      observation["observed_at"], len(observation["source_prefix"]), observation["source_prefix"])).fetchone()
            inserted += recorded is not None
    log.info("PAPER REGIME QUOTE SAMPLING | market=%s | sampled=%s | unavailable=%s | additional_requests=0 | execution_impact=NONE", normalized_market, inserted, unavailable)
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
                SELECT trade_id, market, symbol, strategy, side, quantity, gross_pnl, net_pnl, fees,
                       broker_mode,account_environment,
                       entry_signal_id,entry_decision_id,entry_forecast_id,entry_quote_id,
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
        from paper_fill_provenance import load_fill_lot_allocations

        allocations = load_fill_lot_allocations(
            conn, trade_ids=[str(trade.get("trade_id") or "") for trade in trades],
            market=normalized_market,
        ) if trades else []
        allocations_by_trade: dict[str, list[dict[str, Any]]] = {}
        for allocation in allocations:
            allocations_by_trade.setdefault(str(allocation.get("trade_id") or ""), []).append(allocation)
        for trade in trades:
            trade_id = str(trade.get("trade_id") or "").strip()
            if not trade_id:
                continue
            existing = conn.execute(
                "SELECT trade_id,cost_provenance,time_to_mfe_sec,time_to_mae_sec,mfe_observed_at,mae_observed_at FROM paper_regime_trade_metrics WHERE trade_id=%s LIMIT 1",
                (trade_id,),
            ).fetchone() or {}

            symbol = str(trade.get("symbol") or "").upper()
            entry_time = trade.get("entry_time")
            exit_time = trade.get("exit_time")
            entry_price = _num(trade.get("entry_price"))
            exit_price = _num(trade.get("exit_price"))
            feature_snapshot = _json_obj(trade.get("feature_snapshot"))
            regime = classify_regime(
                feature_snapshot=feature_snapshot,
                memory_regime=_memory_regime(conn, normalized_market, symbol, entry_time),
            )
            entry_pattern = str(
                feature_snapshot.get("entry_pattern")
                or feature_snapshot.get("schwager_pattern_tag")
                or "unclassified"
            ).strip().lower()[:80] or "unclassified"

            sampled: list[dict[str, Any]] = []
            if entry_time and exit_time:
                sampled = list(conn.execute(
                    """
                    SELECT observed_at,price FROM paper_regime_price_samples
                    WHERE market=%s AND symbol=%s AND observed_at BETWEEN %s AND %s
                      AND source LIKE 'verified_quote_v1:%%'
                    ORDER BY observed_at ASC
                    """,
                    (normalized_market, symbol, entry_time, exit_time),
                ).fetchall())

            # Entry is the factual 0% excursion anchor; forward samples supply the path.
            valid_samples = [item for item in sampled if item.get("observed_at") is not None and _num(item.get("price")) > 0]
            excursion_count = len(valid_samples)
            mfe_pct, mae_pct, mfe_observed_at, mae_observed_at, time_to_mfe_sec, time_to_mae_sec = _excursion_temporal_metrics(
                entry_price, entry_time, valid_samples
            )
            round_trip_net,round_trip_fees,cost_provenance=_round_trip_accounting(
                conn, dict(trade), allocations=allocations_by_trade.get(trade_id, []),
            )
            if existing:
                updates: list[str] = []
                params: list[Any] = []
                if cost_provenance == "exact_lot" and str(existing.get("cost_provenance") or "") != "exact_lot":
                    updates.extend(["round_trip_net_pnl=%s", "round_trip_fees=%s", "cost_provenance=%s"])
                    params.extend([round_trip_net, round_trip_fees, cost_provenance])
                if excursion_count > 0 and existing.get("mfe_observed_at") is None and existing.get("mae_observed_at") is None:
                    updates.extend(["time_to_mfe_sec=%s", "time_to_mae_sec=%s", "mfe_observed_at=%s", "mae_observed_at=%s"])
                    params.extend([time_to_mfe_sec, time_to_mae_sec, mfe_observed_at, mae_observed_at])
                if updates:
                    updates.append("schema_version=%s")
                    params.extend([_SCHEMA_VERSION, trade_id])
                    conn.execute(
                        "UPDATE paper_regime_trade_metrics SET " + ",".join(updates) + " WHERE trade_id=%s",
                        tuple(params),
                    )
                    created += 1
                continue

            conn.execute(
                """
                INSERT INTO paper_regime_trade_metrics(
                    trade_id,market,symbol,strategy,regime,entry_pattern,entry_time,exit_time,
                    entry_price,exit_price,net_pnl,fees,mfe_pct,mae_pct,
                    excursion_sample_count,schema_version,round_trip_net_pnl,round_trip_fees,cost_provenance,
                    time_to_mfe_sec,time_to_mae_sec,mfe_observed_at,mae_observed_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (trade_id) DO NOTHING
                """,
                (
                    trade_id, normalized_market, symbol, normalize_strategy_identity(trade.get("strategy")), regime, entry_pattern,
                    entry_time, exit_time, entry_price or None, exit_price or None,
                    _num(trade.get("net_pnl")), max(0.0, _num(trade.get("fees"))),
                    mfe_pct, mae_pct, excursion_count, (_ENTRY_READY_VERSION if cost_provenance == "exact_lot" and excursion_count > 0 else _SCHEMA_VERSION),
                    round_trip_net,round_trip_fees,cost_provenance,
                    time_to_mfe_sec,time_to_mae_sec,mfe_observed_at,mae_observed_at,
                ),
            )
            created += 1
    return created


def _display_metric(value: Any, precision: int = 4) -> str:
    finite = _cost_number(value)
    return "NA" if finite is None else f"{finite:.{precision}f}"


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
                    WITH evidence AS (
                        SELECT *, COALESCE(cost_provenance='exact_lot'
                            AND round_trip_net_pnl > '-Infinity'::double precision
                            AND round_trip_net_pnl < 'Infinity'::double precision
                            AND round_trip_fees >= 0 AND round_trip_fees < 'Infinity'::double precision,
                            FALSE) AS exact_accounting
                        FROM paper_regime_trade_metrics
                        WHERE (%s::text IS NULL OR market=%s::text)
                    )
                    SELECT market, strategy, regime, entry_pattern, COUNT(*) AS samples,
                           SUM(COALESCE(round_trip_net_pnl,net_pnl)) AS net_pnl,
                           SUM(COALESCE(round_trip_fees,fees)) AS fees,
                           AVG(COALESCE(round_trip_net_pnl,net_pnl)) AS expectancy,
                           COUNT(*) FILTER (WHERE exact_accounting) AS exact_accounting_samples,
                           COUNT(*) FILTER (WHERE NOT exact_accounting) AS incomplete_accounting_samples,
                           SUM(round_trip_net_pnl) FILTER (WHERE exact_accounting) AS exact_accounting_net_pnl,
                           SUM(round_trip_fees) FILTER (WHERE exact_accounting) AS exact_accounting_fees,
                           AVG(mfe_pct) FILTER (WHERE excursion_sample_count > 0) AS avg_mfe_pct,
                           AVG(mae_pct) FILTER (WHERE excursion_sample_count > 0) AS avg_mae_pct,
                           SUM(CASE WHEN excursion_sample_count > 0 THEN 1 ELSE 0 END) AS excursion_trades
                    FROM evidence
                    GROUP BY market, strategy, regime, entry_pattern
                    ORDER BY samples DESC
                    LIMIT 20
                    """,
                    (normalized_market, normalized_market),
                ).fetchall()
            )
        for row in rows:
            log.info(
                "PAPER REGIME ECONOMICS | market=%s | strategy=%s | regime=%s | entry_pattern=%s | samples=%s | net_pnl=%s | fees=%s | expectancy=%s | avg_mfe_pct=%s | avg_mae_pct=%s | excursion_trades=%s | exact_accounting_samples=%s | incomplete_accounting_samples=%s | exact_accounting_net_pnl=%s | exact_accounting_fees=%s | evaluation_scope=RETROSPECTIVE_CLOSED_LEDGER_ROWS | independent_episodes=UNAVAILABLE | strategy_qualification=NOT_ESTABLISHED | excursion_verification=NOT_CERTIFIED_BY_SUMMARY | mode=shadow | execution_impact=NONE | live_trading=DISARMED",
                row.get("market"), row.get("strategy"), row.get("regime"), row.get("entry_pattern"), row.get("samples"),
                _display_metric(row.get("net_pnl")), _display_metric(row.get("fees")), _display_metric(row.get("expectancy"), 6),
                "NA" if row.get("avg_mfe_pct") is None else f"{_num(row.get('avg_mfe_pct')):.4f}",
                "NA" if row.get("avg_mae_pct") is None else f"{_num(row.get('avg_mae_pct')):.4f}",
                row.get("excursion_trades"), row.get("exact_accounting_samples"),
                row.get("incomplete_accounting_samples"), _display_metric(row.get("exact_accounting_net_pnl")),
                _display_metric(row.get("exact_accounting_fees")),
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
