from __future__ import annotations

import logging
import time
import uuid
from typing import Any

log = logging.getLogger("stock-execution-repair")

_CACHE_TTL_SECONDS = 8.0
_snapshot_cache: dict[str, tuple[float, Any | None]] = {}


def _symbol(value: Any) -> str:
    return str(value or "").upper().strip()


def _verified_live_snapshot(symbol: str) -> Any | None:
    symbol = _symbol(symbol)
    if not symbol or symbol.endswith("-USD"):
        return None

    now = time.monotonic()
    cached = _snapshot_cache.get(symbol)
    if cached and now - cached[0] <= _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        from market_data import get_live_snapshot, snapshot_is_verified

        snapshot = get_live_snapshot(symbol)
        if snapshot is None or not snapshot_is_verified(snapshot, symbol):
            snapshot = None
    except Exception as exc:
        log.debug("Verified stock quote refresh failed | symbol=%s | error=%s", symbol, exc)
        snapshot = None

    _snapshot_cache[symbol] = (now, snapshot)
    return snapshot


def _verified_payload(worker: Any, symbol: str, history: Any, scan_type: str) -> dict[str, Any] | None:
    snapshot = _verified_live_snapshot(symbol)
    if snapshot is None:
        return None

    payload = dict(snapshot.to_quote_payload())
    quote_eligible = payload.get("quote_verified") is True
    paper_reference_verified = payload.get("paper_reference_verified") is True
    if "provider_quote_verified" in payload:
        provider_verified = payload.get("provider_quote_verified") is True
    else:
        provider_verified = bool(quote_eligible and not paper_reference_verified)

    correlation_id = str(
        payload.get("correlation_id")
        or payload.get("decision_correlation_id")
        or uuid.uuid4()
    ).strip()

    payload.update(
        {
            "symbol": _symbol(symbol),
            "market": "cash",
            "scan_type": scan_type,
            "source_interval": payload.get("interval"),
            "quote_verified": True,
            "verified": True,
            "execution_quote_eligible": True,
            "provider_quote_verified": provider_verified,
            "paper_reference_verified": paper_reference_verified,
            "verification_kind": (
                "provider"
                if provider_verified
                else "paper_reference"
                if paper_reference_verified
                else "unverified"
            ),
            "stale": False,
            "tradeable": True,
            "execution_source": "verified_live_snapshot",
            "correlation_id": correlation_id,
            "decision_correlation_id": correlation_id,
        }
    )

    try:
        avg_dollar_volume = worker._average_dollar_volume(history)
    except Exception:
        avg_dollar_volume = None
    if avg_dollar_volume is not None:
        payload["avg_dollar_volume"] = avg_dollar_volume
        payload["average_dollar_volume"] = avg_dollar_volume
        payload["liquidity_value"] = avg_dollar_volume

    return payload


