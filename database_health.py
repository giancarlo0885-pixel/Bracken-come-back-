from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from database import rows


@dataclass
class DatabaseHealth:
    connection_healthy: bool
    table_counts: dict[str, int]
    table_sizes: dict[str, str]
    slow_query_counters: dict[str, int]
    last_migration: str
    retention_status: str


def database_health() -> DatabaseHealth:
    try:
        counts = {
            item["table_name"]: int(item["row_estimate"] or 0)
            for item in rows(
                """
                SELECT relname AS table_name, n_live_tup AS row_estimate
                FROM pg_stat_user_tables
                ORDER BY relname
                """
            )
        }
        sizes = {
            item["table_name"]: item["size"]
            for item in rows(
                """
                SELECT relname AS table_name, pg_size_pretty(pg_total_relation_size(relid)) AS size
                FROM pg_catalog.pg_statio_user_tables
                ORDER BY relname
                """
            )
        }
        migration = rows("SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1")
        return DatabaseHealth(True, counts, sizes, {}, str(migration[0]["version"]) if migration else "", "active")
    except Exception:
        return DatabaseHealth(False, {}, {}, {}, "", "unavailable")
