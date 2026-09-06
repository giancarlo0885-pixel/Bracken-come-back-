from __future__ import annotations

from collections import deque
import time
from typing import Any, Iterable


_WINDOW = 200


def _health_score(events: deque[tuple[float, int, int]]) -> float:
    requested = sum(item[1] for item in events)
    resolved = sum(item[2] for item in events)
    if requested <= 0:
        return 100.0
    return round(max(0.0, min(100.0, resolved / requested * 100.0)), 2)


def install_crypto_provider_health_runtime(worker: Any) -> bool:
    """Track broker quote availability without changing execution behavior.

    Install this after Robinhood quote resilience so the health score reflects the
    final broker result after retries/cache grace. The wrapper only records counts
    and exposes them on the worker for observability. It never fabricates a quote,
    changes quote contents, or relaxes any execution gate.
    """
    if getattr(worker, "_crypto_provider_health_runtime_installed", False):
        return False

    provider = getattr(worker, "_robinhood_current_marketdata_provider", None)
    if provider is None:
        return False

    original_snapshots = provider.snapshots
    events: deque[tuple[float, int, int]] = deque(maxlen=_WINDOW)

    def observed_snapshots(symbols: Iterable[str]):
        requested = list(
            dict.fromkeys(
                str(symbol or "").upper().strip()
                for symbol in symbols
                if str(symbol or "").strip()
            )
        )
        result = dict(original_snapshots(requested) or {})
        resolved = sum(1 for symbol in requested if symbol in result)
        events.append((time.monotonic(), len(requested), resolved))

        score = _health_score(events)
        unresolved = [symbol for symbol in requested if symbol not in result]
        worker._crypto_provider_health = {
            "primary_provider": "Robinhood Crypto",
            "quote_health_score": score,
            "window_calls": len(events),
            "requested": sum(item[1] for item in events),
            "resolved": sum(item[2] for item in events),
            "last_requested": len(requested),
            "last_resolved": resolved,
            "last_unresolved": unresolved[:12],
        }

        if unresolved or len(events) in {1, 10, 25, 50, 100, 200}:
            worker.log.info(
                "CRYPTO_PROVIDER_HEALTH | provider=Robinhood Crypto | score=%.2f | last_requested=%d | last_resolved=%d | unresolved=%s | window_calls=%d",
                score,
                len(requested),
                resolved,
                ",".join(unresolved[:8]) or "none",
                len(events),
            )
        return result

    provider.snapshots = observed_snapshots
    worker._crypto_provider_health_runtime_installed = True
    worker.log.info(
        "CRYPTO_PROVIDER_HEALTH | installed=ON | primary=Robinhood Crypto | final_post_retry_observation=ON | execution_behavior=UNCHANGED"
    )
    return True
