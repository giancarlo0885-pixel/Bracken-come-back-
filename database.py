from __future__ import annotations

import json
import os
import random
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

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
    DATABASE_RETENTION_BATCH_SIZE,
    DATABASE_VOLUME_CAPACITY_GB,
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


TRANSIENT_DATABASE_ERROR_TEXT = (
    "database system is in recovery mode",
    "database system is not yet accepting connections",
    "connection reset",
    "server closed the connection unexpectedly",
    "unexpected eof",
    "connection refused",
    "could not connect",
    "temporary failure in name resolution",
    "name or service not known",
    "no route to host",
    "timeout expired",
    "operation timed out",
)
DATABASE_BOOTSTRAP_LOCK_NAME = "garibaldi_database_bootstrap_v37"
DATABASE_MAINTENANCE_LOCK_NAME = "garibaldi_database_maintenance_v37"
CANONICAL_PROTECTED_TABLES = {
    "portfolios",
    "positions",
    "trades",
    "execution_claims",
    "schema_migrations",
    "model_registry",
    "model_registry_events",
    "paper_data_audit",
}
DATABASE_RETENTION_POLICIES = {
    "signals": {"keep_rows": 6000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "forecasts": {"keep_rows": 3000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "equity_snapshots": {"keep_rows": 15000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "alerts": {"keep_rows": 3000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "intelligence_events": {"keep_rows": 5000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "opportunity_rankings": {"keep_rows": 12000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "oracle_decision_audit": {"keep_rows": 12000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "opportunity_radar_assessments": {"keep_rows": 12000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
}
DATABASE_TABLE_GROWTH_AUDIT = {
    "portfolios": {"class": "canonical financial records", "inserted_by": "initialize_database/portfolio bootstrap", "frequency": "one row per market", "retention": "never auto-delete"},
    "positions": {"class": "canonical financial records", "inserted_by": "oracle_bot buy execution", "frequency": "one row per open position", "retention": "never auto-delete"},
    "trades": {"class": "canonical financial records", "inserted_by": "oracle_bot buy/sell execution", "frequency": "one row per completed paper trade", "retention": "never auto-delete"},
    "execution_claims": {"class": "governance/audit records", "inserted_by": "oracle_bot execution idempotency", "frequency": "one per execution attempt", "retention": "never auto-delete"},
    "signals": {"class": "append-only analytical/ephemeral records", "inserted_by": "market_worker save_json_signal", "frequency": "scan candidates", "retention": "keep newest 6000 rows"},
    "forecasts": {"class": "append-only analytical/ephemeral records", "inserted_by": "market_worker save_forecast", "frequency": "scan candidates", "retention": "keep newest 3000 rows"},
    "equity_snapshots": {"class": "append-only analytical/ephemeral records", "inserted_by": "oracle_bot snapshot", "frequency": "worker pulse/scan", "retention": "keep newest 15000 rows"},
    "alerts": {"class": "append-only analytical/ephemeral records", "inserted_by": "database save_alert", "frequency": "notable system/market events", "retention": "keep newest 3000 rows"},
    "intelligence_events": {"class": "append-only analytical/ephemeral records", "inserted_by": "market intelligence collection", "frequency": "stock intelligence refresh", "retention": "keep newest 5000 rows"},
    "opportunity_rankings": {"class": "append-only analytical/ephemeral records", "inserted_by": "market_worker rank persistence", "frequency": "scan candidates", "retention": "keep newest 12000 rows"},
    "oracle_decision_audit": {"class": "append-only analytical/ephemeral records", "inserted_by": "market_worker decision persistence", "frequency": "ranked scan candidates", "retention": "keep newest 12000 rows"},
    "opportunity_radar_assessments": {"class": "append-only analytical/ephemeral records", "inserted_by": "market_worker radar persistence", "frequency": "ranked scan candidates", "retention": "keep newest 12000 rows"},
    "forecast_validation": {"class": "governance/audit records", "inserted_by": "forecast quality validation", "frequency": "realized forecast outcomes", "retention": "preserve until archive strategy exists"},
    "recommendations": {"class": "append-only analytical/ephemeral records", "inserted_by": "advisor recommendations", "frequency": "advisor generation", "retention": "recommended conservative row/age policy after usage review"},
    "recommendation_evidence": {"class": "append-only analytical/ephemeral records", "inserted_by": "advisor evidence persistence", "frequency": "per recommendation", "retention": "recommended conservative row/age policy after usage review"},
    "strategy_signals": {"class": "append-only analytical/ephemeral records", "inserted_by": "strategy engine", "frequency": "strategy evaluation", "retention": "recommended conservative row/age policy after usage review"},
    "forecast_results": {"class": "append-only analytical/ephemeral records", "inserted_by": "forecasting registry", "frequency": "forecast generation", "retention": "recommended conservative row/age policy after usage review"},
    "model_performance": {"class": "governance/audit records", "inserted_by": "model performance tracking", "frequency": "validation rollups", "retention": "preserve until archive strategy exists"},
    "quote_verifications": {"class": "append-only analytical/ephemeral records", "inserted_by": "provider quote verification", "frequency": "quote validation", "retention": "recommended conservative row/age policy after usage review"},
    "order_events": {"class": "governance/audit records", "inserted_by": "order proposal lifecycle", "frequency": "operator/order events", "retention": "preserve until archive strategy exists"},
    "shadow_orders": {"class": "append-only analytical/ephemeral records", "inserted_by": "shadow trading", "frequency": "shadow proposals", "retention": "recommended conservative row/age policy after usage review"},
    "shadow_fills": {"class": "append-only analytical/ephemeral records", "inserted_by": "shadow trading", "frequency": "shadow fills", "retention": "recommended conservative row/age policy after usage review"},
    "strategy_performance": {"class": "append-only analytical/ephemeral records", "inserted_by": "strategy scoring", "frequency": "performance rollups", "retention": "recommended conservative row/age policy after usage review"},
    "trade_audits": {"class": "governance/audit records", "inserted_by": "trade audit", "frequency": "audit events", "retention": "preserve until archive strategy exists"},
    "position_audits": {"class": "governance/audit records", "inserted_by": "position audit", "frequency": "audit events", "retention": "preserve until archive strategy exists"},
    "risk_events": {"class": "governance/audit records", "inserted_by": "risk engine", "frequency": "risk checks/events", "retention": "preserve until archive strategy exists"},
}


def _safe_error_message(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    if DATABASE_URL:
        text = text.replace(DATABASE_URL, "[DATABASE_URL_REDACTED]")
    password = os.getenv("PGPASSWORD", "")
    if password:
        text = text.replace(password, "[PASSWORD_REDACTED]")
    for marker in ("password=", "passwd=", "api_token=", "apikey=", "token="):
        lower = text.lower()
        idx = lower.find(marker)
        if idx >= 0:
            end = text.find(" ", idx)
            end = len(text) if end < 0 else end
            text = text[: idx + len(marker)] + "REDACTED" + text[end:]
    return text


def is_transient_database_error(exc: BaseException) -> bool:
    if psycopg is not None and isinstance(exc, getattr(psycopg, "OperationalError", ())):
        return True
    text = _safe_error_message(exc).lower()
    return any(fragment in text for fragment in TRANSIENT_DATABASE_ERROR_TEXT)


def database_ready(connect_timeout: int = 5) -> dict[str, Any]:
    if not DATABASE_URL:
        return {"ok": False, "transient": False, "configuration_error": True, "message": "DATABASE_URL is missing"}
    if psycopg is None:
        return {"ok": False, "transient": False, "configuration_error": True, "message": "psycopg is not installed"}
    try:
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=connect_timeout)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                cursor.fetchone()
            return {"ok": True, "transient": False, "configuration_error": False, "message": "database ready"}
        finally:
            conn.close()
    except Exception as exc:
        return {"ok": False, "transient": is_transient_database_error(exc), "configuration_error": False, "message": _safe_error_message(exc), "error_type": exc.__class__.__name__}


def database_health() -> dict[str, Any]:
    return database_ready()


def wait_for_database_ready(*, stop_event: Any | None = None, initial_delay: float = 2.0, max_delay: float = 30.0, log_callback: Callable[[str], None] | None = None, label: str = "PostgreSQL") -> dict[str, Any]:
    delay = max(0.1, initial_delay)
    while True:
        result = database_ready()
        if result.get("ok"):
            if log_callback:
                log_callback(f"{label} connection restored; worker bootstrap continuing")
            return result
        if not result.get("transient"):
            raise RuntimeError(str(result.get("message") or "Database configuration failure"))
        if stop_event is not None and stop_event.is_set():
            return {**result, "stopped": True}
        sleep_for = min(max_delay, delay) + random.uniform(0, min(1.0, delay * 0.2))
        if log_callback:
            log_callback(f"{label} waiting for PostgreSQL; retry in {sleep_for:.0f}s")
        if stop_event is not None:
            if stop_event.wait(sleep_for):
                return {**result, "stopped": True}
        else:
            time.sleep(sleep_for)
        delay = min(max_delay, delay * 2)


@contextmanager
def database_advisory_lock(lock_name: str, wait: bool = True) -> Iterator[bool]:
    with connect() as conn:
        function = "pg_advisory_lock" if wait else "pg_try_advisory_lock"
        record = conn.execute(f"SELECT {function}(hashtext(%s)) AS locked", (lock_name,)).fetchone()
        locked = True if wait else bool(record and record.get("locked"))
        try:
            yield locked
        finally:
            if locked:
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))


def bootstrap_database_with_lock(run_migrations_func: Callable[[], Any]) -> Any:
    with database_advisory_lock(DATABASE_BOOTSTRAP_LOCK_NAME, wait=True):
        initialize_database()
        return run_migrations_func()


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
        CREATE TABLE IF NOT EXISTS model_registry_events (
            id BIGSERIAL PRIMARY KEY,
            model TEXT NOT NULL,
            model_version TEXT,
            old_status TEXT,
            new_status TEXT NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS execution_claims (
            execution_key TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quote_timestamp TEXT NOT NULL,
            verified_price DOUBLE PRECISION NOT NULL,
            source_identity TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT
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
        CREATE TABLE IF NOT EXISTS provider_daily_usage (
            provider TEXT NOT NULL,
            usage_date TEXT NOT NULL,
            requests_used INTEGER NOT NULL DEFAULT 0,
            daily_budget INTEGER NOT NULL DEFAULT 0,
            last_request_at TEXT,
            last_success TEXT,
            last_error TEXT,
            PRIMARY KEY(provider, usage_date)
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
        "CREATE INDEX IF NOT EXISTS idx_model_registry_events_created ON model_registry_events (created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_execution_claims_symbol_created ON execution_claims (market, symbol, created_at DESC)",
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
) -> int | None:
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
                RETURNING id
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
            record = cursor.fetchone()
            return int(record.get("id")) if record and record.get("id") is not None else None


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

def _apply_retention_policy(conn: Any, table: str, policy: dict[str, Any]) -> int:
    if table in CANONICAL_PROTECTED_TABLES:
        raise ValueError(f"Refusing retention cleanup for protected table: {table}")
    if table not in DATABASE_RETENTION_POLICIES:
        raise ValueError(f"Invalid database cleanup table: {table}")
    keep_rows = int(policy.get("keep_rows") or 0)
    batch_size = max(1, int(policy.get("batch_size") or DATABASE_RETENTION_BATCH_SIZE))
    if keep_rows <= 0:
        return 0
    deleted_total = 0
    while True:
        deleted = conn.execute(
            f"""
            WITH doomed AS (
                SELECT id
                FROM {table}
                WHERE id NOT IN (
                    SELECT id FROM {table} ORDER BY id DESC LIMIT %s
                )
                ORDER BY id
                LIMIT %s
            )
            DELETE FROM {table}
            WHERE id IN (SELECT id FROM doomed)
            """,
            (keep_rows, batch_size),
        ).rowcount or 0
        deleted_total += deleted
        if deleted < batch_size:
            break
    return deleted_total


def _retention_table_exists(conn: Any, table: str) -> bool:
    record = conn.execute("SELECT to_regclass(%s) AS table_name", (f"public.{table}",)).fetchone()
    return bool(record and record.get("table_name"))


def trim_old_records() -> dict[str, int]:
    deleted_by_table: dict[str, int] = {}
    with connect() as conn:
        for table, policy in DATABASE_RETENTION_POLICIES.items():
            if not _retention_table_exists(conn, table):
                deleted_by_table[table] = 0
                continue
            deleted_by_table[table] = _apply_retention_policy(conn, table, policy)
    return deleted_by_table


def _human_bytes(value: int | float) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{size:.1f}TB"


def database_storage_report(limit: int = 12) -> dict[str, Any]:
    with connect() as conn:
        db = conn.execute("SELECT current_database() AS name, pg_database_size(current_database()) AS bytes").fetchone() or {}
        records = conn.execute(
            """
            SELECT c.relname AS table,
                   pg_total_relation_size(c.oid) AS total_bytes,
                   pg_relation_size(c.oid) AS table_bytes,
                   pg_indexes_size(c.oid) AS index_bytes,
                   COALESCE(s.n_live_tup, 0) AS live_rows,
                   COALESCE(s.n_dead_tup, 0) AS dead_rows,
                   s.last_autovacuum,
                   s.last_autoanalyze
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
            WHERE c.relkind = 'r' AND n.nspname = 'public'
            ORDER BY pg_total_relation_size(c.oid) DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    database_bytes = int(db.get("bytes") or 0)
    capacity_bytes = int(float(DATABASE_VOLUME_CAPACITY_GB) * 1024**3) if DATABASE_VOLUME_CAPACITY_GB else None
    used_pct = (database_bytes / capacity_bytes * 100.0) if capacity_bytes else None
    status = "unknown"
    if used_pct is not None:
        status = "critical" if used_pct >= 92 else "high" if used_pct >= 85 else "warning" if used_pct >= 75 else "ok"
    tables = []
    for record in records:
        item = dict(record)
        for key in ("total_bytes", "table_bytes", "index_bytes", "live_rows", "dead_rows"):
            item[key] = int(item.get(key) or 0)
        item["total_size"] = _human_bytes(item["total_bytes"])
        item["table_size"] = _human_bytes(item["table_bytes"])
        item["index_size"] = _human_bytes(item["index_bytes"])
        tables.append(item)
    return {
        "database": db.get("name"),
        "database_bytes": database_bytes,
        "database_size": _human_bytes(database_bytes),
        "capacity_bytes": capacity_bytes,
        "used_pct": used_pct,
        "status": status,
        "largest_tables": tables,
        "retention_policies": DATABASE_RETENTION_POLICIES,
        "table_growth_audit": DATABASE_TABLE_GROWTH_AUDIT,
    }


def run_database_maintenance() -> dict[str, Any]:
    with database_advisory_lock(DATABASE_MAINTENANCE_LOCK_NAME, wait=False) as locked:
        if not locked:
            return {"ok": True, "skipped": True, "reason": "maintenance already running"}
        deleted = trim_old_records()
        report = database_storage_report()
        return {"ok": True, "skipped": False, "deleted": deleted, "storage": report}
