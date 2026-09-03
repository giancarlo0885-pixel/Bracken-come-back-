from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import os
import threading
import time
from typing import Any, Iterable

from asset_routing import infer_asset_class
from market_data import MarketSnapshot
from robinhood_crypto_api import RobinhoodCryptoClient, best_bid_ask


log = logging.getLogger("robinhood-current-marketdata")


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _crypto_symbol(symbol: str) -> bool:
    normalized = str(symbol or "").upper().strip()
    return bool(normalized and infer_asset_class(normalized) == "crypto")


def robinhood_recoverable_symbols(
    quarantined: Iterable[str],
    robinhood_tradable: Iterable[str],
    configured_watchlist: Iterable[str],
) -> set[str]:
    """Return legacy-quarantined crypto seeds Robinhood now proves are valid."""
    blocked = {str(symbol or "").upper().strip() for symbol in quarantined if str(symbol or "").strip()}
    supported = {str(symbol or "").upper().strip() for symbol in robinhood_tradable if str(symbol or "").strip()}
    configured = {str(symbol or "").upper().strip() for symbol in configured_watchlist if str(symbol or "").strip()}
    return blocked & supported & configured


def snapshot_from_robinhood_quote(
    symbol: str,
    quote: dict[str, Any],
    *,
    fetched_at: str | None = None,
) -> MarketSnapshot | None:
    """Convert Robinhood's authenticated best bid/ask into execution market data.

    Robinhood's v2 best-bid/ask response does not expose an exchange-event
    timestamp. The snapshot timestamp is therefore the authenticated provider
    read time, and the verification basis records that distinction explicitly.
    """
    requested = str(symbol or "").upper().strip()
    quote_symbol = str(quote.get("symbol") or "").upper().strip()
    if not requested or quote_symbol != requested or not _crypto_symbol(requested):
        return None

    book = best_bid_ask(quote)
    if book is None:
        return None

    read_time = fetched_at or datetime.now(timezone.utc).isoformat()
    bid = float(book["bid"])
    ask = float(book["ask"])
    mid = float(book["mid"])
    spread_pct = float(book["spread_pct"])
    if not all(math.isfinite(value) and value > 0 for value in (bid, ask, mid)):
        return None

    return MarketSnapshot(
        symbol=requested,
        price=mid,
        change_pct=0.0,
        volume=0.0,
        timestamp=read_time,
        bid=bid,
        ask=ask,
        provider="Robinhood Crypto",
        interval="1m",
        fetched_at=read_time,
        requested_symbol=requested,
        provider_symbol=requested,
        provider_native_symbol=requested,
        quote_verified=True,
        stale=False,
        spread_pct=spread_pct,
        source_capability="best_bid_ask_realtime",
        correlation_id=None,
        source_identity=f"Robinhood Crypto:{requested}:best_bid_ask",
        cache_identity=f"robinhood_crypto_best_bid_ask:{requested}",
        ohlcv_fingerprint=None,
        provider_quote_verified=True,
        paper_reference_verified=False,
        verification_basis="provider:robinhood_crypto_best_bid_ask_read_time",
    )


def overlay_execution_payload(payload: dict[str, Any], snapshot: MarketSnapshot) -> dict[str, Any]:
    """Replace only the point-in-time execution mark; preserve research provenance."""
    data = dict(payload or {})
    symbol = str(data.get("symbol") or snapshot.symbol or "").upper().strip()
    if symbol != str(snapshot.symbol or "").upper().strip():
        return data

    provider_support = [
        str(item)
        for item in (data.get("provider_support") or [])
        if str(item or "").strip()
    ]
    if "Robinhood Crypto" not in provider_support:
        provider_support.append("Robinhood Crypto")

    data.update(
        {
            "analysis_price": data.get("price"),
            "analysis_provider": data.get("provider"),
            "analysis_quote_timestamp": data.get("quote_timestamp") or data.get("timestamp"),
            "analysis_source_interval": data.get("source_interval") or data.get("interval"),
            "analysis_verification_basis": data.get("verification_basis"),
            "price": snapshot.price,
            "bid": snapshot.bid,
            "ask": snapshot.ask,
            "quote_timestamp": snapshot.timestamp,
            "timestamp": snapshot.timestamp,
            "quote_age_seconds": 0.0,
            "interval": snapshot.interval,
            "source_interval": snapshot.interval,
            "provider": snapshot.provider,
            "requested_symbol": snapshot.requested_symbol,
            "provider_symbol": snapshot.provider_symbol,
            "provider_native_symbol": snapshot.provider_native_symbol,
            "quote_verified": True,
            "verified": True,
            "stale": False,
            "spread_pct": snapshot.spread_pct,
            "spread_known": snapshot.spread_pct is not None,
            "source_mode": "broker_current_quote",
            "source_capability": snapshot.source_capability,
            "source_identity": snapshot.source_identity,
            "cache_identity": snapshot.cache_identity,
            "provider_quote_verified": True,
            "paper_reference_verified": False,
            "verification_basis": snapshot.verification_basis,
            "provider_read_timestamp": snapshot.fetched_at,
            "provider_support": provider_support,
            "current_data_provider": "Robinhood Crypto",
            "current_data_verified": True,
        }
    )
    return data


