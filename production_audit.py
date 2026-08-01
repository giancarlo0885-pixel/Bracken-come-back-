from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from database import connect, utc_now
from provider_router import normalize_symbol


MERGE_COMMIT_SHA = "3a9d5de6d609b934c2619c7fde8c449787195a43"
AUDIT_STATUSES = {"unreviewed", "valid", "suspected_price_corruption", "invalid", "reviewed"}


@dataclass
class AuditReport:
    merge_commit_sha: str
    affected_symbols: list[str]
    trades: int
    positions: int
    realized_pnl_impact: float
    unrealized_pnl_impact: float
    destructive_changes: int = 0


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _before_cutoff(record: dict[str, Any], cutoff: datetime) -> bool:
    created = _parse_time(record.get("created_at") or record.get("opened_at"))
    return created is not None and created < cutoff


def build_audit_report(
    trades: Iterable[dict[str, Any]],
    positions: Iterable[dict[str, Any]],
    *,
    cutoff: datetime,
    merge_commit_sha: str = MERGE_COMMIT_SHA,
) -> AuditReport:
    flagged_trades = [dict(item) for item in trades if _before_cutoff(dict(item), cutoff)]
    flagged_positions = [dict(item) for item in positions if _before_cutoff(dict(item), cutoff)]
    symbols = sorted(
        {
            normalize_symbol(item.get("symbol"))
            for item in flagged_trades + flagged_positions
            if normalize_symbol(item.get("symbol"))
        }
    )
    realized = sum(float(item.get("realized_pnl") or 0.0) for item in flagged_trades)
    unrealized = 0.0
    for position in flagged_positions:
        quantity = float(position.get("quantity") or 0.0)
        current = float(position.get("current_price") or 0.0)
        entry = float(position.get("average_price") or position.get("entry_price") or 0.0)
        unrealized += (current - entry) * quantity
    return AuditReport(
        merge_commit_sha=merge_commit_sha,
        affected_symbols=symbols,
        trades=len(flagged_trades),
        positions=len(flagged_positions),
        realized_pnl_impact=realized,
        unrealized_pnl_impact=unrealized,
    )


def audit_legacy_paper_records(cutoff: datetime, *, mark: bool = True) -> AuditReport:
    with connect() as conn:
        trades = list(conn.execute("SELECT * FROM trades ORDER BY id").fetchall())
        positions = list(conn.execute("SELECT * FROM positions ORDER BY id").fetchall())
        report = build_audit_report(trades, positions, cutoff=cutoff)
        if mark:
            now = utc_now()
            for item in trades:
                if not _before_cutoff(dict(item), cutoff):
                    continue
                conn.execute(
                    """
                    INSERT INTO paper_data_audit(record_type, record_id, market, symbol, status, reason, payload, created_at)
                    VALUES ('trade', %s, %s, %s, 'suspected_price_corruption', %s, %s::jsonb, %s)
                    """,
                    (item.get("id"), item.get("market"), item.get("symbol"), f"created before merge commit {MERGE_COMMIT_SHA}", "{}", now),
                )
            for item in positions:
                if not _before_cutoff(dict(item), cutoff):
                    continue
                conn.execute(
                    """
                    INSERT INTO paper_data_audit(record_type, record_id, market, symbol, status, reason, payload, created_at)
                    VALUES ('position', %s, %s, %s, 'suspected_price_corruption', %s, %s::jsonb, %s)
                    """,
                    (item.get("id"), item.get("market"), item.get("symbol"), f"opened before merge commit {MERGE_COMMIT_SHA}", "{}", now),
                )
        return report


def clean_paper_portfolio_command(market: str, *, approved_by: str = "") -> dict[str, Any]:
    """Return the protected command metadata without executing a reset/create action."""
    return {
        "command": "create_clean_paper_portfolio",
        "market": market,
        "approved_by": approved_by,
        "requires_explicit_operator_confirmation": True,
        "executed": False,
        "reason": "Historical records are never deleted or rewritten by the audit command.",
    }


if __name__ == "__main__":
    cutoff_text = input("Cutoff timestamp for merge commit 3a9d5de6d609b934c2619c7fde8c449787195a43: ").strip()
    cutoff = _parse_time(cutoff_text)
    if cutoff is None:
        raise SystemExit("Invalid cutoff timestamp.")
    print(audit_legacy_paper_records(cutoff, mark=True))
