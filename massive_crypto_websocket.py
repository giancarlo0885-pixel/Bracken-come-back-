from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
import os
import statistics
import threading
import time
from typing import Any, Iterable

import websocket

from asset_routing import infer_asset_class


log = logging.getLogger("massive-crypto-ws")

DEFAULT_URL = "wss://socket.massive.com/crypto"
ALLOWED_CHANNELS = ("XQ", "XT", "XAS")


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _pair(value: Any) -> str:
    return str(value or "").strip().upper().replace("/", "-")


def _timestamp_iso(unix_ms: int | float) -> str:
    return datetime.fromtimestamp(float(unix_ms) / 1000.0, tz=timezone.utc).isoformat()


def _channel_names(raw: str | None) -> tuple[str, ...]:
    requested = [item.strip().upper() for item in str(raw or "XQ,XT,XAS").split(",") if item.strip()]
    return tuple(channel for channel in ALLOWED_CHANNELS if channel in requested) or ("XQ",)


def build_subscriptions(
    symbols: Iterable[str],
    *,
    scope: str = "watchlist",
    channels: Iterable[str] = ALLOWED_CHANNELS,
) -> tuple[str, ...]:
    normalized_channels = tuple(channel for channel in (str(item).upper().strip() for item in channels) if channel in ALLOWED_CHANNELS)
    normalized_channels = normalized_channels or ("XQ",)
    if str(scope or "watchlist").strip().lower() == "all":
        return tuple(f"{channel}.*" for channel in normalized_channels)

    pairs = tuple(dict.fromkeys(_pair(symbol) for symbol in symbols if _pair(symbol)))
    return tuple(f"{channel}.{symbol}" for channel in normalized_channels for symbol in pairs)


@dataclass(frozen=True)
class MassiveReference:
    symbol: str
    price: float
    bid: float | None
    ask: float | None
    timestamp: str
    age_seconds: float
    exchange_count: int
    event_type: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "requested_symbol": self.symbol,
            "provider_symbol": self.symbol,
            "provider_native_symbol": self.symbol,
            "provider": "Massive Crypto WebSocket",
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "quote_timestamp": self.timestamp,
            "timestamp": self.timestamp,
            "interval": "1s",
            "quote_verified": True,
            "verified": True,
            "stale": False,
            "source_capability": f"websocket_crypto_{self.event_type.lower()}",
            "currency": self.symbol.rsplit("-", 1)[-1] if "-" in self.symbol else "",
            "exchange_count": self.exchange_count,
            "age_seconds": self.age_seconds,
            "event_type": self.event_type,
            "cache_identity": f"massive_ws:{self.symbol}:{self.event_type}",
            "verification_basis": "provider:massive_websocket",
        }


