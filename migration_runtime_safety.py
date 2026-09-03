from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


log = logging.getLogger("migration-runtime-safety")
_INSTALLED = False
_ORIGINAL = None


def _sync_runtime_data(conn: Any) -> None:
    """Apply non-DDL runtime settings without taking heavyweight schema locks."""
    import migrations

    now = migrations.utc_now()
    if migrations.PAPER_CAPITAL_UPGRADE:
        for market, target, leverage in (
            ("cash", float(migrations.STOCK_STARTING_BALANCE), float(migrations.STOCK_PAPER_LEVERAGE)),
            ("crypto", float(migrations.CRYPTO_STARTING_BALANCE), float(migrations.CRYPTO_PAPER_LEVERAGE)),
        ):
            conn.execute(
                """
                UPDATE portfolios
                SET cash = cash + (%s - starting_balance),
                    starting_balance = %s,
                    peak_equity = GREATEST(COALESCE(peak_equity, 0), %s),
                    leverage_limit = %s,
                    broker_profile = %s,
                    margin_interest_updated_at = COALESCE(margin_interest_updated_at, %s),
                    updated_at = %s
                WHERE market = %s
                  AND starting_balance < %s
                """,
                (
                    target,
                    target,
                    target,
                    leverage,
                    migrations.PAPER_BROKER_PROFILE,
                    now,
                    now,
                    market,
                    target,
                ),
            )

    conn.execute(
        """
        UPDATE portfolios
        SET leverage_limit = CASE WHEN market='crypto' THEN %s ELSE %s END,
            broker_profile = %s,
            margin_interest_updated_at = COALESCE(margin_interest_updated_at, %s)
        WHERE market IN ('cash','crypto')
        """,
        (
            float(migrations.CRYPTO_PAPER_LEVERAGE),
            float(migrations.STOCK_PAPER_LEVERAGE),
            migrations.PAPER_BROKER_PROFILE,
            now,
        ),
    )

    conn.execute(
        """
        INSERT INTO market_worker_status(market,status,message,last_run,heartbeat)
        SELECT 'cash', status, message, last_run, heartbeat
        FROM market_worker_status
        WHERE market='stock'
        ON CONFLICT (market) DO UPDATE SET
            status = CASE
                WHEN COALESCE(EXCLUDED.heartbeat, '') > COALESCE(market_worker_status.heartbeat, '')
                THEN EXCLUDED.status ELSE market_worker_status.status END,
            message = CASE
                WHEN COALESCE(EXCLUDED.heartbeat, '') > COALESCE(market_worker_status.heartbeat, '')
                THEN EXCLUDED.message ELSE market_worker_status.message END,
            last_run = GREATEST(EXCLUDED.last_run, market_worker_status.last_run),
            heartbeat = GREATEST(EXCLUDED.heartbeat, market_worker_status.heartbeat)
        """
    )
    conn.execute("DELETE FROM market_worker_status WHERE market='stock'")


def safe_run_migrations() -> list[str]:
    """Run schema DDL only when truly needed; keep normal restarts DML-only.

    The legacy runner executed every ``ALTER TABLE ... IF NOT EXISTS`` repair on
    every web/worker startup. PostgreSQL still needs heavyweight table locks for
    those ALTER statements, which deadlocked against active position/portfolio
    transactions. Existing databases already have a durable ``schema_migrations``
    ledger, so repeated compatibility DDL is unnecessary.

    Fresh/legacy databases with no applied migration history still receive the
    complete compatibility repair. Established databases receive only runtime
    data-setting synchronization plus genuinely unapplied SQL migration files,
    all serialized by the existing transaction advisory lock.
    """
    import migrations

    folder = Path(migrations.__file__).with_name("migrations")
    applied: list[str] = []
    with migrations.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (migrations.MIGRATION_LOCK_ID,))
        # This table is independent of the hot portfolio/position relations and
        # is needed to determine whether heavyweight compatibility repair is new.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"""
        )
        existing = {
            record["version"]
            for record in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        if not existing:
            log.warning("MIGRATION SAFETY | schema_history=EMPTY | compatibility_repair=RUN_ONCE")
            migrations._repair_database(conn)
        else:
            _sync_runtime_data(conn)
            log.info(
                "MIGRATION SAFETY | schema_history=%d | repeated_repair_ddl=SKIPPED | runtime_sync=PASS",
                len(existing),
            )

        if folder.exists():
            for path in sorted(folder.glob("*.sql")):
                if path.name in existing:
                    continue
                sql = path.read_text(encoding="utf-8").strip()
                if sql:
                    conn.execute(sql)
                conn.execute(
                    """INSERT INTO schema_migrations(version,applied_at)
                       VALUES (%s,%s) ON CONFLICT(version) DO NOTHING""",
                    (path.name, migrations.utc_now()),
                )
                applied.append(path.name)

    return applied


def install_migration_runtime_safety() -> None:
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return
    import migrations

    _ORIGINAL = migrations.run_migrations
    migrations.run_migrations = safe_run_migrations
    # capital_readiness_runtime imports the function by value, so update that
    # binding too when the module is already loaded by a worker entrypoint.
    try:
        import capital_readiness_runtime

        capital_readiness_runtime.run_migrations = safe_run_migrations
    except Exception:
        pass
    _INSTALLED = True
    log.info("MIGRATION SAFETY | repeated_runtime_schema_ddl=DISABLED | advisory_lock=ON")