class RobinhoodCurrentData:
    def __init__(self, client: RobinhoodCryptoClient | None = None) -> None:
        self.client = client or RobinhoodCryptoClient()
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, MarketSnapshot]] = {}
        self._tradable_symbols: set[str] | None = None

    @staticmethod
    def _ttl_seconds() -> float:
        try:
            value = float(os.getenv("ROBINHOOD_CRYPTO_MARKETDATA_TTL_SECONDS", "3"))
        except ValueError:
            value = 3.0
        return min(30.0, max(1.0, value))

    def tradable_symbols(self) -> set[str]:
        if self._tradable_symbols is not None:
            return set(self._tradable_symbols)
        try:
            pairs = self.client.trading_pairs()
        except Exception as exc:
            log.warning("Robinhood trading-pair discovery failed | error=%s", exc.__class__.__name__)
            return set()
        supported = {
            str(pair.get("symbol") or "").upper().strip()
            for pair in pairs or []
            if isinstance(pair, dict)
            and pair.get("tradable") is True
            and str(pair.get("symbol") or "").strip()
        }
        self._tradable_symbols = supported
        return set(supported)

    def _cached(self, symbol: str, now: float) -> MarketSnapshot | None:
        cached = self._cache.get(symbol)
        if not cached:
            return None
        inserted_at, snapshot = cached
        if now - inserted_at > self._ttl_seconds():
            return None
        return snapshot

    def snapshots(self, symbols: Iterable[str]) -> dict[str, MarketSnapshot]:
        normalized = list(
            dict.fromkeys(
                str(symbol or "").upper().strip()
                for symbol in symbols
                if _crypto_symbol(str(symbol or ""))
            )
        )
        if not normalized:
            return {}

        now = time.monotonic()
        results: dict[str, MarketSnapshot] = {}
        missing: list[str] = []
        with self._lock:
            for symbol in normalized:
                cached = self._cached(symbol, now)
                if cached is not None:
                    results[symbol] = cached
                else:
                    missing.append(symbol)

        if not missing:
            return results

        configured = self.client.configured()
        if configured.get("ok") is not True:
            log.warning("Robinhood current-data provider unavailable | reason=%s", configured.get("reason"))
            return results

        try:
            records = self.client.best_bid_ask_quotes(*missing)
        except Exception as exc:
            log.warning("Robinhood current-data request failed | symbols=%d | error=%s", len(missing), exc.__class__.__name__)
            return results

        read_time = datetime.now(timezone.utc).isoformat()
        by_symbol = {
            str(item.get("symbol") or "").upper().strip(): item
            for item in records or []
            if isinstance(item, dict) and str(item.get("symbol") or "").strip()
        }
        with self._lock:
            inserted_at = time.monotonic()
            for symbol in missing:
                quote = by_symbol.get(symbol)
                if quote is None:
                    continue
                snapshot = snapshot_from_robinhood_quote(symbol, quote, fetched_at=read_time)
                if snapshot is None:
                    continue
                self._cache[symbol] = (inserted_at, snapshot)
                results[symbol] = snapshot
        return results

    def snapshot(self, symbol: str) -> MarketSnapshot | None:
        return self.snapshots([symbol]).get(str(symbol or "").upper().strip())


