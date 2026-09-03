from __future__ import annotations

import json
import os
from typing import Any


_DEFAULT_SYMBOLS = ("BTC-USD", "ETH-USD", "SOL-USD")


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


def _active_symbols() -> list[str]:
    raw = os.getenv("CAPITAL_CRYPTO_SYMBOLS", ",".join(_DEFAULT_SYMBOLS))
    symbols: list[str] = []
    for value in str(raw or "").split(","):
        symbol = value.strip().upper()
        if symbol.endswith("-USD") and symbol not in symbols:
            symbols.append(symbol)
    return symbols[:12] or list(_DEFAULT_SYMBOLS)


def install_current_model_readiness_fix() -> None:
    """Evaluate current leakage evidence without erasing historical failures.

    Old validation runs remain durable audit records. Readiness is based on the
    newest run for every symbol in the *active* validation universe, and it fails
    closed if any expected symbol is missing or currently fails causality. This
    prevents obsolete symbols/runs from poisoning current readiness forever while
    preserving their rows for audit and model-governance history.
    """
    import oracle_readiness as readiness
    from database import rows

    def current_leakage_for_model(model: str, model_version: str) -> dict[str, Any]:
        expected = _active_symbols()
        placeholders = ",".join(["%s"] * len(expected))
        try:
            records = rows(
                f"""
                SELECT DISTINCT ON (symbol)
                       run_id, symbol, leakage_checks, created_at
                FROM walk_forward_validation_runs
                WHERE model=%s
                  AND COALESCE(model_version,'')=COALESCE(%s,'')
                  AND symbol IN ({placeholders})
                ORDER BY symbol, created_at DESC, run_id DESC
                """,
                tuple([model, model_version, *expected]),
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "UNAVAILABLE",
                "reason": exc.__class__.__name__,
                "run_count": 0,
                "expected_symbols": expected,
                "evidence_scope": "latest_active_symbol_set",
            }

        by_symbol = {str(item.get("symbol") or "").upper(): item for item in (records or [])}
        missing = [symbol for symbol in expected if symbol not in by_symbol]
        failures: list[dict[str, str]] = []
        passed_symbols: list[str] = []

        for symbol in expected:
            item = by_symbol.get(symbol)
            if item is None:
                continue
            leakage = _json_object(item.get("leakage_checks"))
            probe = _json_object(leakage.get("future_mutation_probe"))
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

        ok = not missing and not failures and len(passed_symbols) == len(expected)
        return {
            "ok": ok,
            "status": "PASS" if ok else ("NO_EVIDENCE" if missing and not failures else "FAIL"),
            "run_count": len(records or []),
            "failed_run_count": len(failures),
            "expected_symbols": expected,
            "passed_symbols": passed_symbols,
            "missing_symbols": missing,
            "failures": failures,
            "evidence_scope": "latest_active_symbol_set",
        }

    current_leakage_for_model._oracle_current_per_symbol = True
    readiness._leakage_for_model = current_leakage_for_model
