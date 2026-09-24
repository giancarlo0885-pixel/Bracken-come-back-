from __future__ import annotations

import json
import logging
import math
import os
from typing import Any


log = logging.getLogger("paper-regime-entry-signal-fallback")
_RAW_REGIME_FIELDS = ("trend_strength", "momentum_20d", "volatility_20d")
_SCHEMA_VERSION = "regime-economics-v1-entry-signal-fallback3"


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


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except Exception:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _entry_signal_features(conn: Any, entry_signal_id: Any) -> dict[str, float]:
    """Read only the exact persisted entry signal identified by immutable provenance.

    Canonical signal JSON is stored in ``signals.details``. Alias it to ``payload``
    locally so the remainder of this shadow-only provenance reader can keep a
    stable internal representation without changing the production schema.
    """
    signal_id = str(entry_signal_id or "").strip()
    if not signal_id:
        return {}
    try:
        item = conn.execute(
            "SELECT details AS payload FROM signals WHERE id::text=%s LIMIT 1",
            (signal_id,),
        ).fetchone()
    except Exception:
        return {}
    payload = _json_obj(item.get("payload")) if item else {}
    nested = _json_obj(payload.get("features"))
    result: dict[str, float] = {}
    for key in _RAW_REGIME_FIELDS:
        value = _finite(payload.get(key))
        if value is None:
            value = _finite(nested.get(key))
        if value is not None:
            result[key] = value
    return result


def _merge_entry_features(existing: Any, persisted_signal: Any) -> dict[str, Any]:
    """Fill missing regime fields only; immutable ledger values always win."""
    merged = _json_obj(existing)
    source = _json_obj(persisted_signal)
    for key in _RAW_REGIME_FIELDS:
        if _finite(merged.get(key)) is not None:
            continue
        value = _finite(source.get(key))
        if value is not None:
            merged[key] = value
    return merged


def repair_unknown_regimes(limit: int = 1000, market: str = "crypto") -> int:
    """Enrich incomplete shadow regime labels from the exact persisted entry signal.

    Earlier versions only revisited ``*vol_unknown`` rows. Once volatility began
    persisting earlier in the signal pipeline, a partially observed row could be
    materialized as ``range__low_vol`` and then become ineligible for enrichment,
    even though the same immutable entry signal also contained trend/momentum.
    Re-evaluate recent attributable rows only when the ledger snapshot is missing
    at least one raw regime field and the exact entry signal supplies additional
    evidence. This remains measurement-only and never touches execution state.
    """
    if not active():
        return 0
    from database import connect
    from paper_regime_economics_shadow import _normalize_market, classify_regime

    normalized_market = _normalize_market(market)
    repaired = 0
    with connect() as conn:
        rows = list(
            conn.execute(
                """
                SELECT m.trade_id, m.regime, tl.entry_signal_id, tl.feature_snapshot
                FROM paper_regime_trade_metrics m
                JOIN trade_ledger tl ON tl.trade_id=m.trade_id
                WHERE m.market=%s
                  AND tl.entry_signal_id IS NOT NULL
                ORDER BY m.exit_time DESC
                LIMIT %s
                """,
                (normalized_market, max(1, int(limit))),
            ).fetchall()
        )
        for row in rows:
            existing = _json_obj(row.get("feature_snapshot"))
            missing = {key for key in _RAW_REGIME_FIELDS if _finite(existing.get(key)) is None}
            if not missing:
                continue
            signal_features = _entry_signal_features(conn, row.get("entry_signal_id"))
            if not any(key in signal_features for key in missing):
                continue
            features = _merge_entry_features(existing, signal_features)
            # Never manufacture a volatility bucket from classifier defaults. A
            # repaired label is admissible only when volatility is actually
            # observed in immutable entry evidence or the exact entry signal.
            if _finite(features.get("volatility_20d")) is None:
                continue
            regime = classify_regime(feature_snapshot=features, memory_regime=None)
            current = str(row.get("regime") or "")
            if not regime or regime.endswith("vol_unknown") or regime == current:
                continue
            conn.execute(
                """
                UPDATE paper_regime_trade_metrics
                SET regime=%s, schema_version=%s
                WHERE trade_id=%s AND regime=%s
                """,
                (regime, _SCHEMA_VERSION, str(row.get("trade_id") or ""), current),
            )
            repaired += 1
    return repaired


def install_entry_signal_regime_fallback(shadow_module: Any | None = None) -> bool:
    """Attach a shadow-only post-materialization provenance repair.

    The wrapper never alters orders, sizing, cooldowns, quotes, positions, P&L, or
    accounting. It only improves a regime label when the canonical ledger carries
    an exact immutable entry_signal_id whose persisted signal details add observed
    regime fields missing from the immutable ledger snapshot.
    """
    if not active():
        return False
    if shadow_module is None:
        import paper_regime_economics_shadow as shadow_module

    original = shadow_module.finalize_closed_trades
    if getattr(original, "_entry_signal_regime_fallback_v1", False):
        return True

    def wrapped(limit: int = 250, market: str = "crypto") -> int:
        created = original(limit, market=market)
        try:
            repaired = repair_unknown_regimes(max(1000, int(limit)), market=market)
            if repaired:
                log.info(
                    "PAPER REGIME ENTRY SIGNAL FALLBACK | repaired=%s | source=immutable_entry_signal | "
                    "execution_impact=NONE | live_trading=DISARMED",
                    repaired,
                )
        except Exception as exc:
            log.warning(
                "PAPER REGIME ENTRY SIGNAL FALLBACK | status=UNAVAILABLE | reason=%s | execution_impact=NONE",
                exc.__class__.__name__,
            )
        return created

    wrapped._entry_signal_regime_fallback_v1 = True  # type: ignore[attr-defined]
    wrapped._entry_signal_regime_fallback_original = original  # type: ignore[attr-defined]
    shadow_module.finalize_closed_trades = wrapped
    return True
