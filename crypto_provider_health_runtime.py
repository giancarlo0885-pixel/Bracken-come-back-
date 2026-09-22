from __future__ import annotations

from collections import deque
import time
from typing import Any, Iterable


_WINDOW = 200


def _ratio_score(events: deque[tuple[float, int, int, int, int]], requested_index: int, good_index: int) -> float:
    requested = sum(item[requested_index] for item in events)
    good = sum(item[good_index] for item in events)
    if requested <= 0:
        return 100.0
    return round(max(0.0, min(100.0, good / requested * 100.0)), 2)


def _quality_quarantined_symbols(provider: Any) -> set[str]:
    return {
        str(symbol or "").upper().strip()
        for symbol in (getattr(provider, "_oracle_quality_quarantined_symbols", set()) or set())
        if str(symbol or "").strip()
    }


def install_crypto_provider_health_runtime(worker: Any) -> bool:
    """Track broker quote health without counting unsupported symbols as failures.

    Install after Robinhood quote resilience so availability reflects retries and
    paper cache grace. Symbols outside Robinhood's API-tradable set are coverage
    gaps, not provider failures. Symbols intentionally quality-quarantined after
    repeated invalid books are tracked separately as data-quality events.
    """
    if getattr(worker, "_crypto_provider_health_runtime_installed", False):
        return False

    provider = getattr(worker, "_robinhood_current_marketdata_provider", None)
    if provider is None:
        return False

    original_snapshots = provider.snapshots
    events: deque[tuple[float, int, int, int, int]] = deque(maxlen=_WINDOW)

    def observed_snapshots(symbols: Iterable[str]):
        requested = list(
            dict.fromkeys(
                str(symbol or "").upper().strip()
                for symbol in symbols
                if str(symbol or "").strip()
            )
        )
        try:
            supported = {
                str(symbol or "").upper().strip()
                for symbol in (provider.tradable_symbols() or set())
                if str(symbol or "").strip()
            }
        except Exception:
            supported = set(requested)

        coverage_gaps = [symbol for symbol in requested if symbol not in supported]

        # The resilience wrapper can place a symbol into quality quarantine while
        # resolving this very snapshots() call. Read quarantine state only after
        # that call so telemetry reflects the final post-retry provider state.
        result = dict(original_snapshots(requested) or {})
        quality_quarantined = _quality_quarantined_symbols(provider)
        health_eligible = [
            symbol for symbol in requested
            if symbol in supported and symbol not in quality_quarantined
        ]
        resolved = sum(1 for symbol in health_eligible if symbol in result)
        quality_eligible = [symbol for symbol in requested if symbol in supported]
        quality_good = [symbol for symbol in quality_eligible if symbol not in quality_quarantined]
        events.append((
            time.monotonic(),
            len(health_eligible),
            resolved,
            len(quality_eligible),
            len(quality_good),
        ))

        availability_score = _ratio_score(events, 1, 2)
        data_quality_score = _ratio_score(events, 3, 4)
        score = min(availability_score, data_quality_score)
        unresolved = [symbol for symbol in health_eligible if symbol not in result]
        quarantined_requested = [symbol for symbol in requested if symbol in quality_quarantined]
        worker._crypto_provider_health = {
            "primary_provider": "Robinhood Crypto",
            "quote_health_score": score,
            "availability_score": availability_score,
            "data_quality_score": data_quality_score,
            "window_calls": len(events),
            "requested": sum(item[1] for item in events),
            "resolved": sum(item[2] for item in events),
            "last_requested": len(health_eligible),
            "last_resolved": resolved,
            "last_unresolved": unresolved[:12],
            "last_coverage_gaps": coverage_gaps[:12],
            "last_quality_quarantined": quarantined_requested[:12],
        }

        if unresolved or coverage_gaps or quarantined_requested or len(events) in {1, 10, 25, 50, 100, 200}:
            worker.log.info(
                "CRYPTO_PROVIDER_HEALTH | provider=Robinhood Crypto | score=%.2f | availability=%.2f | quality=%.2f | "
                "eligible_requested=%d | resolved=%d | unresolved=%s | coverage_gaps=%s | quality_quarantined=%s | window_calls=%d",
                score,
                availability_score,
                data_quality_score,
                len(health_eligible),
                resolved,
                ",".join(unresolved[:8]) or "none",
                ",".join(coverage_gaps[:8]) or "none",
                ",".join(quarantined_requested[:8]) or "none",
                len(events),
            )
        return result

    provider.snapshots = observed_snapshots
    worker._crypto_provider_health_runtime_installed = True
    worker.log.info(
        "CRYPTO_PROVIDER_HEALTH | installed=ON | primary=Robinhood Crypto | coverage_gap_penalty=OFF | "
        "quality_penalty=ON | final_post_retry_observation=ON | execution_behavior=UNCHANGED"
    )
    return True
