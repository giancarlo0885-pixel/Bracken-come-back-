from __future__ import annotations

from collections import defaultdict
import os
from typing import Any

import oracle_bot
from crypto_execution_guard import (
    _coinbase_reference_validation,
    _paper_yahoo_reference,
    _persist_quote_verifications,
    _quote_verification_record,
    _symbol,
)


def _sample_limit() -> int:
    try:
        return max(1, min(12, int(os.getenv("V39_QUOTE_VERIFICATION_SAMPLE_SIZE", "6"))))
    except ValueError:
        return 6


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
        return original(market, signals, prices, ranked, scan_type, *args, **kwargs)

    worker._v39_execute_iterative = sampled_v39_execute_iterative
    worker._crypto_v39_quote_verification_sampler_installed = True
