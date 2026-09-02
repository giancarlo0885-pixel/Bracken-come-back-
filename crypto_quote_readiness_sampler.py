from __future__ import annotations

from collections import defaultdict
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


def persist_v39_quote_verification_evidence(
    worker: Any,
    signals: Any,
    prices: dict[str, Any] | None,
    *,
    max_samples: int | None = None,
) -> int:
    """Persist bounded Yahoo/Coinbase evidence even when V39 keeps signals on HOLD.

    The execution guard still performs the authoritative per-entry consensus gate.
    This sampler only prevents capital-readiness evidence from depending on an
    entry action existing. It never changes a signal action, optimizer allocation,
    quote, or execution decision.
    """
    quote_map = prices or {}
    limit = _sample_limit() if max_samples is None else max(1, min(12, int(max_samples)))
    evidence: list[dict[str, Any]] = []
    blocked: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()

    for signal in list(signals or []):
        if len(evidence) >= limit:
            break
        symbol = _symbol(signal)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)

        quote = oracle_bot._verified_quote_for(symbol, quote_map, "crypto")
        if quote is None:
            continue
        if str(quote.get("provider") or "").strip().lower() != "yahoo finance":
            continue
        if not _paper_yahoo_reference(quote):
            blocked["YAHOO_REFERENCE_NOT_EXECUTION_ELIGIBLE"].append(symbol)
            continue

        validation = _coinbase_reference_validation(symbol, quote.get("price"))
        evidence.append(_quote_verification_record(symbol, quote, validation))
        if validation.get("ok") is not True:
            blocked[str(validation.get("reason") or "COINBASE_REFERENCE_REJECTED")].append(symbol)

    persisted = _persist_quote_verifications(evidence)
    if evidence:
        worker.log.info(
            "CRYPTO | V39 QUOTE VERIFICATION EVIDENCE | persisted=%d | attempted=%d | sample_limit=%d",
            persisted,
            len(evidence),
            limit,
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