def install_robinhood_current_marketdata(worker: Any) -> bool:
    """Make Robinhood the primary current-data source for crypto execution.

    Historical OHLCV remains sourced from the existing provider router because
    Robinhood's Crypto Trading API exposes point-in-time market data, not the
    historical bars required by technical models. Crypto execution marks,
    opportunity ranking marks, and live position pulses use Robinhood best
    bid/ask and fail closed when an authenticated broker quote is unavailable.

    A legacy provider quarantine is not allowed to suppress a configured crypto
    seed when Robinhood itself currently reports that exact USD pair as API
    tradable. The quarantine row is retained for audit; only the runtime block is
    bypassed for that broker-verified configured symbol.
    """
    if os.getenv("ROBINHOOD_CRYPTO_ENABLED", "false").strip().lower() != "true":
        return False
    if getattr(worker, "_robinhood_current_marketdata_installed", False):
        return False

    import market_data

    provider = RobinhoodCurrentData()
    if provider.client.configured().get("ok") is not True:
        log.warning("Robinhood current-data bridge not installed: credentials unavailable")
        return False

    robinhood_tradable = provider.tradable_symbols()
    configured_crypto = set(getattr(worker, "WATCHLISTS", {}).get("crypto", {}).keys())
    if not robinhood_tradable:
        log.warning("Robinhood current-data bridge not installed: no API-tradable crypto pairs returned")
        return False

    original_live_snapshot = market_data.get_live_snapshot
    original_many_snapshots = market_data.get_many_snapshots
    original_execution_quote = worker._execution_quote_payload_from_history
    original_active_quarantine = worker._active_quarantined_symbols

    def active_quarantined_symbols() -> set[str]:
        quarantined = set(original_active_quarantine() or set())
        recoverable = robinhood_recoverable_symbols(quarantined, robinhood_tradable, configured_crypto)
        return quarantined - recoverable

    def live_snapshot(symbol: str) -> MarketSnapshot | None:
        if not _crypto_symbol(symbol):
            return original_live_snapshot(symbol)
        return provider.snapshot(symbol)

    def many_snapshots(symbols: Iterable[str], live: bool = False) -> dict[str, MarketSnapshot]:
        symbol_list = list(dict.fromkeys(str(symbol or "").upper().strip() for symbol in symbols if symbol))
        if not live:
            return original_many_snapshots(symbol_list, live=False)

        crypto_symbols = [symbol for symbol in symbol_list if _crypto_symbol(symbol)]
        other_symbols = [symbol for symbol in symbol_list if symbol not in crypto_symbols]
        results: dict[str, MarketSnapshot] = {}
        if other_symbols:
            results.update(original_many_snapshots(other_symbols, live=True))
        if crypto_symbols:
            results.update(provider.snapshots(crypto_symbols))
        return results

    def execution_quote_payload_from_history(
        symbol: str,
        history: Any,
        price: Any = None,
        *,
        scan_type: str = "",
    ) -> dict[str, Any] | None:
        payload = original_execution_quote(symbol, history, price, scan_type=scan_type)
        if payload is None or not _crypto_symbol(symbol):
            return payload

        snapshot = provider.snapshot(symbol)
        if snapshot is None:
            worker.log.info(
                "CRYPTO | ROBINHOOD CURRENT DATA BLOCK | symbol=%s | reason=BROKER_QUOTE_UNAVAILABLE",
                str(symbol or "").upper(),
            )
            return None

        enriched = overlay_execution_payload(payload, snapshot)
        current_price = _finite_positive(enriched.get("price"))
        if current_price is None:
            return None
        worker.log.info(
            "CRYPTO | ROBINHOOD CURRENT DATA | symbol=%s | bid=%.8f | ask=%.8f | mid=%.8f | spread_pct=%.6f",
            str(symbol or "").upper(),
            float(snapshot.bid or 0.0),
            float(snapshot.ask or 0.0),
            current_price,
            float(snapshot.spread_pct or 0.0),
        )
        return enriched

    recovered_now: set[str] = set()
    try:
        current_quarantine = set(original_active_quarantine() or set())
        recovered_now = robinhood_recoverable_symbols(current_quarantine, robinhood_tradable, configured_crypto)
    except Exception:
        recovered_now = set()

    market_data.get_live_snapshot = live_snapshot
    market_data.get_many_snapshots = many_snapshots
    worker.get_many_snapshots = many_snapshots
    worker._active_quarantined_symbols = active_quarantined_symbols
    worker._execution_quote_payload_from_history = execution_quote_payload_from_history
    worker._robinhood_current_marketdata_installed = True
    worker._robinhood_current_marketdata_provider = provider
    log.info(
        "Installed Robinhood Crypto primary current-data bridge | endpoint=best_bid_ask | api_tradable_pairs=%d | configured_pairs=%d | legacy_quarantine_recovered=%d | broker_submission=NONE",
        len(robinhood_tradable),
        len(configured_crypto),
        len(recovered_now),
    )
    return True
