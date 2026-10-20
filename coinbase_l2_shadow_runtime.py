"""Optional Coinbase Exchange L2 batch wire probe: research diagnostics only.

Never exposes market-data objects to paper execution, forecasting or broker APIs.
A subscriber cannot be enabled unless all execution modes remain disarmed.
"""
from datetime import datetime, timezone
import json
import logging
import os
import threading
import time

import websocket

from coinbase_l2_shadow import CoinbaseL2ShadowBooks, SYMBOLS

log = logging.getLogger("coinbase-l2-shadow-probe")
URI = "wss://ws-feed.exchange.coinbase.com"
CHANNEL = "level2_batch"
MAX_WIRE_BYTES = 4 * 1024 * 1024
MAX_SNAPSHOT_SECONDS = 15
MAX_ACK_SECONDS = 10
MAX_WIRE_IDLE_SECONDS = 15
PUBLICATION_SECONDS = 60
MAX_BACKOFF_SECONDS = 30


class ShadowWireError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def reconnect_delay(attempt):
    return min(MAX_BACKOFF_SECONDS, 2 ** min(5, max(0, int(attempt))))


def subscription(symbol):
    if symbol not in SYMBOLS:
        raise ValueError("Unsupported market data probe symbol")
    return {"type": "subscribe", "channels": [
        {"name": CHANNEL, "product_ids": [symbol]}]}


def subscribed(packet, symbol):
    if packet.get("type") != "subscriptions":
        return False
    channels = packet.get("channels")
    if not isinstance(channels, list):
        return False
    for channel in channels:
        if (isinstance(channel, dict) and channel.get("name") == CHANNEL
                and isinstance(channel.get("product_ids"), list)
                and symbol in channel["product_ids"]):
            return True
    return False


def decode_wire(raw):
    if not isinstance(raw, (bytes, str)) or len(raw) > MAX_WIRE_BYTES:
        raise ShadowWireError("OVERSIZED_OR_INVALID_FRAME")
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ShadowWireError("INVALID_UTF8") from exc
    try:
        packet = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ShadowWireError("INVALID_JSON") from exc
    if not isinstance(packet, dict):
        raise ShadowWireError("NON_OBJECT_FRAME")
    return packet


def permitted():
    """Default off; broker submission and live trading strictly forbidden."""
    return (
        os.getenv("COINBASE_L2_SHADOW_PROBE_ENABLED", "false").strip().lower() == "true"
        and os.getenv("EXECUTION_MODE", "paper").strip().lower() == "paper"
        and os.getenv("ENABLE_BROKER_SUBMISSION", "false").strip().lower() == "false"
        and os.getenv("LIVE_TRADING_ARMED", "false").strip().lower() == "false"
    )