class MassiveCryptoStream:
    """Persistent Massive crypto WebSocket client with a bounded in-memory market cache.

    The stream is surveillance/reference data only. It never submits broker orders and
    never replaces the broker-anchored execution quote path.
    """

    def __init__(
        self,
        api_key: str,
        subscriptions: Iterable[str],
        *,
        url: str = DEFAULT_URL,
        max_age_seconds: float = 10.0,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.subscriptions = tuple(dict.fromkeys(str(item).strip() for item in subscriptions if str(item).strip()))
        self.url = str(url or DEFAULT_URL).strip()
        self.max_age_seconds = max(1.0, float(max_age_seconds))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._app: websocket.WebSocketApp | None = None
        self._authenticated = False
        self._connected = False
        self._quotes: dict[str, dict[int, dict[str, Any]]] = {}
        self._trades: dict[str, dict[str, Any]] = {}
        self._aggregates: dict[str, dict[str, Any]] = {}
        self._counts: Counter[str] = Counter()
        self._last_health_log = 0.0
        self._last_consensus_log: dict[str, float] = {}

    @property
    def authenticated(self) -> bool:
        with self._lock:
            return self._authenticated

    def start(self) -> bool:
        if not self.api_key or not self.subscriptions:
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="massive-crypto-ws", daemon=True)
            self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        app = self._app
        if app is not None:
            try:
                app.close()
            except Exception:
                pass

    def ingest_payload(self, payload: Any) -> None:
        messages = payload if isinstance(payload, list) else [payload]
        for item in messages:
            if not isinstance(item, dict):
                continue
            event_type = str(item.get("ev") or "").upper().strip()
            if event_type == "XQ":
                self._ingest_quote(item)
            elif event_type == "XT":
                self._ingest_trade(item)
            elif event_type == "XAS":
                self._ingest_aggregate(item)

    def reference(self, symbol: str, *, now_ms: int | None = None) -> dict[str, Any] | None:
        requested = _pair(symbol)
        current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        max_age_ms = int(self.max_age_seconds * 1000)

        with self._lock:
            quotes = list((self._quotes.get(requested) or {}).values())
            aggregate = dict(self._aggregates.get(requested) or {})
            trade = dict(self._trades.get(requested) or {})

        fresh_quotes = [
            item
            for item in quotes
            if current_ms - int(item.get("t") or 0) <= max_age_ms
            and _finite_positive(item.get("bp")) is not None
            and _finite_positive(item.get("ap")) is not None
        ]
        if fresh_quotes:
            mids: list[float] = []
            bids: list[float] = []
            asks: list[float] = []
            newest = 0
            for item in fresh_quotes:
                bid = _finite_positive(item.get("bp"))
                ask = _finite_positive(item.get("ap"))
                if bid is None or ask is None or ask < bid:
                    continue
                bids.append(bid)
                asks.append(ask)
                mids.append((bid + ask) / 2.0)
                newest = max(newest, int(item.get("t") or 0))
            if mids and newest > 0:
                bid = float(statistics.median(bids)) if bids else None
                ask = float(statistics.median(asks)) if asks else None
                if bid is not None and ask is not None and ask < bid:
                    bid = None
                    ask = None
                reference = MassiveReference(
                    symbol=requested,
                    price=float(statistics.median(mids)),
                    bid=bid,
                    ask=ask,
                    timestamp=_timestamp_iso(newest),
                    age_seconds=max(0.0, (current_ms - newest) / 1000.0),
                    exchange_count=len(mids),
                    event_type="XQ",
                )
                return reference.to_payload()

        aggregate_time = int(aggregate.get("e") or aggregate.get("s") or 0)
        aggregate_price = _finite_positive(aggregate.get("c"))
        if aggregate_price is not None and aggregate_time > 0 and current_ms - aggregate_time <= max_age_ms:
            return MassiveReference(
                symbol=requested,
                price=aggregate_price,
                bid=None,
                ask=None,
                timestamp=_timestamp_iso(aggregate_time),
                age_seconds=max(0.0, (current_ms - aggregate_time) / 1000.0),
                exchange_count=1,
                event_type="XAS",
            ).to_payload()

        trade_time = int(trade.get("t") or 0)
        trade_price = _finite_positive(trade.get("p"))
        if trade_price is not None and trade_time > 0 and current_ms - trade_time <= max_age_ms:
            return MassiveReference(
                symbol=requested,
                price=trade_price,
                bid=None,
                ask=None,
                timestamp=_timestamp_iso(trade_time),
                age_seconds=max(0.0, (current_ms - trade_time) / 1000.0),
                exchange_count=1,
                event_type="XT",
            ).to_payload()
        return None

    def should_log_consensus(self, symbol: str, *, interval_seconds: float = 60.0) -> bool:
        key = _pair(symbol)
        now = time.monotonic()
        with self._lock:
            previous = self._last_consensus_log.get(key, 0.0)
            if now - previous < interval_seconds:
                return False
            self._last_consensus_log[key] = now
            return True

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "authenticated": self._authenticated,
                "quotes": int(self._counts.get("XQ", 0)),
                "trades": int(self._counts.get("XT", 0)),
                "aggregates": int(self._counts.get("XAS", 0)),
                "quote_pairs": len(self._quotes),
                "trade_pairs": len(self._trades),
                "aggregate_pairs": len(self._aggregates),
            }

    def _ingest_quote(self, item: dict[str, Any]) -> None:
        pair = _pair(item.get("pair"))
        bid = _finite_positive(item.get("bp"))
        ask = _finite_positive(item.get("ap"))
        timestamp = int(item.get("t") or 0)
        if not pair or bid is None or ask is None or ask < bid or timestamp <= 0:
            return
        try:
            exchange_id = int(item.get("x") if item.get("x") is not None else -1)
        except (TypeError, ValueError):
            exchange_id = -1
        with self._lock:
            self._quotes.setdefault(pair, {})[exchange_id] = {
                "bp": bid,
                "ap": ask,
                "bs": item.get("bs"),
                "as": item.get("as"),
                "t": timestamp,
                "x": exchange_id,
            }
            self._counts["XQ"] += 1
        self._maybe_log_health()

    def _ingest_trade(self, item: dict[str, Any]) -> None:
        pair = _pair(item.get("pair"))
        price = _finite_positive(item.get("p"))
        timestamp = int(item.get("t") or 0)
        if not pair or price is None or timestamp <= 0:
            return
        with self._lock:
            self._trades[pair] = dict(item)
            self._counts["XT"] += 1
        self._maybe_log_health()

    def _ingest_aggregate(self, item: dict[str, Any]) -> None:
        pair = _pair(item.get("pair"))
        price = _finite_positive(item.get("c"))
        timestamp = int(item.get("e") or item.get("s") or 0)
        if not pair or price is None or timestamp <= 0:
            return
        with self._lock:
            self._aggregates[pair] = dict(item)
            self._counts["XAS"] += 1
        self._maybe_log_health()

    def _maybe_log_health(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_health_log < 60.0:
                return
            self._last_health_log = now
            stats = self.stats()
        log.info(
            "MASSIVE WS HEALTH | connected=%s | auth=%s | quotes=%d | trades=%d | xas=%d | quote_pairs=%d | trade_pairs=%d | aggregate_pairs=%d | broker_submission=NONE",
            "YES" if stats["connected"] else "NO",
            "PASS" if stats["authenticated"] else "PENDING",
            stats["quotes"],
            stats["trades"],
            stats["aggregates"],
            stats["quote_pairs"],
            stats["trade_pairs"],
            stats["aggregate_pairs"],
        )

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        with self._lock:
            self._connected = True
            self._authenticated = False
        log.info("MASSIVE WS | connection=CONNECTED | endpoint=crypto | auth=PENDING | broker_submission=NONE")
        ws.send(json.dumps({"action": "auth", "params": self.api_key}))

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        messages = payload if isinstance(payload, list) else [payload]
        for item in messages:
            if not isinstance(item, dict):
                continue
            if str(item.get("ev") or "").lower() == "status":
                status = str(item.get("status") or "").lower().strip()
                if status == "auth_success":
                    with self._lock:
                        self._authenticated = True
                    ws.send(json.dumps({"action": "subscribe", "params": ",".join(self.subscriptions)}))
                    log.info(
                        "MASSIVE WS | auth=PASS | subscriptions=%d | scope=%s | broker_submission=NONE",
                        len(self.subscriptions),
                        "all" if any(item.endswith(".*") for item in self.subscriptions) else "watchlist",
                    )
                elif status in {"auth_failed", "error"}:
                    log.warning(
                        "MASSIVE WS | auth=FAIL | status=%s | reason=%s",
                        status,
                        str(item.get("message") or "provider_rejected")[:240],
                    )
                continue
            self.ingest_payload(item)

    def _on_error(self, _ws: websocket.WebSocketApp, error: Any) -> None:
        log.warning("MASSIVE WS | connection=ERROR | reason=%s", error.__class__.__name__)

    def _on_close(self, _ws: websocket.WebSocketApp, status_code: Any, _message: Any) -> None:
        with self._lock:
            self._connected = False
            self._authenticated = False
        log.warning("MASSIVE WS | connection=CLOSED | code=%s", status_code)

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            with self._lock:
                was_authenticated = self._authenticated
            app = websocket.WebSocketApp(
                self.url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._app = app
            log.info(
                "MASSIVE WS | connection=CONNECTING | endpoint=crypto | subscriptions=%d | broker_submission=NONE",
                len(self.subscriptions),
            )
            try:
                app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                log.warning("MASSIVE WS | run=ERROR | reason=%s", exc.__class__.__name__)
            if self._stop.is_set():
                break
            if was_authenticated or self.authenticated:
                backoff = 1.0
            log.info("MASSIVE WS | reconnect_in=%.0fs", backoff)
            self._stop.wait(backoff)
            backoff = min(60.0, backoff * 2.0)


def install_massive_crypto_websocket(worker: Any) -> bool:
    """Start Massive surveillance and attach it as a non-execution reference feed."""
    if os.getenv("MASSIVE_CRYPTO_WS_ENABLED", "false").strip().lower() != "true":
        return False
    if getattr(worker, "_massive_crypto_websocket_installed", False):
        return False

    api_key = (os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY") or "").strip()
    if not api_key:
        log.warning("MASSIVE WS | install=SKIP | reason=API_KEY_MISSING")
        return False

    scope = os.getenv("MASSIVE_CRYPTO_WS_SCOPE", "watchlist").strip().lower()
    channels = _channel_names(os.getenv("MASSIVE_CRYPTO_WS_CHANNELS"))
    symbols = tuple(getattr(worker, "WATCHLISTS", {}).get("crypto", {}).keys())
    subscriptions = build_subscriptions(symbols, scope=scope, channels=channels)
    if not subscriptions:
        log.warning("MASSIVE WS | install=SKIP | reason=NO_SUBSCRIPTIONS")
        return False

    try:
        max_age = float(os.getenv("MASSIVE_CRYPTO_WS_MAX_AGE_SECONDS", "10"))
    except ValueError:
        max_age = 10.0
    try:
        tolerance = float(os.getenv("MASSIVE_CRYPTO_WS_CONSENSUS_TOLERANCE_PCT", "1.0"))
    except ValueError:
        tolerance = 1.0

    stream = MassiveCryptoStream(
        api_key,
        subscriptions,
        url=os.getenv("MASSIVE_CRYPTO_WS_URL", DEFAULT_URL),
        max_age_seconds=max_age,
    )
    if not stream.start():
        return False

    original_execution_quote = worker._execution_quote_payload_from_history

    def execution_quote_payload_from_history(
        symbol: str,
        history: Any,
        price: Any = None,
        *,
        scan_type: str = "",
    ) -> dict[str, Any] | None:
        payload = original_execution_quote(symbol, history, price, scan_type=scan_type)
        if payload is None or infer_asset_class(symbol) != "crypto":
            return payload

        reference = stream.reference(symbol)
        if reference is None:
            payload["massive_reference_verified"] = False
            payload["massive_reference_status"] = "unavailable_or_stale"
            return payload

        primary_price = _finite_positive(payload.get("price"))
        reference_price = _finite_positive(reference.get("price"))
        difference_pct = None
        if primary_price is not None and reference_price is not None:
            difference_pct = abs(primary_price - reference_price) / primary_price * 100.0

        payload.update(
            {
                "massive_reference_verified": True,
                "massive_reference_status": "available",
                "massive_reference_provider": reference.get("provider"),
                "massive_reference_price": reference_price,
                "massive_reference_bid": reference.get("bid"),
                "massive_reference_ask": reference.get("ask"),
                "massive_reference_timestamp": reference.get("quote_timestamp"),
                "massive_reference_age_seconds": reference.get("age_seconds"),
                "massive_reference_exchange_count": reference.get("exchange_count"),
                "massive_reference_event_type": reference.get("event_type"),
                "massive_reference_difference_pct": difference_pct,
            }
        )

        if stream.should_log_consensus(symbol):
            status = "PASS" if difference_pct is not None and difference_pct <= tolerance else "OBSERVE"
            worker.log.info(
                "CRYPTO | MASSIVE REFERENCE | symbol=%s | status=%s | broker_price=%s | massive_price=%s | diff_pct=%s | exchanges=%s | event=%s | broker_submission=NONE",
                _pair(symbol),
                status,
                f"{primary_price:.8f}" if primary_price is not None else "missing",
                f"{reference_price:.8f}" if reference_price is not None else "missing",
                f"{difference_pct:.6f}" if difference_pct is not None else "missing",
                reference.get("exchange_count"),
                reference.get("event_type"),
            )
        return payload

    worker._execution_quote_payload_from_history = execution_quote_payload_from_history
    worker._massive_crypto_websocket_installed = True
    worker._massive_crypto_stream = stream
    log.info(
        "Installed Massive crypto WebSocket surveillance bridge | scope=%s | channels=%s | subscriptions=%d | execution_authority=Robinhood | broker_submission=NONE",
        scope,
        ",".join(channels),
        len(subscriptions),
    )
    return True
