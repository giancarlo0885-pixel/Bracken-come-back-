from __future__ import annotations

from datetime import datetime, timezone

from database import initialize_database, rows
from migrations import run_migrations


def _age_minutes(value: object) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 60.0)
    except (TypeError, ValueError):
        return None


def main() -> int:
    try:
        initialize_database()
        applied = run_migrations()
        workers = rows(
            "SELECT market,status,message,last_run,heartbeat FROM market_worker_status "
            "WHERE market IN ('cash','crypto') ORDER BY market"
        )
        normalized = []
        for worker in workers:
            normalized.append({**worker, "heartbeat_age_minutes": _age_minutes(worker.get("heartbeat"))})
        markets = {str(worker.get("market")) for worker in workers}
        missing = sorted({"cash", "crypto"} - markets)
        print({
            "ok": not missing,
            "database": "connected",
            "migrations_applied": applied,
            "missing_workers": missing,
            "workers": normalized,
        })
        return 0 if not missing else 1
    except Exception as exc:
        print({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