class CoinbaseShadowWireProbe:
    """One connection per symbol; never passes its quotes to any execution system."""

    def __init__(self, symbol, *, socket_factory=None, clock=None, monotonic=None, stop_event=None):
        if symbol not in SYMBOLS:
            raise ValueError("Unsupported Coinbase shadow probe symbol")
        self.symbol = symbol
        self.socket_factory = socket_factory or websocket.create_connection
        self.clock = clock or utc_now
        self.monotonic = monotonic or time.monotonic
        self.stop_event = stop_event or threading.Event()
        self.books = CoinbaseL2ShadowBooks([symbol])
        self.counts = {"snapshots": 0, "updates": 0, "invalid_books": 0,
                       "reconnects": 0, "wire_errors": 0, "comparisons": 0}
        self.thread = None
        self._socket = None

    def _comparison(self, now):
        shadow = self.books.shadow_reference(self.symbol, now=now)
        if not shadow:
            return {"status": "SOURCE_UNAVAILABLE"}
        try:
            from kraken_reference_runtime import read_kraken_reference_state
            from kraken_reference_book import compare_reference
            reference = read_kraken_reference_state().get("quotes", {}).get(self.symbol)
            comparison = compare_reference(reference, shadow, now)
            if comparison.get("status") == "ALIGNED_VENUE_COMPARISON":
                self.counts["comparisons"] += 1
            # Do not copy any raw price, source receipt or executable quote into logs.
            return {"status": comparison.get("status", "UNAVAILABLE"),
                    "timestamp_delta_seconds": comparison.get("timestamp_delta_seconds"),
                    "mid_difference_bps": comparison.get("mid_difference_bps")}
        except Exception as exc:
            return {"status": "COMPARISON_UNAVAILABLE",
                    "reason_class": type(exc).__name__}

    def _report(self, now):
        comparison = self._comparison(now)
        log.info("COINBASE L2 SHADOW PROBE | %s", json.dumps({
            "symbol": self.symbol, "comparison": comparison,
            "counts": self.counts, "reference_only": True,
            "execution_authority": "NONE", "broker_submission": "NONE",
            "live_trading": "DISARMED"}, sort_keys=True))

    def session(self, *, max_packets=None):
        """One bounded live session; tests inject socket and clocks, not network."""
        self.books.disconnected()
        acked = False
        snapshot_seen = False
        packets = 0
        start = last_wire = last_report = self.monotonic()
        ws = None
        try:
            ws = self.socket_factory(URI, timeout=2, enable_multithread=True)
            self._socket = ws
            ws.settimeout(2)
            ws.send(json.dumps(subscription(self.symbol)))
            while not self.stop_event.is_set():
                tick = self.monotonic()
                if not acked and tick - start > MAX_ACK_SECONDS:
                    raise ShadowWireError("SUBSCRIPTION_TIMEOUT")
                if acked and not snapshot_seen and tick - start > MAX_SNAPSHOT_SECONDS:
                    raise ShadowWireError("SNAPSHOT_TIMEOUT")
                if tick - last_wire > MAX_WIRE_IDLE_SECONDS:
                    raise ShadowWireError("WIRE_IDLE")
                if tick - last_report >= PUBLICATION_SECONDS:
                    self._report(self.clock())
                    last_report = tick
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    raise ShadowWireError("EMPTY_OR_CLOSED_FRAME")
                packet = decode_wire(raw)
                message_type = packet.get("type")
                now = self.clock()
                tick = self.monotonic()
                if message_type == "error":
                    raise ShadowWireError("SUBSCRIPTION_REJECTED")
                if message_type == "subscriptions":
                    if not subscribed(packet, self.symbol):
                        raise ShadowWireError("SUBSCRIPTION_UNCONFIRMED")
                    if acked:
                        raise ShadowWireError("DUPLICATE_SUBSCRIPTION")
                    acked = True
                    last_wire = tick
                elif message_type in ("snapshot", "l2update"):
                    if not acked or packet.get("product_id") != self.symbol:
                        raise ShadowWireError("UNSUBSCRIBED_PRODUCT")
                    if not self.books.ingest(packet, received_at=now):
                        self.counts["invalid_books"] += 1
                        raise ShadowWireError("INVALID_LEVEL2_BOOK")
                    if message_type == "snapshot":
                        snapshot_seen = True
                        self.counts["snapshots"] += 1
                    else:
                        self.counts["updates"] += 1
                    last_wire = tick
                elif message_type == "heartbeat" and packet.get("product_id") == self.symbol:
                    # A heartbeat cannot make a stale book or event timestamp fresh.
                    last_wire = tick
                # Unknown message types never refresh the liveness timeout.
                packets += 1
                if max_packets is not None and packets >= max_packets:
                    return
        finally:
            self.books.disconnected()
            self._socket = None
            if ws is not None:
                ws.close()

    def run(self):
        attempt = 0
        while not self.stop_event.is_set() and permitted():
            started = self.monotonic()
            prev = self.counts["updates"]
            try:
                self.session()
            except Exception as exc:
                self.counts["wire_errors"] += 1
                # No untrusted network payloads, credentials or URLs in error logs.
                reason = str(exc) if isinstance(exc, ShadowWireError) else type(exc).__name__
                log.warning("COINBASE L2 SHADOW PROBE | symbol=%s | status=%s | "
                            "execution_authority=NONE", self.symbol, reason)
            if self.stop_event.is_set():
                return
            if self.monotonic() - started >= 60 and self.counts["updates"] > prev:
                attempt = 0
            self.counts["reconnects"] += 1
            self.stop_event.wait(reconnect_delay(attempt))
            attempt += 1

    def start(self):
        if not permitted() or (self.thread and self.thread.is_alive()):
            return False
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run,
                                       name="CoinbaseShadowL2-" + self.symbol, daemon=True)
        self.thread.start()
        return True

    def shutdown(self):
        self.stop_event.set()
        ws = self._socket
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def install_coinbase_shadow_probes(worker):
    """Never installs into the quote adapter, fill writer or execution pipeline."""
    if not permitted() or getattr(worker, "_coinbase_l2_shadow_probes", None):
        return False
    probes = [CoinbaseShadowWireProbe(s) for s in ("BTC-USD", "ETH-USD")]
    if not all(p.start() for p in probes):
        for p in probes:
            p.shutdown()
        return False
    worker._coinbase_l2_shadow_probes = probes
    log.info("COINBASE L2 SHADOW PROBE | status=ENABLED | channels=level2_batch | "
             "symbols=BTC-USD,ETH-USD | execution_authority=NONE | broker_submission=NONE")
    return True
