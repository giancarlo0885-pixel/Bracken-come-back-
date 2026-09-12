from __future__ import annotations

import math
import os
from typing import Any

from market_memory import feature_vector
from paper_regime_entry_signal_fallback import install_entry_signal_regime_fallback


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


def _runtime_signal_value(signal: Any, key: str) -> Any:
    if isinstance(signal, dict):
        return signal.get(key)
    return getattr(signal, key, None)


def _enrich_persisted_signal_payload(signal: Any, existing: Any) -> dict[str, Any]:
    """Add only observed entry-time regime inputs to persisted signal JSON.

    This is persistence-only telemetry. It never mutates the runtime signal and
    never supplies defaults, later prices, realized outcomes, or inferred labels.
    Existing persisted values remain authoritative.
    """
    payload = dict(existing) if isinstance(existing, dict) else {}
    for key in _RAW_REGIME_FIELDS:
        if key in payload and _finite(payload.get(key)) is not None:
            continue
        value = _finite(_runtime_signal_value(signal, key))
        if value is not None:
            payload[key] = value
    return payload


def _install_signal_payload_provenance(worker_module: Any) -> bool:
    original = getattr(worker_module, "_signal_payload", None)
    if original is None:
        return False
    if getattr(original, "_paper_regime_signal_payload_v1", False):
        return True

    def wrapped(signal: Any, route: dict[str, Any], scan_type: str, **extra: Any) -> dict[str, Any]:
        payload = original(signal, route, scan_type, **extra)
        return _enrich_persisted_signal_payload(signal, payload)

    wrapped._paper_regime_signal_payload_v1 = True  # type: ignore[attr-defined]
    wrapped._paper_regime_signal_payload_original = original  # type: ignore[attr-defined]
    worker_module._signal_payload = wrapped
    return True


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


def install_paper_regime_entry_provenance(
    oracle_module: Any | None = None,
    worker_module: Any | None = None,
) -> bool:
    """Enrich paper entry persistence/provenance only; execution is untouched."""
    if not active():
        return False

    production_install = oracle_module is None
    if oracle_module is None:
        import oracle_bot as oracle_module
    if worker_module is None and production_install:
        import market_worker as worker_module

    # Persist the observed runtime fields in the immutable signal record before
    # execution transforms the signal into lots/ledger provenance. This wrapper
    # changes only JSON persistence; it does not mutate the signal or gate trades.
    if worker_module is not None:
        _install_signal_payload_provenance(worker_module)

    original = oracle_module._entry_provenance
    if getattr(original, "_paper_regime_entry_provenance_v1", False):
        install_entry_signal_regime_fallback()
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
    # Install before the shadow sampler starts so its startup materialization and
    # every later cycle can repair unknown labels from the exact entry signal.
    install_entry_signal_regime_fallback()
    return True
