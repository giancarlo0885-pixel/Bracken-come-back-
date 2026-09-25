from __future__ import annotations

import hashlib
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
    "trade_ledger",
    "position_lots",
    "executions",
    "live_order_proposals",
    "live_order_approvals",
    "execution_claims",
    "schema_migrations",
    "model_registry",
    "model_registry_events",
    "paper_data_audit",
    "oracle_brain_entries",
    "oracle_brain_sources",
    "oracle_brain_episodes",
    "oracle_brain_links",
    "oracle_brain_contradictions",
    "oracle_brain_research_queue",
    "oracle_brain_learning_state",
    "oracle_brain_observations",
    "oracle_counterfactual_outcomes",
    "oracle_calibration_buckets",
    "oracle_validation_weaknesses",
    "oracle_paper_promotion_evidence",
    "oracle_decision_replays",
}
DATABASE_RETENTION_POLICIES = {
    "signals": {"keep_rows": 6000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "forecasts": {"keep_rows": 3000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "equity_snapshots": {"keep_rows": 15000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "alerts": {"keep_rows": 3000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "intelligence_events": {"keep_rows": 5000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "canonical deduplicated research observations"},
    "opportunity_rankings": {"keep_rows": 12000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "oracle_decision_audit": {"keep_rows": 12000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "opportunity_radar_assessments": {"keep_rows": 12000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "global_decision_ledger": {"keep_rows": 12000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "decision audit; preserve execution/outcome-linked provenance"},
    "global_decision_events": {"keep_rows": 1000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "append-only analytical/ephemeral"},
    "forecast_validation": {"keep_rows": 15000, "batch_size": DATABASE_RETENTION_BATCH_SIZE, "classification": "governance/audit records; preserve until archive strategy exists"},
}
DATABASE_TABLE_GROWTH_AUDIT = {
    "portfolios": {"class": "canonical financial records", "inserted_by": "initialize_database/portfolio bootstrap", "frequency": "one row per market", "retention": "never auto-delete"},
    "positions": {"class": "canonical financial records", "inserted_by": "oracle_bot buy execution", "frequency": "one row per open position", "retention": "never auto-delete"},
    "trades": {"class": "canonical financial records", "inserted_by": "oracle_bot buy/sell execution", "frequency": "one row per completed paper trade", "retention": "never auto-delete"},
    "trade_ledger": {"class": "canonical financial attribution", "inserted_by": "profit attribution ledger", "frequency": "one per attributed trade/lot close", "retention": "never auto-delete"},
    "position_lots": {"class": "canonical financial attribution", "inserted_by": "profit attribution ledger", "frequency": "one per position entry lot", "retention": "never auto-delete"},
    "executions": {"class": "canonical execution records", "inserted_by": "broker/paper fill import", "frequency": "one per fill", "retention": "never auto-delete"},
    "live_order_proposals": {"class": "governance/audit records", "inserted_by": "brokerage readiness proposal flow", "frequency": "one per immutable live proposal", "retention": "never auto-delete"},
    "live_order_approvals": {"class": "governance/audit records", "inserted_by": "manual live approval flow", "frequency": "one per approval/rejection", "retention": "never auto-delete"},
    "execution_claims": {"class": "governance/audit records", "inserted_by": "oracle_bot execution idempotency", "frequency": "one per execution attempt", "retention": "never auto-delete"},
    "signals": {"class": "append-only analytical/ephemeral records", "inserted_by": "market_worker save_json_signal", "frequency": "scan candidates", "retention": "keep newest 6000 rows"},
    "forecasts": {"class": "append-only analytical/ephemeral records", "inserted_by": "market_worker save_forecast", "frequency": "scan candidates", "retention": "keep newest 3000 rows"},
    "equity_snapshots": {"class": "append-only analytical/ephemeral records", "inserted_by": "oracle_bot snapshot", "frequency": "worker pulse/scan", "retention": "keep newest 15000 rows"},
    "alerts": {"class": "append-only analytical/ephemeral records", "inserted_by": "database save_alert", "frequency": "notable system/market events", "retention": "keep newest 3000 rows"},
    "intelligence_events": {"class": "canonical deduplicated research observations", "inserted_by": "market intelligence bridge", "frequency": "continuous collection", "retention": "keep newest 5000 unique events"},
    "opportunity_rankings": {"class": "append-only analytical/ephemeral records", "inserted_by": "market_worker rank persistence", "frequency": "scan candidates", "retention": "keep newest 12000 rows"},
    "oracle_decision_audit": {"class": "append-only analytical/ephemeral records", "inserted_by": "market_worker decision persistence", "frequency": "ranked scan candidates", "retention": "keep newest 12000 rows"},
    "opportunity_radar_assessments": {"class": "append-only analytical/ephemeral records", "inserted_by": "market_worker radar persistence", "frequency": "ranked scan candidates", "retention": "keep newest 12000 rows"},
    "forecast_validation": {"class": "governance/audit records", "inserted_by": "forecast quality validation", "frequency": "realized forecast outcomes", "retention": "keep newest 15000 rows; preserve governance/audit provenance"},
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
    "global_asset_identities": {"class": "canonical identity records", "inserted_by": "global adaptive engine", "frequency": "one per canonical asset identity", "retention": "never auto-delete"},
    "global_decision_ledger": {"class": "governance/audit records", "inserted_by": "global adaptive engine", "frequency": "decision-level audit", "retention": "keep newest 12000 unlinked rows; never auto-delete trade/execution/outcome-linked decisions"},
    "global_forecast_outcomes": {"class": "governance/audit records", "inserted_by": "learning loop outcome evaluator", "frequency": "decision horizon outcomes", "retention": "preserve until archive strategy exists"},
    "provider_budget_ledger": {"class": "governance/provider quota records", "inserted_by": "provider budget manager", "frequency": "provider/capability/day", "retention": "preserve recent quota history until archive strategy exists"},
    "invalid_symbol_quarantine": {"class": "governance/provider safety records", "inserted_by": "provider symbol quarantine", "frequency": "provider-symbol failures", "retention": "preserve until retry policy/archive exists"},
    "global_model_governance": {"class": "governance/model records", "inserted_by": "champion/challenger governance", "frequency": "model lifecycle changes", "retention": "never auto-delete"},
    "global_decision_events": {"class": "append-only analytical/ephemeral records", "inserted_by": "global adaptive engine", "frequency": "worker decision funnel events", "retention": "keep newest 1000 rows"},
    "oracle_brain_entries": {"class": "durable research knowledge", "inserted_by": "Oracle Brain learning/engineering workflows", "frequency": "meaningful evidence revisions only", "retention": "never auto-delete"},
    "oracle_brain_sources": {"class": "durable research source memory", "inserted_by": "Oracle Brain source ingestion", "frequency": "new intelligence events", "retention": "preserve until archive strategy exists"},
    "oracle_brain_episodes": {"class": "durable exact-provenance episodic memory", "inserted_by": "Oracle Brain outcome learner", "frequency": "one per exact-provenance closed paper trade", "retention": "never auto-delete"},
    "oracle_brain_links": {"class": "durable research relationship memory", "inserted_by": "Oracle Brain relationship learner", "frequency": "distinct evidence relationships", "retention": "never auto-delete"},
    "oracle_brain_contradictions": {"class": "research governance/audit records", "inserted_by": "Oracle Brain contradiction engine", "frequency": "evidence polarity changes", "retention": "never auto-delete"},
    "oracle_brain_research_queue": {"class": "research governance records", "inserted_by": "Oracle Brain uncertainty engine", "frequency": "under-sampled/conflicting cohorts", "retention": "preserve until resolved/retired"},
    "oracle_brain_learning_state": {"class": "research cursor/state records", "inserted_by": "Oracle Brain learner", "frequency": "periodic learning sync", "retention": "never auto-delete"},
    "oracle_brain_observations": {"class": "canonical append-only research observations", "inserted_by": "Oracle observation bus", "frequency": "normalized persisted Oracle evidence", "retention": "never auto-delete"},
    "oracle_counterfactual_outcomes": {"class": "research validation evidence", "inserted_by": "Oracle validation layer", "frequency": "mature rejected decisions", "retention": "never auto-delete"},
    "oracle_calibration_buckets": {"class": "research validation rollups", "inserted_by": "Oracle validation layer", "frequency": "learning sync", "retention": "never auto-delete"},
    "oracle_validation_weaknesses": {"class": "research weakness evidence", "inserted_by": "Oracle validation layer", "frequency": "learning sync", "retention": "never auto-delete"},
    "oracle_paper_promotion_evidence": {"class": "research promotion evidence", "inserted_by": "Oracle validation layer", "frequency": "learning sync", "retention": "never auto-delete"},
    "oracle_decision_replays": {"class": "advanced research evidence", "inserted_by": "Oracle advanced learning", "frequency": "learning sync", "retention": "never auto-delete (canonical protected table); autovacuum tuned aggressively to control dead-tuple bloat under high churn"},
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

def _established_schema_history(conn: Any) -> bool:
    """Return True only when the versioned schema ledger already has history.

    Established production databases should not replay compatibility DDL on
    every process restart. Unapplied versioned migrations remain the authority
    for future schema changes.
    """
    try:
        relation = conn.execute(
            "SELECT to_regclass('public.schema_migrations') AS relation"
        ).fetchone() or {}
        if relation.get("relation") is None:
            return False
        row = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM schema_migrations LIMIT 1) AS has_history"
        ).fetchone() or {}
        return bool(row.get("has_history"))
    except Exception:
        return False


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
        CREATE TABLE IF NOT EXISTS trade_ledger (
            id BIGSERIAL PRIMARY KEY,
            trade_id TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            market TEXT NOT NULL,
            bucket TEXT NOT NULL,
            strategy TEXT,
            side TEXT NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            entry_time TEXT,
            entry_price DOUBLE PRECISION,
            exit_time TEXT,
            exit_price DOUBLE PRECISION,
            gross_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
            fees DOUBLE PRECISION NOT NULL DEFAULT 0,
            net_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
            return_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
            tier TEXT,
            confidence_score DOUBLE PRECISION,
            weighted_signal_score DOUBLE PRECISION,
            quote_provider TEXT,
            decision_id TEXT,
            order_id TEXT,
            broker_mode TEXT NOT NULL DEFAULT 'PAPER',
            account_environment TEXT NOT NULL DEFAULT 'PAPER',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS position_lots (
            id BIGSERIAL PRIMARY KEY,
            lot_id TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            market TEXT NOT NULL,
            bucket TEXT NOT NULL,
            strategy TEXT,
            opened_at TEXT NOT NULL,
            quantity_opened DOUBLE PRECISION NOT NULL,
            quantity_remaining DOUBLE PRECISION NOT NULL,
            entry_price DOUBLE PRECISION NOT NULL,
            entry_fees DOUBLE PRECISION NOT NULL DEFAULT 0,
            decision_id TEXT,
            broker_mode TEXT NOT NULL DEFAULT 'PAPER',
            account_environment TEXT NOT NULL DEFAULT 'PAPER',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS executions (
            id BIGSERIAL PRIMARY KEY,
            execution_id TEXT NOT NULL UNIQUE,
            order_id TEXT,
            symbol TEXT NOT NULL,
            market TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            fill_price DOUBLE PRECISION NOT NULL,
            fees DOUBLE PRECISION NOT NULL DEFAULT 0,
            executed_at TEXT NOT NULL,
            broker_mode TEXT NOT NULL,
            account_environment TEXT NOT NULL DEFAULT 'PAPER',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS live_order_proposals (
            proposal_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            market TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            estimated_notional DOUBLE PRECISION NOT NULL,
            reference_price DOUBLE PRECISION NOT NULL,
            stop_loss DOUBLE PRECISION,
            take_profit DOUBLE PRECISION,
            strategy TEXT,
            tier TEXT,
            confidence DOUBLE PRECISION,
            reward_risk_ratio DOUBLE PRECISION,
            reason TEXT,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            broker_mode TEXT NOT NULL,
            account_environment TEXT NOT NULL DEFAULT 'LIVE',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS live_order_approvals (
            approval_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            reference_price DOUBLE PRECISION NOT NULL,
            maximum_notional DOUBLE PRECISION NOT NULL,
            approval_timestamp TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            status TEXT NOT NULL,
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
            event_key TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            provider TEXT NOT NULL,
            symbol TEXT,
            title TEXT NOT NULL,
            details TEXT,
            event_time TEXT,
            source_url TEXT,
            verification_status TEXT NOT NULL DEFAULT 'reported',
            confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            expires_at TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            first_seen_at TEXT,
            last_seen_at TEXT,
            updated_at TEXT,
            ingest_count INTEGER NOT NULL DEFAULT 1,
            execution_impact TEXT NOT NULL DEFAULT 'NONE',
            created_at TEXT NOT NULL,
            CHECK (verification_status IN ('verified','corroborated','reported','unverified','inference')),
            CHECK (confidence >= 0.0 AND confidence <= 1.0),
            CHECK (ingest_count >= 1),
            CHECK (execution_impact = 'NONE')
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
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_decision_id TEXT",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_signal_id TEXT",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_forecast_id TEXT",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_quote_id TEXT",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS decision_correlation_id TEXT",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS model TEXT",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS model_version TEXT",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS provider TEXT",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS provider_symbol TEXT",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS quote_timestamp TEXT",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS decision_timestamp TEXT",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS feature_snapshot JSONB",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS risk_snapshot JSONB",
        "ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS portfolio_snapshot JSONB",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS entry_decision_id TEXT",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS entry_signal_id TEXT",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS entry_forecast_id TEXT",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS entry_quote_id TEXT",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS decision_correlation_id TEXT",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS model TEXT",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS model_version TEXT",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS provider TEXT",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS provider_symbol TEXT",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS quote_timestamp TEXT",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS decision_timestamp TEXT",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS feature_snapshot JSONB",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS risk_snapshot JSONB",
        "ALTER TABLE trade_ledger ADD COLUMN IF NOT EXISTS portfolio_snapshot JSONB",
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
        # High-churn rolling telemetry is continuously deleted by retention. Aggressive
        # per-table autovacuum keeps dead tuples reusable before they inflate the volume.
        "ALTER TABLE signals SET (autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_threshold = 50, autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 50)",
        "ALTER TABLE forecasts SET (autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_threshold = 50, autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 50)",
        "ALTER TABLE equity_snapshots SET (autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_threshold = 50, autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 50)",
        "ALTER TABLE opportunity_rankings SET (autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_threshold = 50, autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 50)",
        "CREATE INDEX IF NOT EXISTS idx_trade_audits_status ON trade_audits (status, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_position_audits_status ON position_audits (status, created_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS global_asset_identities (
            canonical_id TEXT PRIMARY KEY,
            asset_class TEXT NOT NULL,
            exchange TEXT NOT NULL,
            native_symbol TEXT NOT NULL,
            currency TEXT NOT NULL,
            provider_aliases JSONB DEFAULT '{}'::jsonb,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS global_decision_ledger (
            decision_id TEXT PRIMARY KEY,
            scan_id TEXT,
            signal_id TEXT,
            forecast_id TEXT,
            execution_claim_id TEXT,
            trade_id BIGINT,
            canonical_id TEXT,
            market TEXT,
            symbol TEXT,
            asset_class TEXT,
            features JSONB,
            portfolio_context JSONB,
            decision TEXT,
            rejection_reasons JSONB DEFAULT '[]'::jsonb,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS global_forecast_outcomes (
            id BIGSERIAL PRIMARY KEY,
            decision_id TEXT,
            forecast_id TEXT,
            symbol TEXT,
            horizon TEXT,
            realized_return_pct DOUBLE PRECISION,
            absolute_forecast_error DOUBLE PRECISION,
            direction_correct BOOLEAN,
            mfe_pct DOUBLE PRECISION,
            mae_pct DOUBLE PRECISION,
            drawdown_pct DOUBLE PRECISION,
            payload JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS global_model_governance (
            model_key TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'SHADOW',
            champion BOOLEAN DEFAULT FALSE,
            sample_count INTEGER DEFAULT 0,
            directional_accuracy DOUBLE PRECISION DEFAULT 0,
            mape DOUBLE PRECISION DEFAULT 100,
            calibration_error DOUBLE PRECISION DEFAULT 100,
            payload JSONB,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS provider_budget_ledger (
            provider TEXT NOT NULL,
            capability TEXT NOT NULL,
            utc_date TEXT NOT NULL,
            entitlement TEXT,
            daily_budget INTEGER DEFAULT 0,
            requests_used INTEGER DEFAULT 0,
            remaining_budget INTEGER DEFAULT 0,
            latency_ms DOUBLE PRECISION,
            last_success TEXT,
            last_failure TEXT,
            cooldown_until TEXT,
            data_mode TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(provider, capability, utc_date)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_global_decision_symbol_created ON global_decision_ledger (symbol, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_global_outcomes_decision ON global_forecast_outcomes (decision_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_global_outcomes_decision_horizon ON global_forecast_outcomes (decision_id, horizon)",
        "CREATE INDEX IF NOT EXISTS idx_global_outcomes_forecast_horizon ON global_forecast_outcomes (forecast_id, horizon)",
        "CREATE INDEX IF NOT EXISTS idx_provider_budget_capability ON provider_budget_ledger (provider, capability, utc_date)",
        """
        CREATE TABLE IF NOT EXISTS global_decision_events (
            id BIGSERIAL PRIMARY KEY,
            decision_id TEXT NOT NULL,
            market TEXT,
            symbol TEXT,
            stage TEXT NOT NULL,
            rejection_reason TEXT,
            payload JSONB,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS invalid_symbol_quarantine (
            symbol TEXT NOT NULL,
            provider TEXT NOT NULL,
            failure_type TEXT NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 1,
            last_failure TEXT NOT NULL,
            retry_after TEXT NOT NULL,
            PRIMARY KEY(symbol, provider, failure_type)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_global_decision_events_created ON global_decision_events (created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_global_decision_events_decision ON global_decision_events (decision_id, stage)",
        "CREATE INDEX IF NOT EXISTS idx_global_decision_events_market_symbol_stage ON global_decision_events (market, symbol, stage, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_invalid_symbol_retry ON invalid_symbol_quarantine (retry_after)",
        "CREATE INDEX IF NOT EXISTS idx_invalid_symbol_provider_retry ON invalid_symbol_quarantine (provider, retry_after)",
    ]

    with connect() as conn:
        established_schema = _established_schema_history(conn)
        with conn.cursor() as cursor:
            if not established_schema:
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

def _intelligence_event_key(
    *,
    category: str,
    provider: str,
    title: str,
    symbol: str | None,
    event_time: str | None,
    source_url: str | None,
) -> str:
    normalized = "|".join(
        " ".join(str(value or "").strip().lower().split())
        for value in (provider, category, symbol, title, event_time, source_url)
    )
    return "intel:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:40]


def save_intelligence_event(
    category: str,
    provider: str,
    title: str,
    details: Any,
    symbol: str | None = None,
    event_time: str | None = None,
    *,
    event_key: str | None = None,
    source_url: str | None = None,
    verification_status: str = "reported",
    confidence: float = 0.5,
    expires_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one canonical research event and collapse repeat observations.

    A stable ``event_key`` prevents periodic collectors from flooding the Brain
    with the same fact. Duplicate observations update provenance telemetry but
    do not create another learning event.
    """
    clean_category = " ".join(str(category or "uncategorized").split())[:120]
    clean_provider = " ".join(str(provider or "unknown").split())[:160]
    clean_title = " ".join(str(title or "Untitled intelligence").split())[:500]
    clean_symbol = str(symbol or "").upper().strip()[:32] or None
    clean_url = str(source_url or "").strip()[:2000] or None
    clean_status = str(verification_status or "reported").strip().lower()
    if clean_status not in {"verified", "corroborated", "reported", "unverified", "inference"}:
        clean_status = "unverified"
    try:
        clean_confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        clean_confidence = 0.0
    clean_key = str(event_key or "").strip()[:240] or _intelligence_event_key(
        category=clean_category,
        provider=clean_provider,
        title=clean_title,
        symbol=clean_symbol,
        event_time=event_time,
        source_url=clean_url,
    )
    now = utc_now()
    detail_payload = details if isinstance(details, (dict, list)) else {"summary": str(details or "")}
    metadata_payload = dict(metadata or {})
    metadata_payload.update(
        {
            "event_key": clean_key,
            "verification_status": clean_status,
            "execution_impact": "NONE",
        }
    )
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO intelligence_events (
                event_key,category,provider,symbol,title,details,event_time,source_url,
                verification_status,confidence,expires_at,metadata,first_seen_at,
                last_seen_at,updated_at,ingest_count,execution_impact,created_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,1,'NONE',%s)
            ON CONFLICT (event_key) DO UPDATE SET
                category=EXCLUDED.category,
                provider=EXCLUDED.provider,
                symbol=COALESCE(EXCLUDED.symbol,intelligence_events.symbol),
                title=EXCLUDED.title,
                details=EXCLUDED.details,
                event_time=COALESCE(EXCLUDED.event_time,intelligence_events.event_time),
                source_url=COALESCE(EXCLUDED.source_url,intelligence_events.source_url),
                verification_status=CASE
                    WHEN 'verified' IN (intelligence_events.verification_status,EXCLUDED.verification_status) THEN 'verified'
                    WHEN 'corroborated' IN (intelligence_events.verification_status,EXCLUDED.verification_status) THEN 'corroborated'
                    WHEN 'reported' IN (intelligence_events.verification_status,EXCLUDED.verification_status) THEN 'reported'
                    WHEN 'unverified' IN (intelligence_events.verification_status,EXCLUDED.verification_status) THEN 'unverified'
                    ELSE EXCLUDED.verification_status END,
                confidence=GREATEST(intelligence_events.confidence,EXCLUDED.confidence),
                expires_at=COALESCE(EXCLUDED.expires_at,intelligence_events.expires_at),
                metadata=(intelligence_events.metadata || EXCLUDED.metadata) || jsonb_build_object(
                    'verification_status',CASE
                        WHEN 'verified' IN (intelligence_events.verification_status,EXCLUDED.verification_status) THEN 'verified'
                        WHEN 'corroborated' IN (intelligence_events.verification_status,EXCLUDED.verification_status) THEN 'corroborated'
                        WHEN 'reported' IN (intelligence_events.verification_status,EXCLUDED.verification_status) THEN 'reported'
                        WHEN 'unverified' IN (intelligence_events.verification_status,EXCLUDED.verification_status) THEN 'unverified'
                        ELSE EXCLUDED.verification_status END,
                    'execution_impact','NONE'
                ),
                last_seen_at=EXCLUDED.last_seen_at,
                updated_at=EXCLUDED.updated_at,
                ingest_count=intelligence_events.ingest_count + 1,
                execution_impact='NONE'
            RETURNING id,event_key,ingest_count,verification_status,confidence,execution_impact
            """,
            (
                clean_key,
                clean_category,
                clean_provider,
                clean_symbol,
                clean_title,
                json.dumps(detail_payload, default=str),
                event_time,
                clean_url,
                clean_status,
                clean_confidence,
                expires_at,
                json.dumps(metadata_payload, default=str),
                now,
                now,
                now,
                now,
            ),
        ).fetchone()
    return dict(row or {"event_key": clean_key, "execution_impact": "NONE"})


# =========================================================
# DATABASE CLEANUP
# =========================================================

def _global_decision_ledger_eligible_sql() -> str:
    return (
        "trade_id IS NULL AND execution_claim_id IS NULL "
        "AND NOT EXISTS ("
        "SELECT 1 FROM global_forecast_outcomes gfo "
        "WHERE gfo.decision_id = global_decision_ledger.decision_id"
        ")"
    )


def _global_decision_ledger_cleanup_due(conn: Any, *, keep_rows: int, batch_size: int) -> bool:
    slack_rows = max(batch_size, max(1, keep_rows // 5))
    eligible_sql = _global_decision_ledger_eligible_sql()
    probe = conn.execute(
        "SELECT decision_id FROM global_decision_ledger "
        f"WHERE {eligible_sql} ORDER BY created_at DESC OFFSET %s LIMIT 1",
        (keep_rows + slack_rows,),
    ).fetchone()
    return bool(probe)


def _trim_global_decision_ledger(conn: Any, *, keep_rows: int, batch_size: int, max_batches: int = 5) -> int:
    if not _global_decision_ledger_cleanup_due(conn, keep_rows=keep_rows, batch_size=batch_size):
        return 0
    eligible_sql = _global_decision_ledger_eligible_sql()
    deleted_total = 0
    batches = 0
    while batches < max_batches:
        deleted = conn.execute(
            f"""
            WITH doomed AS (
                SELECT decision_id
                FROM global_decision_ledger
                WHERE {eligible_sql}
                ORDER BY created_at DESC
                OFFSET %s
                LIMIT %s
            )
            DELETE FROM global_decision_ledger
            WHERE decision_id IN (SELECT decision_id FROM doomed)
            """,
            (keep_rows, batch_size),
        ).rowcount or 0
        deleted_total += deleted
        batches += 1
        if deleted < batch_size:
            break
    return deleted_total


def _retention_cleanup_due(conn: Any, table: str, *, keep_rows: int, batch_size: int) -> bool:
    """Avoid constant INSERT/DELETE churn by trimming only after a bounded overshoot.

    The table may temporarily grow by at most max(batch_size, 20% of keep_rows)
    before a cleanup runs. This preserves the same retained history after cleanup
    while reducing dead tuples, index churn, and WAL generated by tiny trims.
    """
    slack_rows = max(batch_size, max(1, keep_rows // 5))
    probe = conn.execute(
        f"SELECT id FROM {table} ORDER BY id DESC OFFSET %s LIMIT 1",
        (keep_rows + slack_rows,),
    ).fetchone()
    return bool(probe)


def _apply_retention_policy(conn: Any, table: str, policy: dict[str, Any], *, max_batches: int = 5) -> int:
    if table in CANONICAL_PROTECTED_TABLES:
        raise ValueError(f"Refusing retention cleanup for protected table: {table}")
    if table not in DATABASE_RETENTION_POLICIES:
        raise ValueError(f"Invalid database cleanup table: {table}")
    keep_rows = int(policy.get("keep_rows") or 0)
    batch_size = max(1, int(policy.get("batch_size") or DATABASE_RETENTION_BATCH_SIZE))
    if keep_rows <= 0:
        return 0
    if table == "global_decision_ledger":
        return _trim_global_decision_ledger(conn, keep_rows=keep_rows, batch_size=batch_size, max_batches=max_batches)
    if not _retention_cleanup_due(conn, table, keep_rows=keep_rows, batch_size=batch_size):
        return 0
    deleted_total = 0
    batches = 0
    while batches < max_batches:
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
        batches += 1
        if deleted < batch_size:
            break
    return deleted_total


def _retention_table_exists(conn: Any, table: str) -> bool:
    record = conn.execute("SELECT to_regclass(%s) AS table_name", (f"public.{table}",)).fetchone()
    return bool(record and record.get("table_name"))


def trim_old_records() -> dict[str, int]:
    """Run a bounded retention pass, committing each table independently.

    Per-table commits prevent one high-churn relation from holding a single
    transaction open across the entire maintenance sweep. Each policy is also
    capped to a small number of delete batches per pass.
    """
    deleted_by_table: dict[str, int] = {}
    tables = list(DATABASE_RETENTION_POLICIES)
    if "global_decision_ledger" in tables:
        tables.remove("global_decision_ledger")
        tables.insert(0, "global_decision_ledger")
    for table in tables:
        policy = DATABASE_RETENTION_POLICIES[table]
        with connect() as conn:
            if not _retention_table_exists(conn, table):
                deleted_by_table[table] = 0
                continue
            deleted_by_table[table] = _apply_retention_policy(
                conn, table, policy, max_batches=5
            )
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
    archive_candidates = []
    for item in tables:
        audit = DATABASE_TABLE_GROWTH_AUDIT.get(item["table"], {})
        retention = str(audit.get("retention") or "")
        if "never auto-delete" in retention or "canonical financial" in str(audit.get("class") or ""):
            continue
        if "preserve" in retention:
            archive_candidates.append(
                {
                    "table": item["table"],
                    "recommendation": "roll up or archive after explicit operator review",
                    "retention": retention,
                    "live_rows": item["live_rows"],
                    "total_bytes": item["total_bytes"],
                }
            )
        elif "keep newest" in retention:
            archive_candidates.append(
                {
                    "table": item["table"],
                    "recommendation": "eligible for configured rolling retention",
                    "retention": retention,
                    "live_rows": item["live_rows"],
                    "total_bytes": item["total_bytes"],
                }
            )
    projected_30d_bytes = None
    if database_bytes:
        projected_30d_bytes = int(database_bytes * 1.10)
    return {
        "database": db.get("name"),
        "database_bytes": database_bytes,
        "database_size": _human_bytes(database_bytes),
        "capacity_bytes": capacity_bytes,
        "used_pct": used_pct,
        "status": status,
        "largest_tables": tables,
        "archive_candidates": archive_candidates,
        "capacity_projection": {
            "method": "conservative static 10 percent 30-day growth estimate until historical samples are available",
            "projected_30d_bytes": projected_30d_bytes,
            "projected_30d_size": _human_bytes(projected_30d_bytes or 0) if projected_30d_bytes else None,
        },
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
