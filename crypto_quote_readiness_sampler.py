from __future__ import annotations

from collections import defaultdict
import math
import os
import time
from typing import Any

import oracle_bot
from crypto_execution_guard import (
    _coinbase_reference_validation,
    _paper_yahoo_reference,
    _persist_quote_verifications,
    _quote_verification_record,
    _symbol,
)
from shadow_forward_sampler import maintain_passive_shadow_evidence


_LAST_SHADOW_STATUS_LOG = 0.0


def _sample_limit() -> int:
    try:
        return max(1, min(12, int(os.getenv("V39_QUOTE_VERIFICATION_SAMPLE_SIZE", "6"))))
    except ValueError:
        return 6


def _shadow_status_log_interval() -> int:
    try:
        return max(30, min(900, int(os.getenv("SHADOW_STATUS_LOG_INTERVAL_SECONDS", "60"))))
    except ValueError:
        return 60


def _emit_shadow_status(worker: Any, result: dict[str, Any]) -> None:
    """Emit bounded sanitized evidence-collection status without broker details."""
    global _LAST_SHADOW_STATUS_LOG
    capture = result.get("capture") if isinstance(result.get("capture"), dict) else {}
    evaluate = result.get("evaluate") if isinstance(result.get("evaluate"), dict) else {}
    captured = int(capture.get("captured") or 0)
    evaluated = int(evaluate.get("evaluated") or 0)
    now = time.monotonic()
    noteworthy = captured > 0 or evaluated > 0 or str(capture.get("status") or "") in {
        "BROKER_PAIR_DISCOVERY_UNAVAILABLE",
    }
    if not noteworthy and now - _LAST_SHADOW_STATUS_LOG < _shadow_status_log_interval():
        return
    _LAST_SHADOW_STATUS_LOG = now
    worker.log.info(
        "CRYPTO | PASSIVE SHADOW STATUS | overall=%s | capture_status=%s | captured=%d | skipped=%d | "
        "capture_reason=%s | evaluate_status=%s | evaluated=%d | due=%d | broker_submission=NONE",
        str(result.get("status") or "UNKNOWN"),
        str(capture.get("status") or "UNKNOWN"),
        captured,
        int(capture.get("skipped") or 0),
        str(capture.get("reason") or "")[:80] or "none",
        str(evaluate.get("status") or "UNKNOWN"),
        evaluated,
        int(evaluate.get("due") or 0),
    )


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _snapshot_payload(snapshot: Any, symbol: str) -> dict[str, Any] | None:
    """Convert a validated broker snapshot into generic quote-evidence payload."""
    if snapshot is None:
        return None
    if isinstance(snapshot, dict):
        payload = dict(snapshot)
    else:
        converter = getattr(snapshot, "to_quote_payload", None)
        if not callable(converter):
            return None
        try:
            payload = dict(converter() or {})
        except Exception:
            return None

    normalized = str(symbol or "").upper().strip()
    actual_symbol = str(payload.get("symbol") or normalized).upper().strip()
    provider_symbol = str(payload.get("provider_symbol") or actual_symbol).upper().strip()
    requested_symbol = str(payload.get("requested_symbol") or normalized).upper().strip()
    price = _finite_positive(payload.get("price"))
    if price is None or actual_symbol != normalized or provider_symbol != normalized or requested_symbol != normalized:
        return None

    payload["symbol"] = normalized
    payload["requested_symbol"] = normalized
    payload["provider_symbol"] = normalized
    payload["price"] = price
    payload["provider"] = str(payload.get("provider") or "Robinhood Crypto")
    payload["provider_quote_verified"] = True
    payload["quote_verified"] = True
    payload["execution_quote_eligible"] = True
    payload.setdefault("stale", False)
    return payload


def _primary_quote(worker: Any, symbol: str, quote_map: dict[str, Any]) -> dict[str, Any] | None:
    """Prefer the current broker snapshot; fall back to canonical paper reference."""
    provider = getattr(worker, "_robinhood_current_marketdata_provider", None)
    if provider is not None:
        try:
            snapshots = dict(provider.snapshots([symbol]) or {})
            payload = _snapshot_payload(snapshots.get(symbol), symbol)
            if payload is not None:
                return payload
        except Exception:
            # Readiness evidence is best-effort and cannot affect execution. Fall
            # back to the canonical worker quote path when broker sampling fails.
            pass

    quote = oracle_bot._verified_quote_for(symbol, quote_map, "crypto")
    if quote is None:
        return None
    payload = dict(quote)
    provider_name = str(payload.get("provider") or "").strip().lower()
    if provider_name == "yahoo finance" and not _paper_yahoo_reference(payload):
        return None
    if _finite_positive(payload.get("price")) is None:
        return None
    return payload


