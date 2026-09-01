from __future__ import annotations

from typing import Any

from crypto_execution_guard import _coinbase_reference_validation


def _finite_nonnegative(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return None
    return number


def install_crypto_v39_spread_bridge(worker: Any) -> None:
    """Supply V39 with independently verified crypto spread evidence.

    Yahoo paper-reference quotes do not carry a native bid/ask book, so their
    spread is legitimately unknown. V39 correctly fails closed when spread is
    unknown. This bridge does not weaken that gate: it asks the already-required
    Coinbase consensus guard for an independent live exchange spread and only
    attaches that measured spread when Coinbase confirms freshness, spread, and
    price-consensus limits. Failed or unavailable consensus leaves spread unknown,
    so V39 remains unable to qualify the candidate for capital.
    """
    original = worker._execution_quote_payload_from_history
    if getattr(original, "_oracle_coinbase_spread_bridge", False):
        return

    def execution_quote_payload_from_history(
        symbol: str,
        history: Any,
        price: Any = None,
        *,
        scan_type: str = "",
    ) -> dict[str, Any] | None:
        payload = original(symbol, history, price, scan_type=scan_type)
        if payload is None:
            return None

        if str(payload.get("market") or "").strip().lower() != "crypto":
            return payload
        if str(payload.get("provider") or "").strip().lower() != "yahoo finance":
            return payload
        if payload.get("paper_reference_verified") is not True:
            return payload
        if _finite_nonnegative(payload.get("spread_pct")) is not None:
            return payload

        validation = _coinbase_reference_validation(symbol, payload.get("price"))
        if validation.get("ok") is not True:
            worker.log.info(
                "CRYPTO | V39 SPREAD EVIDENCE BLOCKED | symbol=%s | reason=%s",
                str(symbol or "").upper(),
                str(validation.get("reason") or "COINBASE_REFERENCE_REJECTED"),
            )
            return payload

        spread_pct = _finite_nonnegative(validation.get("spread_pct"))
        if spread_pct is None:
            return payload

        payload["spread_pct"] = spread_pct
        payload["spread_known"] = True
        payload["spread_provider"] = str(validation.get("reference_provider") or "Coinbase Exchange")
        payload["spread_reference_timestamp"] = validation.get("reference_timestamp")
        payload["price_consensus_verified"] = True
        payload["reference_provider"] = validation.get("reference_provider")
        payload["reference_price"] = validation.get("reference_price")
        payload["reference_timestamp"] = validation.get("reference_timestamp")
        payload["reference_difference_pct"] = validation.get("difference_pct")
        worker.log.info(
            "CRYPTO | V39 SPREAD EVIDENCE | symbol=%s | spread_pct=%.6f | provider=%s | consensus=PASS",
            str(symbol or "").upper(),
            spread_pct,
            payload["spread_provider"],
        )
        return payload

    execution_quote_payload_from_history._oracle_coinbase_spread_bridge = True
    worker._execution_quote_payload_from_history = execution_quote_payload_from_history
