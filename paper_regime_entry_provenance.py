from __future__ import annotations

import math
import os
from typing import Any

from entry_pattern_memory_runtime import install_entry_pattern_memory_runtime
from market_memory import feature_vector
from mempool_space_runtime import install_mempool_space_network_context
from paper_regime_entry_signal_fallback import install_entry_signal_regime_fallback


_RAW_REGIME_FIELDS = ("trend_strength", "momentum_20d", "volatility_20d")
_AEVE_ENTRY_FIELDS = (
    "net_expected_value_pct", "expected_return_pct", "forecast_return_pct",
    "possible_move_pct", "expected_move_pct", "edge_pct",
    "dip_depth_pct", "rebound_pct", "drawdown_from_recent_high_pct",
    "rsi_14", "rsi_change", "reclaim_strength",
)
_BTC_NETWORK_PAYLOAD_FIELDS = (
    "btc_network_source",
    "btc_network_observed_at",
    "btc_network_block_height",
    "btc_network_current_hashrate",
    "btc_network_current_difficulty",
    "btc_network_hash_rate_change",
    "btc_network_difficulty_change",
    "btc_network_block_interval_seconds",
    "btc_network_mempool_count",
    "btc_network_mempool_vsize",
    "btc_network_mempool_total_fee",
    "btc_network_fastest_fee_sat_vb",
    "btc_network_half_hour_fee_sat_vb",
    "btc_network_hour_fee_sat_vb",
    "btc_network_economy_fee_sat_vb",
    "btc_network_minimum_fee_sat_vb",
    "btc_network_fee_pressure",
    "btc_network_mempool_pressure",
    "btc_network_available",
    "btc_network_subsidy_btc",
    "btc_network_blocks_to_halving",
    "btc_network_halving_progress",
    "btc_network_security_score",
    "btc_network_activity_score",
    "btc_network_miner_stress_score",
    "btc_network_execution_impact",
)
_BTC_NETWORK_FEATURE_FIELDS = (
    "btc_network_hash_rate_change",
    "btc_network_difficulty_change",
    "btc_network_fee_pressure",
    "btc_network_mempool_pressure",
    "btc_network_halving_progress",
    "btc_network_security_score",
    "btc_network_activity_score",
    "btc_network_miner_stress_score",
)


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
    payload = dict(existing) if isinstance(existing, dict) else {}
    for key in _RAW_REGIME_FIELDS + _AEVE_ENTRY_FIELDS:
        if key in payload and _finite(payload.get(key)) is not None:
            continue
        value = _finite(_runtime_signal_value(signal, key))
        if value is not None:
            payload[key] = value

    # Preserve only values actually observed/derived from the cached provider snapshot.
    # This is immutable entry evidence and does not affect action, sizing, or execution.
    for key in _BTC_NETWORK_PAYLOAD_FIELDS:
        if key in payload:
            continue
        value = _runtime_signal_value(signal, key)
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
    features = dict(existing) if isinstance(existing, dict) else {}
    for key in _RAW_REGIME_FIELDS + _AEVE_ENTRY_FIELDS:
        if key in features and _finite(features.get(key)) is not None:
            continue
        value = _finite(oracle_module.signal_value(signal, key, None))
        if value is not None:
            features[key] = value

    for key in _BTC_NETWORK_FEATURE_FIELDS:
        if key in features and _finite(features.get(key)) is not None:
            continue
        value = _finite(_runtime_signal_value(signal, key))
        if value is not None:
            features[key] = value

    if signal is not None:
        try:
            fallback = oracle_module.feature_vector(signal)
        except Exception:
            fallback = {}
        if isinstance(fallback, dict):
            for key, value in fallback.items():
                features.setdefault(key, value)
    return features


def install_paper_regime_entry_provenance(
    oracle_module: Any | None = None,
    worker_module: Any | None = None,
) -> bool:
    """Enrich paper entry persistence/provenance only; execution is untouched."""
    if not active():
        return False

    # Install the broader entry fingerprint first so the immutable lot snapshot,
    # observation store, opportunity engine and analog matcher all see identical
    # RSI/dip/rebound/reclaim evidence at entry time.
    install_entry_pattern_memory_runtime()

    production_install = oracle_module is None
    if oracle_module is None:
        import oracle_bot as oracle_module
    if worker_module is None and production_install:
        import market_worker as worker_module

    if worker_module is not None:
        # Adds cached, observed-only Bitcoin network context to BTC paper signals.
        # It is explicitly measurement-only and fails soft on provider errors/429s.
        install_mempool_space_network_context(worker_module)
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
    install_entry_signal_regime_fallback()
    return True
