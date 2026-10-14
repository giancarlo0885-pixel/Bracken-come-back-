"""Pure, fail-closed provenance classification for paper research.

These receipts do not infer consumption from Brain ingestion. Only an explicit
immutable decision receipt can prove that a later decision used an episode.
Historical ambiguous accounting is isolated, never rewritten.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Any


def _utc(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def classify_paper_lineage(
    *,
    trade: Mapping[str, Any],
    fill: Mapping[str, Any] | None,
    brain: Mapping[str, Any] | None,
    decision: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Check explicit IDs, exact-cost evidence and chronology; no side effects.

    decision must contain consumed_episode_keys from an immutable, persisted
    decision-time snapshot. Reconstructed context is not acceptable.
    """
    result = {"accounting": "ISOLATED", "brain": "UNVERIFIED", "decision_use": "UNVERIFIED"}
    trade_id = str(trade.get("trade_id") or "")
    if not trade_id or trade.get("broker_mode") != "PAPER" or trade.get("account_environment") != "PAPER":
        return result
    if not fill or not fill.get("fill_id") or fill.get("trade_id") != trade_id:
        return result
    if fill.get("cost_provenance") != "exact_canonical_fill" or fill.get("fee_verified") is not True:
        return result
    if not fill.get("entry_fill_id") or not fill.get("exit_fill_id"):
        return result
    entry, exit_ = _utc(trade.get("entry_time")), _utc(trade.get("exit_time"))
    fill_time = _utc(fill.get("recorded_at"))
    if not entry or not exit_ or not fill_time or not entry <= exit_ <= fill_time:
        return result
    result["accounting"] = "EXACT_LINKED"
    if not brain or brain.get("trade_id") != trade_id or brain.get("episode_key") != f"trade:{trade_id}":
        return result
    if brain.get("provenance_status") != "exact" or brain.get("cost_provenance") != "exact_lot":
        return result
    brain_time = _utc(brain.get("committed_at"))
    if not brain_time or brain_time < fill_time:
        return result
    result["brain"] = "COMMITTED_LINKED"
    if not decision or not decision.get("decision_id") or decision.get("evidence_immutable") is not True:
        return result
    decided_at = _utc(decision.get("decided_at"))
    consumed = decision.get("consumed_episode_keys")
    if not decided_at or decided_at <= brain_time or not isinstance(consumed, list):
        return result
    if f"trade:{trade_id}" not in consumed or any(not isinstance(key, str) for key in consumed):
        return result
    result["decision_use"] = "VERIFIED_CONSUMED"
    return result