def persist_v39_quote_verification_evidence(
    worker: Any,
    signals: Any,
    prices: dict[str, Any] | None,
    *,
    max_samples: int | None = None,
) -> int:
    """Persist bounded independent Coinbase evidence for current crypto quotes.

    Robinhood is the preferred primary current quote authority. Yahoo remains a
    paper-reference fallback when broker sampling is unavailable. The execution
    guard still performs the authoritative entry gate. This sampler is evidence-
    only: it never changes signal actions, optimizer allocations, quotes, or order
    execution and it never submits broker orders.
    """
    quote_map = prices or {}
    limit = _sample_limit() if max_samples is None else max(1, min(12, int(max_samples)))
    evidence: list[dict[str, Any]] = []
    blocked: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    primary_providers: set[str] = set()

    for signal in list(signals or []):
        if len(evidence) >= limit:
            break
        symbol = _symbol(signal)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)

        quote = _primary_quote(worker, symbol, quote_map)
        if quote is None:
            continue
        provider_name = str(quote.get("provider") or "unknown").strip() or "unknown"
        if "coinbase" in provider_name.lower():
            blocked["PRIMARY_ALREADY_COINBASE"].append(symbol)
            continue

        validation = _coinbase_reference_validation(symbol, quote.get("price"))
        record = _quote_verification_record(symbol, quote, validation)
        payload = dict(record.get("payload") or {})
        payload["evidence_kind"] = "independent_crypto_quote_consensus"
        payload["source"] = "v39_quote_readiness_sampler"
        payload["primary_provider"] = provider_name
        record["payload"] = payload
        evidence.append(record)
        primary_providers.add(provider_name)
        if validation.get("ok") is not True:
            blocked[str(validation.get("reason") or "COINBASE_REFERENCE_REJECTED")].append(symbol)

    persisted = _persist_quote_verifications(evidence)
    if evidence:
        worker.log.info(
            "CRYPTO | V39 QUOTE VERIFICATION EVIDENCE | persisted=%d | attempted=%d | sample_limit=%d | primary_providers=%s | secondary=Coinbase Exchange | broker_submission=NONE",
            persisted,
            len(evidence),
            limit,
            ",".join(sorted(primary_providers)) or "unknown",
        )

    for reason, affected in blocked.items():
        worker.log.info(
            "CRYPTO | V39 CONSENSUS EVIDENCE REJECTED | rejected=%d | reason=%s | sample=%s",
            len(affected),
            reason,
            ",".join(affected[:8]),
        )
    return persisted


def install_v39_quote_verification_sampler(worker: Any) -> None:
    """Wrap V39 iteration so readiness evidence exists before entry-only filtering."""
    if getattr(worker, "_crypto_v39_quote_verification_sampler_installed", False):
        return

    original = getattr(worker, "_v39_execute_iterative", None)
    if not callable(original):
        return

    def sampled_v39_execute_iterative(
        market: str,
        signals: list[Any],
        prices: dict[str, Any],
        ranked: list[dict[str, Any]],
        scan_type: str,
        *args: Any,
        **kwargs: Any,
    ) -> list[Any]:
        if str(market or "").lower() == "crypto":
            try:
                persist_v39_quote_verification_evidence(worker, signals, prices)
            except Exception as exc:
                # Paper execution remains best-effort, while capital readiness
                # stays fail-closed if evidence cannot be sampled or persisted.
                worker.log.warning(
                    "CRYPTO | V39 QUOTE VERIFICATION EVIDENCE UNAVAILABLE | error=%s",
                    exc.__class__.__name__,
                )
            try:
                shadow_result = maintain_passive_shadow_evidence(worker, signals, prices)
                if isinstance(shadow_result, dict):
                    _emit_shadow_status(worker, shadow_result)
            except Exception as exc:
                # Passive shadow sampling is read-only against Robinhood and
                # never changes the tactical signal or paper portfolio. Missing
                # evidence simply keeps capital readiness fail-closed.
                worker.log.warning(
                    "CRYPTO | PASSIVE SHADOW EVIDENCE UNAVAILABLE | error=%s",
                    exc.__class__.__name__,
                )
        return original(market, signals, prices, ranked, scan_type, *args, **kwargs)

    worker._v39_execute_iterative = sampled_v39_execute_iterative
    worker._crypto_v39_quote_verification_sampler_installed = True