def install_stock_execution_quote_repair(worker: Any) -> None:
    """Separate stock research history from execution pricing.

    Daily history remains the analysis/forecast source. A fresh eligible intraday
    snapshot is independently required for paper execution. Provider verification
    and paper-reference verification remain explicitly distinguished in payloads
    and logs. Missing execution quotes are filtered before ``process_signals`` so
    they are never represented as a synthetic $0.00 candidate.
    """
    if getattr(worker, "_stock_execution_quote_repair_installed", False):
        return

    import oracle_bot

    original_quote_builder = worker._execution_quote_payload_from_history
    original_process_signals = worker.process_signals
    original_forecast_gate = oracle_bot._entry_forecast_gate

    def repaired_quote_builder(
        symbol: str,
        history: Any,
        price: Any = None,
        *,
        scan_type: str = "",
    ) -> dict[str, Any] | None:
        normalized = _symbol(symbol)
        if normalized.endswith("-USD"):
            return original_quote_builder(symbol, history, price, scan_type=scan_type)

        live_payload = _verified_payload(worker, normalized, history, scan_type)
        if live_payload is not None:
            worker.log.info(
                "EXECUTION_QUOTE_HANDOFF | symbol=%s | market=cash | price=%s | bid=%s | ask=%s | "
                "timestamp=%s | provider=%s | verified=%s | quote_eligible=%s | provider_verified=%s | "
                "paper_reference_verified=%s | verification_kind=%s | stale=%s | spread_pct=%s | "
                "capability=%s | correlation_id=%s",
                normalized,
                live_payload.get("price"),
                live_payload.get("bid"),
                live_payload.get("ask"),
                live_payload.get("quote_timestamp") or live_payload.get("timestamp"),
                live_payload.get("provider"),
                live_payload.get("verified"),
                live_payload.get("execution_quote_eligible"),
                live_payload.get("provider_quote_verified"),
                live_payload.get("paper_reference_verified"),
                live_payload.get("verification_kind"),
                live_payload.get("stale"),
                live_payload.get("spread_pct"),
                live_payload.get("source_capability"),
                live_payload.get("correlation_id"),
            )
            return live_payload

        # Keep the research payload available to the ranking/persistence path,
        # but never upgrade it to execution-grade data.
        return original_quote_builder(symbol, history, price, scan_type=scan_type)

    def repaired_process_signals(
        market: str,
        signals: Any,
        prices: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        if str(market or "").lower() != "cash":
            return original_process_signals(market, signals, prices, *args, **kwargs)

        prices = prices or {}
        executable: list[Any] = []
        skipped: list[str] = []
        for signal in list(signals or []):
            symbol = _symbol(getattr(signal, "symbol", None) if not isinstance(signal, dict) else signal.get("symbol"))
            if not symbol:
                continue
            quote = oracle_bot._verified_quote_for(symbol, prices, "cash")
            if quote is None:
                skipped.append(symbol)
                continue
            executable.append(signal)

        if skipped:
            worker.log.info(
                "CASH | EXECUTION SKIP | no verified live quote | affected_symbols=%d | sample=%s",
                len(skipped),
                ",".join(skipped[:8]),
            )

        return original_process_signals(market, executable, prices, *args, **kwargs)

    def repaired_forecast_gate(
        market: str,
        symbol: str,
        price: float,
        signal: Any | None = None,
        quote: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        if str(market or "").lower() != "cash" or not quote or quote.get("quote_verified") is not True:
            return original_forecast_gate(market, symbol, price, signal, quote)

        # Forecast provenance belongs to the analysis history. Execution quote
        # provenance belongs to the independent live quote. Let the original
        # gate validate the forecast against the analysis metadata while the
        # execution guard separately validates the real execution quote.
        route = oracle_bot._signal_route(signal)
        analysis_interval = (
            oracle_bot.signal_value(signal, "source_interval", "")
            or route.get("interval")
            or route.get("source_interval")
        )
        analysis_timestamp = (
            oracle_bot.signal_value(signal, "source_quote_timestamp", "")
            or oracle_bot.signal_value(signal, "quote_timestamp", "")
            or route.get("quote_timestamp")
            or route.get("timestamp")
        )
        if not analysis_interval or not analysis_timestamp:
            return original_forecast_gate(market, symbol, price, signal, quote)

        forecast_validation_quote = dict(quote)
        forecast_validation_quote["interval"] = analysis_interval
        forecast_validation_quote["quote_timestamp"] = analysis_timestamp
        forecast_validation_quote["timestamp"] = analysis_timestamp
        return original_forecast_gate(
            market,
            symbol,
            price,
            signal,
            forecast_validation_quote,
        )

    worker._execution_quote_payload_from_history = repaired_quote_builder
    worker.process_signals = repaired_process_signals
    oracle_bot._entry_forecast_gate = repaired_forecast_gate
    worker._stock_execution_quote_repair_installed = True
    log.info("Installed stock execution quote handoff repair")
