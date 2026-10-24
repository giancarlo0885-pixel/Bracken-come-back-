"""Isolated Coinbase Exchange L2 book validator for future shadow-feed evaluation.

No socket, database, background task or execution hook is installed by this module.
Snapshots do not carry an exchange event timestamp and NEVER authorize a quote.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

SOURCE = "Coinbase Exchange Level2 shadow"
SYMBOLS = frozenset({"BTC-USD", "ETH-USD"})
MAX_LEVELS_PER_SIDE = 30000
MAX_CHANGES = 5000
MAX_QUOTE_AGE_SECONDS = 5
MAX_FUTURE_SKEW_SECONDS = 2


class InvalidBook(ValueError):
    pass


@dataclass
class _Book:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    snapshot_seen: bool = False
    event_at: datetime | None = None
    received_at: datetime | None = None


def _decimal(raw, *, allow_zero=False):
    if not isinstance(raw, str) or not 1 <= len(raw) <= 48:
        raise InvalidBook("NON_DECIMAL_STRING")
    if not all(c in "0123456789." for c in raw) or raw.count(".") > 1:
        raise InvalidBook("INVALID_DECIMAL")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise InvalidBook("INVALID_DECIMAL") from exc
    if not value.is_finite() or value < 0 or (not allow_zero and value == 0):
        raise InvalidBook("NONPOSITIVE_DECIMAL")
    return value


def _utc(moment):
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise InvalidBook("MISSING_UTC_RECEIPT")
    return moment.astimezone(timezone.utc)


def _event_time(raw):
    if not isinstance(raw, str) or len(raw) > 64:
        raise InvalidBook("MISSING_EXCHANGE_EVENT_TIME")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidBook("INVALID_EXCHANGE_EVENT_TIME") from exc
    return _utc(value)


def _fresh(event, receipt):
    age = (receipt - event).total_seconds()
    return -MAX_FUTURE_SKEW_SECONDS <= age <= MAX_QUOTE_AGE_SECONDS


def _valid_sides(bids, asks):
    if not bids or not asks:
        raise InvalidBook("EMPTY_SIDE")
    if max(bids) >= min(asks):
        raise InvalidBook("LOCKED_OR_CROSSED")
    if len(bids) > MAX_LEVELS_PER_SIDE or len(asks) > MAX_LEVELS_PER_SIDE:
        raise InvalidBook("TOO_MANY_LEVELS")


def _snapshot_side(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_LEVELS_PER_SIDE:
        raise InvalidBook("INVALID_SNAPSHOT_SIDE")
    side = {}
    for level in raw:
        if not isinstance(level, list) or len(level) != 2:
            raise InvalidBook("INVALID_SNAPSHOT_LEVEL")
        price, size = _decimal(level[0]), _decimal(level[1])
        if price in side:
            raise InvalidBook("DUPLICATE_SNAPSHOT_LEVEL")
        side[price] = size
    return side


class CoinbaseL2ShadowBooks:
    """Fail-closed deterministic L2 reducer; never an execution price source."""

    def __init__(self, symbols=SYMBOLS):
        allowed = frozenset(symbols)
        if not allowed or not allowed.issubset(SYMBOLS):
            raise ValueError("unsupported shadow symbols")
        self._books = {symbol: _Book() for symbol in allowed}

    def disconnected(self):
        """Drop every book on wire disconnect, re-subscription or integrity loss."""
        self._books = {symbol: _Book() for symbol in self._books}

    def ingest(self, packet, *, received_at):
        if not isinstance(packet, dict):
            return False
        symbol = packet.get("product_id")
        if symbol not in self._books:
            return False
        message_type = packet.get("type")
        if message_type not in ("snapshot", "l2update"):
            return False
        try:
            receipt = _utc(received_at)
            if message_type == "snapshot":
                bids = _snapshot_side(packet.get("bids"))
                asks = _snapshot_side(packet.get("asks"))
                _valid_sides(bids, asks)
                # Exchange L2 snapshots have no exchange event time: no quote yet.
                self._books[symbol] = _Book(bids, asks, True, None, receipt)
                return True

            old = self._books[symbol]
            if not old.snapshot_seen:
                raise InvalidBook("UPDATE_BEFORE_SNAPSHOT")
            event = _event_time(packet.get("time"))
            if not _fresh(event, receipt):
                raise InvalidBook("STALE_OR_FUTURE_EVENT")
            if old.event_at is not None and event < old.event_at:
                raise InvalidBook("REGRESSING_EVENT_TIME")
            changes = packet.get("changes")
            if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CHANGES:
                raise InvalidBook("INVALID_CHANGES")
            bids, asks = old.bids.copy(), old.asks.copy()
            for change in changes:
                if not isinstance(change, list) or len(change) != 3:
                    raise InvalidBook("INVALID_CHANGE")
                label, price_raw, size_raw = change
                if label not in ("buy", "sell"):
                    raise InvalidBook("INVALID_SIDE")
                target = bids if label == "buy" else asks
                price = _decimal(price_raw)
                size = _decimal(size_raw, allow_zero=True)
                if size == 0:
                    target.pop(price, None)
                else:
                    target[price] = size
            _valid_sides(bids, asks)
            self._books[symbol] = _Book(bids, asks, True, event, receipt)
            return True
        except (InvalidBook, TypeError, KeyError):
            # A partial/corrupted stream can never inherit an earlier good book.
            self._books[symbol] = _Book()
            return False

    def shadow_reference(self, symbol, *, now):
        """A diagnostic comparison mark, explicitly forbidden for fills/entries."""
        book = self._books.get(symbol)
        if book is None or book.event_at is None or book.received_at is None:
            return None
        try:
            current = _utc(now)
            if not (_fresh(book.event_at, current) and
                    _fresh(book.received_at, current) and
                    _fresh(book.event_at, book.received_at)):
                return None
            _valid_sides(book.bids, book.asks)
            bid, ask = max(book.bids), min(book.asks)
            return {
                "symbol": symbol, "provider": SOURCE,
                "bid": str(bid), "ask": str(ask),
                "bid_quantity": str(book.bids[bid]),
                "ask_quantity": str(book.asks[ask]),
                "price": str((bid + ask) / 2),
                "quote_timestamp": book.event_at.isoformat(),
                "received_at": book.received_at.isoformat(),
                "timestamp_basis": "provider_l2update",
                "reference_only": True,
                "quote_verified": False,
                "provider_quote_verified": False,
                "paper_reference_verified": False,
                "execution_quote_eligible": False,
                "execution_authority": "NONE",
                "broker_submission": "NONE",
                "checksum_status": "NOT_PROVIDED",
            }
        except InvalidBook:
            return None
