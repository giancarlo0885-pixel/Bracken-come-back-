from __future__ import annotations

import logging
import sys
from typing import Any


log = logging.getLogger("entry-pattern-memory")
_INSTALLED = False


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if number == number else default
    except (TypeError, ValueError):
        return default


def _value(signal: Any, name: str, default: Any = None) -> Any:
    return signal.get(name, default) if isinstance(signal, dict) else getattr(signal, name, default)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def expanded_feature_vector(signal: Any, base: dict[str, float] | None = None) -> dict[str, float]:
    """Add entry-shape evidence to the durable Market Memory fingerprint."""
    features = dict(base or {})
    features.update({
        "rsi_14": (_clip(_num(_value(signal, "rsi_14", 50.0)), 0.0, 100.0) - 50.0) / 50.0,
        "rsi_change": _clip(_num(_value(signal, "rsi_change", 0.0)), -25.0, 25.0) / 25.0,
        "atr_pct": _clip(_num(_value(signal, "atr_pct", 0.0)), 0.0, 0.20) / 0.20,
        "bollinger_position": (_clip(_num(_value(signal, "bollinger_position", 0.5)), -0.5, 1.5) - 0.5),
        "macd_hist_norm": _clip(_num(_value(signal, "macd_hist", 0.0)) / max(abs(_num(_value(signal, "price", 1.0))) * 0.01, 1e-9), -1.0, 1.0),
        "mean_reversion_score": _clip(_num(_value(signal, "mean_reversion_score", 0.0)), -1.0, 1.0),
        "dip_rebound_score": _clip(_num(_value(signal, "dip_rebound_score", 0.0)), -1.0, 1.0),
        "dip_depth": _clip(_num(_value(signal, "dip_depth_pct", 0.0)), 0.0, 0.20) / 0.20,
        "rebound": _clip(_num(_value(signal, "rebound_pct", 0.0)), 0.0, 0.20) / 0.20,
        "recent_drawdown": _clip(_num(_value(signal, "drawdown_from_recent_high_pct", 0.0)), 0.0, 0.20) / 0.20,
        "reclaim_strength": _clip(_num(_value(signal, "reclaim_strength", 0.0)), -0.10, 0.10) / 0.10,
        "dip_rebound_pattern": 1.0 if str(_value(signal, "entry_pattern", "")).strip().lower() == "dip_rebound" else 0.0,
    })
    return features


def install_entry_pattern_memory_runtime() -> bool:
    """Expand memory comparisons without changing historical provenance rules."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import market_memory

    original = market_memory.feature_vector

    def feature_vector(signal: Any) -> dict[str, float]:
        return expanded_feature_vector(signal, original(signal))

    market_memory.feature_vector = feature_vector
    market_memory.WEIGHTS.update({
        "rsi_14": 1.0,
        "rsi_change": 0.8,
        "atr_pct": 0.8,
        "bollinger_position": 0.7,
        "macd_hist_norm": 0.7,
        "mean_reversion_score": 0.9,
        "dip_rebound_score": 1.3,
        "dip_depth": 1.2,
        "rebound": 1.2,
        "recent_drawdown": 0.9,
        "reclaim_strength": 1.0,
        "dip_rebound_pattern": 1.5,
    })

    # Several modules import feature_vector by value. Rebind those references so
    # persisted entry provenance and opportunity snapshots use the same expanded
    # fingerprint as analog matching.
    for module_name in ("oracle_bot", "oracle_intelligence", "opportunity_engine", "paper_regime_entry_provenance"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "feature_vector"):
            setattr(module, "feature_vector", feature_vector)

    _INSTALLED = True
    log.info(
        "Installed expanded entry-pattern memory | RSI/dip/rebound/reclaim/ATR/Bollinger/MACD=ACTIVE | provenance=EXACT_ENTRY_LOTS"
    )
    return True
