"""Read-only, fail-closed Oracle paper inventory diagnostics.

This module has no trading or database-write capability. The caller supplies
a dedicated SELECT-only connection and may publish findings via an independent
outbox writer; the auditor never receives the outbox credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class Finding:
    key: str
    severity: str
    description: str
    market: str
    symbol: str


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def classify_inventory(rows: list[dict[str, Any]]) -> list[Finding]:
    """Compare canonical open-lot inventory to position inventory, never repair it.

    Input rows must be computed with a consistent database snapshot. Missing
    evidence produces UNKNOWN rather than a manufactured zero inventory.
    """
    findings: list[Finding] = []
    for row in rows:
        market, symbol = str(row["market"]), str(row["symbol"])
        expected, actual = _decimal(row.get("position_quantity")), _decimal(row.get("lot_quantity"))
        key = f"inventory:{market}:{symbol}"
        if expected is None or actual is None:
            findings.append(Finding(key, "WARNING", "Inventory evidence unavailable", market, symbol))
        elif expected < 0 or actual < 0:
            findings.append(Finding(key, "CRITICAL", "Negative position or lot quantity", market, symbol))
        elif abs(expected - actual) > Decimal("0.00000001"):
            findings.append(Finding(key, "CRITICAL", "Position and open-lot quantities disagree", market, symbol))
    return findings


def audit_inventory(conn: Any) -> list[Finding]:
    """Fail closed until schema-verified lot reconciliation is implemented.

    Never return an empty list merely because the production schema is unknown.
    """
    raise NotImplementedError(
        "Production inventory schema and lot reconciliation must be verified before audit execution"
    )
