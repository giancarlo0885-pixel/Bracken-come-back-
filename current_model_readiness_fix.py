from __future__ import annotations

import json
from typing import Any


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}
    return {}


def install_current_model_readiness_fix() -> None:
    """Evaluate current leakage evidence without erasing historical failures.

    Old validation runs remain durable audit records, but a repaired validator must
    be judged by the newest run for each currently validated symbol. The previous
    readiness implementation inspected the newest 20 rows and failed forever when
    any stale run in that window had failed, even after a newer run for the same
    symbol passed. This patch keeps fail-closed semantics while selecting only the
    current evidence generation per symbol.
    """
    import oracle_readiness as readiness
    from database import rows

    def current_leakage_for_model(model: str, model_version: str) -> dict[str, Any]:
        try:
            records = rows(
                """
                SELECT DISTINCT ON (symbol)
                       run_id, symbol, leakage_checks, created_at
                FROM walk_forward_validation_runs
                WHERE model=%s AND COALESCE(model_version,'')=COALESCE(%s,'')
                ORDER BY symbol, created_at DESC, id DESC
                """,
                (model, model_version),
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "UNAVAILABLE",
                "reason": exc.__class__.__name__,
                "run_count": 0,
                "evidence_scope": "latest_per_symbol",
            }
        if not records:
            return {
                "ok": False,
                "status": "NO_EVIDENCE",
                "run_count": 0,
                "evidence_scope": "latest_per_symbol",
            }

        failures: list[dict[str, str]] = []
        passed_symbols: list[str] = []
        for item in records:
            leakage = _json_object(item.get("leakage_checks"))
            probe = _json_object(leakage.get("future_mutation_probe"))
            symbol = str(item.get("symbol") or "UNKNOWN")
            strict_ok = leakage.get("strict_ordering") is True
            probe_ok = probe.get("ok") is True
            if strict_ok and probe_ok:
                passed_symbols.append(symbol)
            else:
                failures.append(
                    {
                        "symbol": symbol,
                        "run_id": str(item.get("run_id") or "run"),
                        "reason": str(probe.get("reason") or "causality check failed"),
                    }
                )

        return {
            "ok": not failures,
            "status": "PASS" if not failures else "FAIL",
            "run_count": len(records),
            "failed_run_count": len(failures),
            "passed_symbols": passed_symbols,
            "failures": failures,
            "evidence_scope": "latest_per_symbol",
        }

    current_leakage_for_model._oracle_current_per_symbol = True
    readiness._leakage_for_model = current_leakage_for_model
