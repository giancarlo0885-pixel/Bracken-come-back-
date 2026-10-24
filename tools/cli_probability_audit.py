"""Read-only, bounded audit of persisted forecast probability scale.

This tool audits evidence only, not BUY-to-WAIT decision deltas. It neither
updates tables nor reports a signal suppression rate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from typing import Any

CATEGORIES = (
    "valid_0_to_1",
    "missing_null",
    "percentage_gt1_le100",
    "nonfinite",
    "malformed_out_of_bounds",
)


def classify_probability(value: Any) -> str:
    if value is None:
        return "missing_null"
    if isinstance(value, bool):
        return "malformed_out_of_bounds"
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "malformed_out_of_bounds"
    if not math.isfinite(number):
        return "nonfinite"
    if 0.0 <= number <= 1.0:
        return "valid_0_to_1"
    if 1.0 < number <= 100.0:
        return "percentage_gt1_le100"
    return "malformed_out_of_bounds"


def summarize_probabilities(values: list[Any]) -> dict[str, int]:
    counts = {category: 0 for category in CATEGORIES}
    for value in values:
        counts[classify_probability(value)] += 1
    return counts


def audit_database(
    dsn: str,
    *,
    days: int = 7,
    batch_size: int = 500,
    max_rows: int = 100_000,
    timeout_ms: int = 15_000,
) -> dict[str, Any]:
    if not 1 <= days <= 365 or not 1 <= batch_size <= 5000 or not 1 <= max_rows <= 1_000_000:
        raise ValueError("days, batch_size or max_rows outside permitted bounds")
    if not 1000 <= timeout_ms <= 30_000:
        raise ValueError("timeout_ms must be between 1000 and 30000")
    import psycopg

    as_of = datetime.now(timezone.utc)
    cutoff = as_of - timedelta(days=days)
    counts = {category: 0 for category in CATEGORIES}
    count = 0
    sample_capped = False
    # Server-side named cursor avoids materializing the entire query result.
    # Timestamps are stored as TEXT in this schema. Casting to timestamptz
    # ensures correct UTC comparisons but fails closed on malformed dates.
    with psycopg.connect(dsn, autocommit=False, connect_timeout=10) as conn:
        try:
            with conn.cursor() as setup:
                setup.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                setup.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),))
                setup.execute("SELECT set_config('lock_timeout', '2000', true)")
            with conn.cursor(name="oracle_probability_audit") as cursor:
                cursor.itersize = batch_size
                cursor.execute(
                    """
                    SELECT probability_up
                    FROM forecasts
                    WHERE created_at IS NOT NULL
                      AND created_at::timestamptz >= %s
                      AND created_at::timestamptz <= %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (cutoff, as_of, max_rows + 1),
                )
                for row in cursor:
                    if count >= max_rows:
                        sample_capped = True
                        break
                    counts[classify_probability(row[0])] += 1
                    count += 1
        finally:
            # No writes, and explicitly end the transaction without committing.
            conn.rollback()
    return {
        "window_days": days,
        "as_of": as_of.isoformat(),
        "total_records_evaluated": count,
        "evidence_classification_summary": counts,
        "max_rows": max_rows,
        "sample_capped": sample_capped,
        "decision_impact": "not evaluated; requires timestamp-aligned engine replay",
        "timestamp_parse_policy": "invalid text timestamps fail the query closed",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-rows", type=int, default=100_000)
    parser.add_argument("--timeout-ms", type=int, default=15_000)
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("DATABASE_URL is required; no local default is assumed")
    print(json.dumps(audit_database(dsn, days=args.days, batch_size=args.batch_size,
                                    max_rows=args.max_rows, timeout_ms=args.timeout_ms), indent=2))


if __name__ == "__main__":
    main()
