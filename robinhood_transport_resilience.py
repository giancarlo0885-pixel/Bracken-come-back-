from __future__ import annotations

import logging
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import robinhood_crypto_api as rh


log = logging.getLogger("robinhood-transport")

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_MARKETDATA_LOCK = threading.Lock()
_LAST_MARKETDATA_REQUEST = 0.0


def _safe_status(response: Any) -> int | None:
    try:
        return int(getattr(response, "status_code", None))
    except (TypeError, ValueError):
        return None


def _safe_symbols(path: str) -> str:
    """Return only normalized public pair symbols from a market-data query."""
    try:
        query = parse_qs(urlsplit(str(path or "")).query)
    except Exception:
        return ""
    symbols = []
    for raw in query.get("symbol", []):
        value = str(raw or "").upper().strip()
        if value and all(ch.isalnum() or ch in {"-", "_"} for ch in value):
            symbols.append(value)
    return ",".join(dict.fromkeys(symbols))


def _retry_after_seconds(response: Any, attempt: int) -> float:
    try:
        raw = getattr(response, "headers", {}).get("Retry-After")
        if raw not in (None, ""):
            return min(3.0, max(0.05, float(raw)))
    except (TypeError, ValueError, AttributeError):
        pass
    return min(2.0, 0.20 * (2 ** attempt))


def _pace_marketdata(path: str) -> None:
    global _LAST_MARKETDATA_REQUEST
    if "/api/v2/crypto/marketdata/" not in str(path or ""):
        return
    minimum_interval = 0.20
    with _MARKETDATA_LOCK:
        now = time.monotonic()
        wait = minimum_interval - (now - _LAST_MARKETDATA_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _LAST_MARKETDATA_REQUEST = time.monotonic()


def install_robinhood_transport_resilience() -> bool:
    """Add bounded retry/backoff and pacing to read-only Robinhood GET requests.

    Order-submission semantics are intentionally untouched. The wrapper retries
    GET requests only, recreates the signed timestamp/signature on every attempt,
    preserves fail-closed behavior after the bounded retries, and logs sanitized
    HTTP status plus public requested pair symbols without response bodies or
    credentials.
    """
    if getattr(rh, "_oracle_transport_resilience_installed", False):
        return False

    original_request = rh.RobinhoodCryptoClient.request

    def resilient_request(self: rh.RobinhoodCryptoClient, method: str, path: str, body: Any = None) -> Any:
        verb = str(method or "").upper().strip()
        if verb != "GET":
            return original_request(self, method, path, body)

        attempts = 4 if "/api/v2/crypto/marketdata/" in str(path or "") else 3
        last_exc: Exception | None = None
        symbols = _safe_symbols(path)

        for attempt in range(attempts):
            _pace_marketdata(path)
            try:
                return original_request(self, method, path, body)
            except Exception as exc:
                last_exc = exc
                cause = getattr(exc, "__cause__", None)
                response = getattr(cause, "response", None)
                status = _safe_status(response)
                retryable = status in _RETRYABLE_STATUS or status is None
                if not retryable or attempt >= attempts - 1:
                    log.warning(
                        "ROBINHOOD GET FAIL | endpoint=%s | symbols=%s | status=%s | attempts=%d | error=%s",
                        str(path or "").split("?", 1)[0],
                        symbols or "none",
                        status if status is not None else "transport",
                        attempt + 1,
                        exc.__class__.__name__,
                    )
                    raise
                delay = _retry_after_seconds(response, attempt)
                log.info(
                    "ROBINHOOD GET RETRY | endpoint=%s | symbols=%s | status=%s | attempt=%d/%d | delay=%.2fs",
                    str(path or "").split("?", 1)[0],
                    symbols or "none",
                    status if status is not None else "transport",
                    attempt + 1,
                    attempts,
                    delay,
                )
                time.sleep(delay)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Robinhood GET failed without an exception")

    rh.RobinhoodCryptoClient.request = resilient_request
    rh._oracle_transport_resilience_installed = True
    log.info(
        "ROBINHOOD TRANSPORT RESILIENCE | get_retry=ON | marketdata_pacing=0.20s | symbol_diagnostics=ON | live_order_submission=UNCHANGED"
    )
    return True
