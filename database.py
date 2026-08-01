from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

try:
    import psycopg
    from psycopg import Connection
    from psycopg.rows import dict_row
except ImportError:  # Allows analysis/test imports before deployment dependencies install.
    psycopg = None
    Connection = Any
    dict_row = None

from config import (
    CRYPTO_PAPER_LEVERAGE,
    CRYPTO_STARTING_BALANCE,
    PAPER_BROKER_PROFILE,
    STOCK_PAPER_LEVERAGE,
    STOCK_STARTING_BALANCE,
)


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


# =========================================================
# TIME AND DATABASE CONNECTION
# =========================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is missing. Link the Railway PostgreSQL "
            "DATABASE_URL variable to the web, stock-worker, and "
            "crypto-worker services."
        )

    return DATABASE_URL


@contextmanager
def connect() -> Iterator[Connection]:
    if psycopg is None:
        raise RuntimeError(
            "psycopg is not installed. Install requirements.txt before starting the app."
        )
    conn = psycopg.connect(
        _database_url(),
        row_factory=dict_row,
        connect_timeout=15,
    )

    try:
        yield conn
        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# =========================================================
# DATABASE INITIALIZATION
# =========================================================

def initialize_database() -> None:
    create_statements = [
        """
        CREATE TABLE IF NOT EXISTS portfolios (
            market TEXT PRIMARY KEY,
            cash DOUBLE PRECISION NOT NULL,
            starting_balance DOUBLE PRECISION NOT NULL,
            leverage_limit DOUBLE PRECISION NOT NULL DEFAULT 1,
            margin_debt DOUBLE PRECISION NOT NULL DEFAULT 0,
            margin_interest_accrued DOUBLE PRECISION NOT NULL DEFAULT 0,
            margin_interest_updated_at TEXT,
            broker_profile TEXT NOT NULL DEFAULT 'cash',
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS positions (
            id BIGSERIAL PRIMARY KEY,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            entry_price DOUBLE PRECISION NOT NULL,
            average_price DOUBLE PRECISION NOT NULL DEFAULT 0,
            current_price DOUBLE PRECISION NOT NULL,
            highest_price DOUBLE PRECISION NOT NULL,
            opened_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(market, symbol)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trades (
            id BIGSERIAL PRIMARY KEY,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
            score DOUBLE PRECISION,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS signals (
            id BIGSERIAL PRIMARY KEY,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            score DOUBLE PRECISION NOT NULL,
            action TEXT NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS forecasts (
            id BIGSERIAL PRIMARY KEY,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            requested_symbol TEXT,
            provider_symbol TEXT,
            source_interval TEXT,
            source_quote_timestamp TEXT,
            scan_type TEXT,
            model_version TEXT,
            expected_move_pct DOUBLE PRECISION,
            signal_id BIGINT,
            signal_created_at TEXT,
            data_quality_score DOUBLE PRECISION,
            forecast_id TEXT,
            horizon_days DOUBLE PRECISION NOT NULL,
            horizon_bars INTEGER,
            horizon_minutes DOUBLE PRECISION,
            target_price DOUBLE PRECISION NOT NULL,
            low_price DOUBLE PRECISION NOT NULL,
            high_price DOUBLE PRECISION NOT NULL,
            probability_up DOUBLE PRECISION NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id BIGSERIAL PRIMARY KEY,
            market TEXT NOT NULL,
            equity DOUBLE PRECISION NOT NULL,
            cash DOUBLE PRECISION NOT NULL,
            positions_value DOUBLE PRECISION NOT NULL,
            drawdown DOUBLE PRECISION NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id BIGSERIAL PRIMARY KEY,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            symbol TEXT,
            source TEXT,
            created_at TEXT NOT NULL,
            acknowledged INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS intelligence_events (
            id BIGSERIAL PRIMARY KEY,
            category TEXT NOT NULL,
            provider TEXT NOT NULL,
            symbol TEXT,
            title TEXT NOT NULL,
            details TEXT,
            event_time TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS worker_status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            status TEXT NOT NULL,
            message TEXT,
            last_run TEXT,
            heartbeat TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS market_worker_status (
            market TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            message TEXT,
            last_run TEXT,
            heartbeat TEXT,
            last_pulse TEXT,
            last_fast_scan TEXT,
            next_fast_scan_at TEXT,
            next_scan_at TEXT,
            session_label TEXT,
            pulse_seconds INTEGER,
            fast_scan_seconds INTEGER,
            deep_scan_seconds INTEGER,
            execution_mode TEXT DEFAULT 'paper',
            actions_last_cycle INTEGER DEFAULT 0,
            fast_actions_last_cycle INTEGER DEFAULT 0,
            cycle_errors INTEGER DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS opportunity_rankings (
            id BIGSERIAL PRIMARY KEY,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            rank INTEGER NOT NULL,
            opportunity_score DOUBLE PRECISION NOT NULL,
            payload JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_rotations (
            id BIGSERIAL PRIMARY KEY,
            market TEXT NOT NULL,
            sold_symbol TEXT,
            bought_symbol TEXT,
            score_gap DOUBLE PRECISION,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'proposed',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS provider_health (
            provider TEXT PRIMARY KEY,
            configured BOOLEAN NOT NULL,
            status TEXT NOT NULL,
            latency_ms DOUBLE PRECISION,
            message TEXT,
            checked_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id BIGSERIAL PRIMARY KEY,
            market TEXT,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            parameters JSONB,
            metrics JSONB NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
    ]

    migration_statements = [
        """
        ALTER TABLE positions
        ADD COLUMN IF NOT EXISTS average_price
        DOUBLE PRECISION NOT NULL DEFAULT 0
        """,
        """
        UPDATE positions
        SET average_price = entry_price
        WHERE average_price IS NULL
           OR average_price <= 0
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_positions_market
        ON positions (market)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_positions_market_symbol
        ON positions (market, symbol)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_trades_market_created
        ON trades (market, created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_signals_market_created
        ON signals (market, created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_forecasts_market_created
        ON forecasts (market, created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_snapshots_market_created
        ON equity_snapshots (market, created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_opportunity_market_created
        ON opportunity_rankings (market, created_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_opportunity_market_score
        ON opportunity_rankings (market, opportunity_score DESC)
        """,
        """
        ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS last_pulse TEXT
        """,
        """
        ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS next_scan_at TEXT
        """,
        """
        ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS session_label TEXT
        """,
        """
        ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS pulse_seconds INTEGER
        """,
        """
        ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS deep_scan_seconds INTEGER
        """,
        """
        ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS execution_mode TEXT DEFAULT 'paper'
        """,
        """
        ALTER TABLE market_worker_status ADD COLUMN IF NOT EXISTS actions_last_cycle INTEGER DEFAULT 0
        """,
        """
        ALTER TABLE portfolios
        ADD COLUMN IF NOT EXISTS broker_profile TEXT NOT NULL DEFAULT 'cash'
        """,
        """
        ALTER TABLE portfolios
        ADD COLUMN IF NOT EXISTS leverage_limit DOUBLE PRECISION NOT NULL DEFAULT 1
        """,
        """
        ALTER TABLE portfolios
        ADD COLUMN IF NOT EXISTS margin_debt DOUBLE PRECISION NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE portfolios
        ADD COLUMN IF NOT EXISTS margin_interest_accrued DOUBLE PRECISION NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE portfolios
        ADD COLUMN IF NOT EXISTS margin_interest_updated_at TEXT
        """,
        """
        ALTER TABLE portfolios
        ADD COLUMN IF NOT EXISTS peak_equity DOUBLE PRECISION
        """,
        """
        ALTER TABLE portfolios
        ADD COLUMN IF NOT EXISTS risk_state TEXT DEFAULT 'normal'
        """,
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS requested_symbol TEXT",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS provider_symbol TEXT",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS source_interval TEXT",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS source_quote_timestamp TEXT",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS scan_type TEXT",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS model_version TEXT",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS expected_move_pct DOUBLE PRECISION",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS signal_id BIGINT",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS signal_created_at TEXT",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS data_quality_score DOUBLE PRECISION",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS forecast_id TEXT",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS horizon_bars INTEGER",
        "ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS horizon_minutes DOUBLE PRECISION",
        "ALTER TABLE forecasts ALTER COLUMN horizon_days TYPE DOUBLE PRECISION USING horizon_days::DOUBLE PRECISION",
        """
        CREATE TABLE IF NOT EXISTS forecast_validation (
            id BIGSERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            source_interval TEXT NOT NULL,
            model TEXT NOT NULL,
            model_version TEXT,
            probability_up DOUBLE PRECISION,
            predicted_move_pct DOUBLE PRECISION,
            realized_move_pct DOUBLE PRECISION,
            direction_correct BOOLEAN,
            mape DOUBLE PRECISION,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS paper_data_audit (
            id BIGSERIAL PRIMARY KEY,
            record_type TEXT NOT NULL,
            record_id BIGINT,
            market TEXT,
            symbol TEXT,
            status TEXT NOT NULL DEFAULT 'suspected_price_corruption',
            reason TEXT,
            payload JSONB,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(record_type, record_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS advisor_profiles (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            investment_objective TEXT,
            risk_tolerance TEXT,
            investment_horizon TEXT,
            available_capital DOUBLE PRECISION,
            liquidity_needs DOUBLE PRECISION,
            maximum_acceptable_drawdown DOUBLE PRECISION,
            restricted_assets JSONB,
            preferred_asset_classes JSONB,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS recommendations (
            id BIGSERIAL PRIMARY KEY,
            recommendation_id TEXT UNIQUE NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            confidence DOUBLE PRECISION,
            opportunity_score DOUBLE PRECISION,
            expected_return DOUBLE PRECISION,
            expected_downside DOUBLE PRECISION,
            data_quality_score DOUBLE PRECISION,
            model_version TEXT,
            payload JSONB,
            expires_at TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS recommendation_evidence (
            id BIGSERIAL PRIMARY KEY,
            recommendation_id TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            summary TEXT,
            payload JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS strategy_signals (
            id BIGSERIAL PRIMARY KEY,
            market TEXT,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            score DOUBLE PRECISION,
            confidence DOUBLE PRECISION,
            available BOOLEAN,
            message TEXT,
            evidence JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS forecast_registry (
            id BIGSERIAL PRIMARY KEY,
            model TEXT NOT NULL,
            model_version TEXT,
            asset_class TEXT,
            source_interval TEXT,
            status TEXT NOT NULL DEFAULT 'experimental',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS forecast_results (
            id BIGSERIAL PRIMARY KEY,
            forecast_id TEXT UNIQUE,
            market TEXT,
            symbol TEXT NOT NULL,
            asset_class TEXT,
            source_interval TEXT,
            source_quote_timestamp TEXT,
            horizon_bars INTEGER,
            horizon_minutes DOUBLE PRECISION,
            horizon_days DOUBLE PRECISION,
            target_price DOUBLE PRECISION,
            probability_up DOUBLE PRECISION,
            validation_status TEXT,
            data_quality_score DOUBLE PRECISION,
            payload JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS model_registry (
            id BIGSERIAL PRIMARY KEY,
            model TEXT NOT NULL,
            model_version TEXT,
            status TEXT NOT NULL DEFAULT 'experimental',
            reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(model, model_version)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS model_performance (
            id BIGSERIAL PRIMARY KEY,
            model TEXT NOT NULL,
            model_version TEXT,
            symbol TEXT,
            asset_class TEXT,
            strategy TEXT,
            source_interval TEXT,
            market_regime TEXT,
            horizon_bars INTEGER,
            metrics JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS provider_capabilities (
            id BIGSERIAL PRIMARY KEY,
            provider TEXT NOT NULL,
            capability TEXT NOT NULL,
            supported BOOLEAN NOT NULL DEFAULT FALSE,
            available BOOLEAN NOT NULL DEFAULT FALSE,
            cooldown_until TEXT,
            limitation TEXT,
            checked_at TEXT NOT NULL,
            UNIQUE(provider, capability)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS quote_verifications (
            id BIGSERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            market TEXT,
            primary_provider TEXT,
            secondary_provider TEXT,
            primary_price DOUBLE PRECISION,
            secondary_price DOUBLE PRECISION,
            difference_pct DOUBLE PRECISION,
            consensus_status TEXT,
            payload JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS order_proposals (
            id BIGSERIAL PRIMARY KEY,
            idempotency_key TEXT UNIQUE NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            order_type TEXT NOT NULL,
            limit_price DOUBLE PRECISION,
            recommendation_id TEXT,
            approval_status TEXT NOT NULL DEFAULT 'proposed',
            payload JSONB,
            expires_at TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS order_events (
            id BIGSERIAL PRIMARY KEY,
            idempotency_key TEXT,
            status TEXT NOT NULL,
            reason TEXT,
            payload JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS shadow_orders (
            id BIGSERIAL PRIMARY KEY,
            idempotency_key TEXT UNIQUE NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            payload JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS shadow_fills (
            id BIGSERIAL PRIMARY KEY,
            idempotency_key TEXT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            filled_quantity DOUBLE PRECISION,
            fill_price DOUBLE PRECISION,
            status TEXT,
            payload JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS strategy_performance (
            id BIGSERIAL PRIMARY KEY,
            strategy TEXT NOT NULL,
            symbol TEXT,
            asset_class TEXT,
            market_regime TEXT,
            metrics JSONB,
            status TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trade_audits (
            id BIGSERIAL PRIMARY KEY,
            trade_id BIGINT,
            symbol TEXT,
            recorded_price DOUBLE PRECISION,
            reference_price DOUBLE PRECISION,
            difference_pct DOUBLE PRECISION,
            status TEXT NOT NULL DEFAULT 'unreviewed',
            reason TEXT,
            reviewer TEXT,
            reviewed_at TEXT,
            estimated_pnl_impact DOUBLE PRECISION,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS position_audits (
            id BIGSERIAL PRIMARY KEY,
            position_id BIGINT,
            symbol TEXT,
            recorded_price DOUBLE PRECISION,
            reference_price DOUBLE PRECISION,
            difference_pct DOUBLE PRECISION,
            status TEXT NOT NULL DEFAULT 'unreviewed',
            reason TEXT,
            reviewer TEXT,
            reviewed_at TEXT,
            estimated_pnl_impact DOUBLE PRECISION,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS risk_events (
            id BIGSERIAL PRIMARY KEY,
            market TEXT,
            symbol TEXT,
            risk_state TEXT,
            event TEXT,
            payload JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS execution_switch_history (
            id BIGSERIAL PRIMARY KEY,
            switch_name TEXT NOT NULL,
            enabled BOOLEAN NOT NULL,
            reason TEXT,
            actor TEXT,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_recommendations_market_created ON recommendations (market, created_at DESC)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_data_audit_record ON paper_data_audit (record_type, record_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_model_registry_model_version ON model_registry (model, model_version)",
        "CREATE INDEX IF NOT EXISTS idx_recommendations_symbol_created ON recommendations (symbol, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_order_proposals_status ON order_proposals (approval_status, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_strategy_signals_symbol_strategy ON strategy_signals (symbol, strategy, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_risk_events_market_created ON risk_events (market, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_trade_audits_status ON trade_audits (status, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_position_audits_status ON position_audits (status, created_at DESC)",
    ]

    with connect() as conn:
        with conn.cursor() as cursor:
            for statement in create_statements:
                cursor.execute(statement)

            for statement in migration_statements:
                cursor.execute(statement)

            for market in ("cash", "crypto"):
                starting_capital = float(
                    CRYPTO_STARTING_BALANCE if market == "crypto" else STOCK_STARTING_BALANCE
                )
                leverage_limit = float(
                    CRYPTO_PAPER_LEVERAGE if market == "crypto" else STOCK_PAPER_LEVERAGE
                )
                cursor.execute(
                    """
                    INSERT INTO portfolios (
                        market,
                        cash,
                        starting_balance,
                        leverage_limit,
                        margin_debt,
                        margin_interest_accrued,
                        margin_interest_updated_at,
                        broker_profile,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, 0, 0, %s, %s, %s)
                    ON CONFLICT (market) DO NOTHING
                    """,
                    (
                        market,
                        starting_capital,
                        starting_capital,
                        leverage_limit,
                        utc_now(),
                        PAPER_BROKER_PROFILE,
                        utc_now(),
                    ),
                )

            cursor.execute(
                """
                INSERT INTO worker_status (
                    id,
                    status,
                    message,
                    last_run,
                    heartbeat
                )
                VALUES (
                    1,
                    'waiting',
                    'Worker has not completed a scan yet.',
                    NULL,
                    %s
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (utc_now(),),
            )

            for market in ("cash", "crypto"):
                cursor.execute(
                    """
                    INSERT INTO market_worker_status (
                        market,
                        status,
                        message,
                        last_run,
                        heartbeat
                    )
                    VALUES (
                        %s,
                        'waiting',
                        'Market worker has not completed a scan yet.',
                        NULL,
                        %s
                    )
                    ON CONFLICT (market) DO NOTHING
                    """,
                    (
                        market,
                        utc_now(),
                    ),
                )


# =========================================================
# GENERAL DATABASE HELPERS
# =========================================================

def rows(
    query: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            return list(cursor.fetchall())


def row(
    query: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any] | None:
    result = rows(query, params)
    return result[0] if result else None


def execute(
    query: str,
    params: tuple[Any, ...] = (),
) -> None:
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)


# =========================================================
# WORKER STATUS
# =========================================================

def set_worker_status(
    status: str,
    message: str,
    completed: bool = False,
) -> None:
    now = utc_now()

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO worker_status (
                    id,
                    status,
                    message,
                    last_run,
                    heartbeat
                )
                VALUES (
                    1,
                    %s,
                    %s,
                    CASE WHEN %s THEN %s ELSE NULL END,
                    %s
                )
                ON CONFLICT (id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    message = EXCLUDED.message,
                    heartbeat = EXCLUDED.heartbeat,
                    last_run = CASE
                        WHEN %s THEN %s
                        ELSE worker_status.last_run
                    END
                """,
                (
                    status,
                    message,
                    completed,
                    now,
                    now,
                    completed,
                    now,
                ),
            )


def set_market_worker_status(
    market: str,
    status: str,
    message: str,
    completed: bool = False,
) -> None:
    now = utc_now()

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO market_worker_status (
                    market,
                    status,
                    message,
                    last_run,
                    heartbeat
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    CASE WHEN %s THEN %s ELSE NULL END,
                    %s
                )
                ON CONFLICT (market)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    message = EXCLUDED.message,
                    heartbeat = EXCLUDED.heartbeat,
                    last_run = CASE
                        WHEN %s THEN %s
                        ELSE market_worker_status.last_run
                    END
                """,
                (
                    market,
                    status,
                    message,
                    completed,
                    now,
                    now,
                    completed,
                    now,
                ),
            )


# =========================================================
# SIGNALS
# =========================================================

def save_json_signal(
    market: str,
    symbol: str,
    price: float,
    score: float,
    action: str,
    confidence: float,
    details: Any,
    created_at: str | None = None,
) -> None:
    created_at = created_at or utc_now()
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO signals (
                    market,
                    symbol,
                    price,
                    score,
                    action,
                    confidence,
                    details,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    market,
                    symbol,
                    float(price),
                    float(score),
                    action,
                    float(confidence),
                    json.dumps(details, default=str),
                    created_at,
                ),
            )


# =========================================================
# FORECASTS
# =========================================================

def save_forecast(
    market: str,
    symbol: str,
    forecast: Any,
    *,
    scan_type: str | None = None,
    signal_id: int | None = None,
    signal_created_at: str | None = None,
) -> None:
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO forecasts (
                    market,
                    symbol,
                    requested_symbol,
                    provider_symbol,
                    source_interval,
                    source_quote_timestamp,
                    scan_type,
                    model_version,
                    expected_move_pct,
                    signal_id,
                    signal_created_at,
                    data_quality_score,
                    forecast_id,
                    horizon_days,
                    horizon_bars,
                    horizon_minutes,
                    target_price,
                    low_price,
                    high_price,
                    probability_up,
                    model,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    market,
                    symbol,
                    str(getattr(forecast, "requested_symbol", "") or symbol),
                    str(getattr(forecast, "provider_symbol", "") or symbol),
                    str(getattr(forecast, "source_interval", "") or "1d"),
                    str(getattr(forecast, "source_quote_timestamp", "") or ""),
                    scan_type,
                    str(getattr(forecast, "model_version", "") or ""),
                    float(getattr(forecast, "expected_move_pct", 0.0) or 0.0),
                    signal_id,
                    signal_created_at,
                    float(getattr(forecast, "data_quality_score", 0.0) or 0.0),
                    str(getattr(forecast, "forecast_id", "") or ""),
                    float(getattr(forecast, "horizon_days", 0.0) or 0.0),
                    int(getattr(forecast, "horizon_bars", 0) or 0),
                    float(getattr(forecast, "horizon_minutes", 0.0) or 0.0),
                    float(forecast.target_price),
                    float(forecast.low_price),
                    float(forecast.high_price),
                    float(forecast.probability_up),
                    str(forecast.model),
                    str(getattr(forecast, "generated_at", "") or utc_now()),
                ),
            )


# =========================================================
# ALERTS
# =========================================================

def add_alert(
    category: str,
    severity: str,
    title: str,
    message: str,
    symbol: str | None = None,
    source: str | None = None,
) -> None:
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO alerts (
                    category,
                    severity,
                    title,
                    message,
                    symbol,
                    source,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    category,
                    severity,
                    title,
                    message,
                    symbol,
                    source,
                    utc_now(),
                ),
            )


# =========================================================
# INTELLIGENCE EVENTS
# =========================================================

def save_intelligence_event(
    category: str,
    provider: str,
    title: str,
    details: Any,
    symbol: str | None = None,
    event_time: str | None = None,
) -> None:
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO intelligence_events (
                    category,
                    provider,
                    symbol,
                    title,
                    details,
                    event_time,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    category,
                    provider,
                    symbol,
                    title,
                    json.dumps(details, default=str),
                    event_time,
                    utc_now(),
                ),
            )


# =========================================================
# DATABASE CLEANUP
# =========================================================

def trim_old_records() -> None:
    limits = [
        ("signals", 6000),
        ("forecasts", 3000),
        ("equity_snapshots", 15000),
        ("alerts", 3000),
        ("intelligence_events", 5000),
    ]

    allowed_tables = {
        "signals",
        "forecasts",
        "equity_snapshots",
        "alerts",
        "intelligence_events",
    }

    with connect() as conn:
        with conn.cursor() as cursor:
            for table, limit in limits:
                if table not in allowed_tables:
                    raise ValueError(
                        f"Invalid database cleanup table: {table}"
                    )

                cursor.execute(
                    f"""
                    DELETE FROM {table}
                    WHERE id NOT IN (
                        SELECT id
                        FROM {table}
                        ORDER BY id DESC
                        LIMIT %s
                    )
                    """,
                    (limit,),
                )
