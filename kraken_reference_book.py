"""Kraken v2 depth-10 books. Reference data only; never an execution adapter."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import json
import zlib

SOURCE = "Kraken WebSocket v2 reference"
SYMBOLS = {"BTC/USD": "BTC-USD", "ETH/USD": "ETH-USD"}
DEPTH = 10
MAX_AGE = 10.0
MAX_ALIGNMENT = 2.0


class BookIntegrityError(ValueError):
    pass


def timestamp(value):
    if not isinstance(value, str):
        raise BookIntegrityError("invalid_timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BookIntegrityError("invalid_timestamp") from exc
    if result.tzinfo is None:
        raise BookIntegrityError("invalid_timestamp")
    return result.astimezone(timezone.utc)


def exact_decimal(value):
    # A binary float has already lost wire precision; never use it for CRC32.
    if type(value) not in (str, int, Decimal) or len(str(value)) > 100:
        raise BookIntegrityError("invalid_numeric_precision")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise BookIntegrityError("invalid_number") from exc
    if not number.is_finite() or abs(number.adjusted()) > 30:
        raise BookIntegrityError("invalid_number")
    return number


def book_checksum(bids, asks):
    """Kraken: top ten asks ascending, bids descending; preserve trailing zeros."""
    parts = []
    for side, reverse in ((asks, False), (bids, True)):
        for price in sorted(side, reverse=reverse)[:DEPTH]:
            for number in (price, side[price]):
                parts.append(format(number, "f").replace(".", "").lstrip("0"))
    return zlib.crc32("".join(parts).encode("ascii")) & 0xffffffff


def fresh(exchange_time, received_time, now):
    return (-2 <= (now - exchange_time).total_seconds() <= MAX_AGE
            and 0 <= (now - received_time).total_seconds() <= MAX_AGE)


class ReferenceBooks:
    """Single-threaded state machine; caller serializes messages in wire order.

    Kraken v2 book has no sequence number. Equal exchange timestamps are valid;
    ordered delivery plus every-message CRC32 establishes book consistency.
    """
    def __init__(self):
        self.books = {}
        self.counts = Counter()
        self.connected = False
        self.last_message = None
        self.last_valid = {}
        self.subscribed = set()
        self.stale_seen = set()
        self.status = "DISCONNECTED"

    def invalidate(self, reason="DISCONNECTED"):
        self.books.clear()
        self.subscribed.clear()
        self.connected = False
        self.status = reason

    def opened(self, now):
        self.invalidate("AWAITING_SNAPSHOT")
        self.connected = True
        self.last_message = now

    def ingest(self, raw, received):
        try:
            if not isinstance(raw, str) or len(raw) > 1_000_000:
                raise BookIntegrityError("invalid_message_size")
            message = json.loads(raw, parse_float=Decimal,
                                 parse_constant=lambda _: (_ for _ in ()).throw(BookIntegrityError("invalid_number")))
            if not isinstance(message, dict):
                raise BookIntegrityError("invalid_message")
            self.last_message = received
            if message.get("method") == "subscribe":
                result = message.get("result") or {}
                if (message.get("success") is not True or result.get("channel") != "book"
                        or result.get("symbol") not in SYMBOLS or result.get("depth") != DEPTH
                        or result.get("snapshot") is not True):
                    raise BookIntegrityError("subscription_error")
                self.subscribed.add(result["symbol"])
                return
            channel = message.get("channel")
            if channel == "status":
                if message["data"][0].get("system") != "online":
                    raise BookIntegrityError("exchange_unavailable")
                return
            if channel != "book":
                if channel == "heartbeat" or message.get("method") == "pong":
                    self.counts["heartbeats"] += 1
                    return
                raise BookIntegrityError("unexpected_message")
            kind = message.get("type")
            data = message.get("data")
            if kind not in {"snapshot", "update"} or not isinstance(data, list) or not 1 <= len(data) <= 2:
                raise BookIntegrityError("invalid_book_message")
            for item in data:
                self._apply(item, kind, received)
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
            reason = str(exc) if isinstance(exc, BookIntegrityError) else "malformed_message"
            self.counts[reason] += 1
            self.invalidate(reason)
            raise BookIntegrityError(reason) from exc

    def _apply(self, item, kind, received):
        symbol = item["symbol"]
        if symbol not in SYMBOLS or symbol not in self.subscribed:
            raise BookIntegrityError("unexpected_symbol")
        previous = self.books.get(symbol)
        if kind == "update" and previous is None:
            raise BookIntegrityError("snapshot_required")
        observed = timestamp(item["timestamp"])
        if not fresh(observed, received, received):
            raise BookIntegrityError("stale_rejections")
        if previous and observed < previous["exchange_time"]:
            raise BookIntegrityError("timestamp_regression")
        sides = {name: {} if kind == "snapshot" else dict(previous[name]) for name in ("bids", "asks")}
        for name in sides:
            levels = item.get(name, [])
            if not isinstance(levels, list) or len(levels) > 2000:
                raise BookIntegrityError("invalid_levels")
            for level in levels:
                price, qty = exact_decimal(level["price"]), exact_decimal(level["qty"])
                if price <= 0 or qty < 0:
                    raise BookIntegrityError("invalid_levels")
                # Replacement, not addition. Repeated levels in one message are sequential.
                # Remove first so the new Decimal price representation wins too.
                sides[name].pop(price, None)
                if qty:
                    sides[name][price] = qty
            # Truncate only after ALL updates to this side, before checksum.
            sides[name] = dict(sorted(sides[name].items(), reverse=name == "bids")[:DEPTH])
        checksum = item["checksum"]
        if type(checksum) is not int or not 0 <= checksum <= 0xffffffff:
            raise BookIntegrityError("invalid_checksum")
        if book_checksum(sides["bids"], sides["asks"]) != checksum:
            raise BookIntegrityError("checksum_failures")
        if not sides["bids"] or not sides["asks"]:
            raise BookIntegrityError("empty_book")
        if max(sides["bids"]) > min(sides["asks"]):
            raise BookIntegrityError("crossed_book_rejections")
        self.books[symbol] = {**sides, "exchange_time": observed, "received_time": received, "checksum": checksum}
        self.last_valid[symbol] = {"exchange_timestamp": observed.isoformat(), "received_at": received.isoformat()}
        self.stale_seen.discard(symbol)
        self.counts[kind + "s"] += 1
        self.status = "STREAMING"

    def quote(self, symbol, now):
        book = self.books.get(symbol)
        if not self.connected or book is None:
            return None
        if not fresh(book["exchange_time"], book["received_time"], now):
            if symbol not in self.stale_seen:
                self.counts["stale_rejections"] += 1
                self.stale_seen.add(symbol)
            return None
        bid, ask = max(book["bids"]), min(book["asks"])
        with localcontext() as ctx:
            ctx.prec = 80
            mid, spread = (bid + ask) / 2, ask - bid
            spread_pct = spread / mid * 100
        return {
            "provider": SOURCE, "source": SOURCE, "venue": "Kraken", "symbol": SYMBOLS[symbol],
            "exchange_symbol": symbol, "bid": str(bid), "ask": str(ask),
            "bid_quantity": str(book["bids"][bid]), "ask_quantity": str(book["asks"][ask]),
            "price": str(mid), "spread": str(spread), "spread_pct": str(spread_pct),
            "quote_timestamp": book["exchange_time"].isoformat(), "received_at": book["received_time"].isoformat(),
            "age_seconds": (now - book["exchange_time"]).total_seconds(),
            "checksum": book["checksum"], "checksum_status": "VALID", "fresh": True, "stale": False,
            "reference_only": True, "verified": False, "quote_verified": False,
            "provider_quote_verified": False, "paper_reference_verified": False,
            "quote_eligible": False, "execution_quote_eligible": False, "execution_authority": "NONE",
        }

    def health(self, now):
        quotes = {SYMBOLS[s]: q for s in SYMBOLS if (q := self.quote(s, now)) is not None}
        status = self.status
        if self.connected and len(quotes) != len(SYMBOLS):
            status = "STALE_OR_AWAITING_SNAPSHOT"
        counts = {key: self.counts[key] for key in ("snapshots", "updates", "reconnects", "heartbeats",
            "checksum_failures", "stale_rejections", "crossed_book_rejections", "subscription_error", "connection_failures")}
        counts.update(self.counts)
        return {"provider": SOURCE, "status": status, "connected": self.connected,
                "last_message_at": self.last_message.isoformat() if self.last_message else None,
                "last_valid_update": dict(self.last_valid), "counts": counts,
                "quotes": quotes, "execution_authority": "NONE", "broker_submission": "NONE"}


def compare_reference(reference, primary, now):
    """Diagnostic venue basis only. Never changes either quote's authority."""
    unavailable = {"status": "UNAVAILABLE_OR_UNALIGNED", "execution_impact": "NONE"}
    if not reference or not primary or primary.get("symbol") != reference.get("symbol"):
        return unavailable
    try:
        rt, rr = timestamp(reference["quote_timestamp"]), timestamp(reference["received_at"])
        pt = timestamp(primary["quote_timestamp"])
        if (reference.get("checksum_status") != "VALID" or not fresh(rt, rr, now)
                or not -2 <= (now - pt).total_seconds() <= MAX_AGE
                or abs((rt - pt).total_seconds()) > MAX_ALIGNMENT or primary.get("stale") is True):
            return unavailable
        bid, ask = exact_decimal(str(primary["bid"])), exact_decimal(str(primary["ask"]))
        if bid <= 0 or ask < bid:
            return {"status": "PRIMARY_INVALID_BOOK", "execution_impact": "NONE"}
        with localcontext() as ctx:
            ctx.prec = 80
            mid = (bid + ask) / 2
            delta = (exact_decimal(reference["price"]) - mid) / mid * 10000
        return {"status": "ALIGNED_VENUE_COMPARISON", "symbol": primary["symbol"],
                "primary_provider": str(primary.get("provider") or "unknown")[:64],
                "reference_provider": SOURCE, "primary_timestamp": pt.isoformat(),
                "reference_timestamp": rt.isoformat(), "timestamp_delta_seconds": abs((rt - pt).total_seconds()),
                "mid_difference_bps": str(delta), "execution_impact": "NONE"}
    except (ValueError, KeyError, TypeError, InvalidOperation):
        return unavailable
