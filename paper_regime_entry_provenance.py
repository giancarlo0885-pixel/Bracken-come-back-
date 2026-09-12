from __future__ import annotations

import math
import os
from typing import Any

from market_memory import feature_vector


_RAW_REGIME_FIELDS = ("trend_strength", "momentum_20d", "volatility_20d")


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def active() -> bool:
    return bool(
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _enrich_entry_features(oracle_module: Any, signal: Any, existing: Any) -> dict[str, Any]:
    """Preserve exact entry-time regime inputs without using future/P&L data."""
    features = dict(existing) if isinstance(existing, dict) else {}

    # Keep the raw runtime regime inputs when present. These are the same fields
    # used by the decision/risk pipeline at entry time and are never backfilled
    # from later prices or realized outcomes.
    for key in _RAW_REGIME_FIELDS:
        if key in features and _finite(features.get(key)) is not None:
            continue
        value = _finite(oracle_module.signal_value(signal, key, None))
        if value is not None:
            features[key] = value

    # Some strategy signals do not expose the raw fields directly. In that case,
    # retain the existing portable entry fingerprint rather than an empty JSON
    # object. Do not overwrite any exact fields already captured above.
    if not features and signal is not None:
        try:
            fallback = feature_vector(signal)
        except Exception:
            fallback = {}
        if isinstance(fallback, dict):
            features.update(fallback)
    return features


def install_paper_regime_entry_provenance(oracle_module: Any | None = None) -> bool:
    """Enrich paper entry provenance only; execution behavior is untouched."""
    if not active():
        return False
    if oracle_module is None:
        import oracle_bot as oracle_module

    original = oracle_module._entry_provenance
    if getattr(original, "_paper_regime_entry_provenance_v1", False):
        return True

    def wrapped(*, signal: Any | None, quote_metadata: dict[str, Any] | None, now: str,
                risk_snapshot: dict[str, Any] | None = None,
                portfolio_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        provenance = original(
            signal=signal,
            quote_metadata=quote_metadata,
            now=now,
            risk_snapshot=risk_snapshot,
            portfolio_snapshot=portfolio_snapshot,
        )
        provenance = dict(provenance or {})
        provenance["feature_snapshot"] = _enrich_entry_features(
            oracle_module,
            signal,
            provenance.get("feature_snapshot"),
        )
        return provenance

    wrapped._paper_regime_entry_provenance_v1 = True  # type: ignore[attr-defined]
    wrapped._paper_regime_entry_provenance_original = original  # type: ignore[attr-defined]
    oracle_module._entry_provenance = wrapped
    return True
