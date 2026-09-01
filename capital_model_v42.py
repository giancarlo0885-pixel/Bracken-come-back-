from __future__ import annotations

import logging
import os
from typing import Any

from capital_model_evidence_runtime import _build_symbol_evidence, _evidence_current, _int_env, _recent_evidence
from capital_model_governance import apply_model_governance
from forecasting import active_crypto_model_identity
from model_registry import register_model


log = logging.getLogger("capital-model-v42")

DEFAULT_SYMBOLS = ("BTC-USD", "ETH-USD", "SOL-USD")
DEFAULT_PERIOD = "30d"
DEFAULT_INTERVAL = "5m"
DEFAULT_HORIZON_BARS = 3
DEFAULT_MIN_HISTORY_BARS = 240
DEFAULT_STRIDE = 30


def _symbols() -> list[str]:
    raw = os.getenv("CAPITAL_CRYPTO_SYMBOLS", ",".join(DEFAULT_SYMBOLS))
    symbols: list[str] = []
    for item in str(raw or "").split(","):
        symbol = item.strip().upper()
        if symbol.endswith("-USD") and symbol not in symbols:
            symbols.append(symbol)
    return symbols[:12] or list(DEFAULT_SYMBOLS)


def refresh_v42_crypto_evidence(*, force: bool = False) -> list[dict[str, Any]]:
    """Generate outer walk-forward evidence for the v42 sign-transition model.

    The inner predictor estimates sign transition probabilities only from labels
    resolved before each decision timestamp. Existing Oracle governance owns all
    thresholds; this runner does not change accuracy, Brier, ECE, baseline,
    regime-count, sample, leakage, capital, quote, or execution requirements.
    """
    model, version = active_crypto_model_identity()
    register_model(model, version, "shadow", "requires causal sign-transition walk-forward validation")

    if not force and _evidence_current(model, version):
        assessment = apply_model_governance(model, version)
        return [
            {
                "model": model,
                "model_version": version,
                "status": "CURRENT",
                "governance_status": assessment.recommended_status,
                "eligible_for_approval": assessment.eligible_for_approval,
                **_recent_evidence(model, version),
            }
        ]

    period = str(os.getenv("CAPITAL_CRYPTO_WALK_FORWARD_PERIOD", DEFAULT_PERIOD) or DEFAULT_PERIOD)
    interval = str(os.getenv("CAPITAL_CRYPTO_WALK_FORWARD_INTERVAL", DEFAULT_INTERVAL) or DEFAULT_INTERVAL)
    horizon_bars = _int_env("CAPITAL_CRYPTO_WALK_FORWARD_HORIZON_BARS", DEFAULT_HORIZON_BARS, 1, 60)
    minimum_history_bars = _int_env(
        "CAPITAL_CRYPTO_WALK_FORWARD_MIN_HISTORY_BARS",
        DEFAULT_MIN_HISTORY_BARS,
        180,
        2000,
    )
    stride = _int_env("CAPITAL_CRYPTO_WALK_FORWARD_STRIDE", DEFAULT_STRIDE, 1, 100)

    results: list[dict[str, Any]] = []
    for symbol in _symbols():
        try:
            item = _build_symbol_evidence(
                symbol,
                model,
                version,
                period=period,
                interval=interval,
                horizon_bars=horizon_bars,
                minimum_history_bars=minimum_history_bars,
                stride=stride,
            )
        except Exception as exc:
            item = {
                "symbol": symbol,
                "status": "UNAVAILABLE",
                "sample_count": 0,
                "reason": exc.__class__.__name__,
            }
        results.append(item)
        log.info(
            "CAPITAL MODEL V42 SYMBOL | symbol=%s | status=%s | samples=%s | accuracy=%s | ece=%s | brier_skill=%s | beats_baselines=%s | leakage_ok=%s",
            symbol,
            item.get("status"),
            item.get("sample_count"),
            item.get("directional_accuracy"),
            item.get("expected_calibration_error"),
            item.get("brier_skill_score"),
            item.get("beats_all_baselines"),
            item.get("temporal_leakage_ok"),
        )

    assessment = apply_model_governance(model, version)
    return [
        {
            "model": model,
            "model_version": version,
            "status": "REFRESHED",
            "governance_status": assessment.recommended_status,
            "eligible_for_approval": assessment.eligible_for_approval,
            "symbols": results,
            **_recent_evidence(model, version),
        }
    ]
