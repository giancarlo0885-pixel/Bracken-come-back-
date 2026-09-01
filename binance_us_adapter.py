from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
import threading
import time
from typing import Any, Callable, Iterable, Mapping

import requests

log = logging.getLogger("binance-us-adapter")

BINANCE_US_BASE_URL = "https://api.binance.us"
BINANCE_US_REQUEST_WEIGHT_PER_MINUTE = 6_000
BINANCE_US_RAW_REQUESTS_PER_FIVE_MINUTES = 61_000
BINANCE_US_ORDER_RAW_REQUESTS_PER_FIVE_MINUTES = 300_000
BINANCE_US_WS_CONNECT_WEIGHT = 2
BINANCE_US_WS_PING_INTERVAL_SECONDS = 20
BINANCE_US_WS_PONG_DEADLINE_SECONDS = 60

_COINBASE_FALLBACK_REASONS = frozenset(
    {
        "COINBASE_REFERENCE_UNAVAILABLE",
        "COINBASE_PAIR_UNAVAILABLE",
        "COINBASE_PAIR_UNSUPPORTED",
        "COINBASE_QUOTE_MISSING",
        "COINBASE_QUOTE_INVALID",
        "COINBASE_TIMESTAMP_INVALID",
    }
)


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def request_weight(path: str, params: Mapping[str, Any] | None = None) -> int:
    """Return the Binance.US request weight effective 2026-08-31."""
    route = str(path or "").split("?", 1)[0]
    query = dict(params or {})

    if route == "/api/v3/exchangeInfo":
        return 20
    if route in {"/api/v3/ticker/price", "/api/v3/ticker/bookTicker"}:
        return 1 if query.get("symbol") else 2
    if route == "/api/v3/klines":
        return 1
    if route == "/api/v3/depth":
        try:
            limit = int(query.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        if limit <= 100:
            return 5
        if limit <= 500:
            return 25
        if limit <= 1_000:
            return 50
        return 250
    if route in {"/api/v3/trades", "/api/v3/historicalTrades"}:
        return 25
    if route == "/api/v3/aggTrades":
        return 4
    if route == "/api/v3/myTrades":
        return 5 if query.get("orderId") not in (None, "") else 20
    return 1


class BinanceUsTokenBucket:
    """Thread-safe request-weight limiter using Binance.US's 6,000/minute ceiling."""

    def __init__(
        self,
        *,
        capacity: float = BINANCE_US_REQUEST_WEIGHT_PER_MINUTE,
        refill_per_second: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.capacity = max(1.0, float(capacity))
        self.refill_per_second = (
            max(0.001, float(refill_per_second))
            if refill_per_second is not None
            else self.capacity / 60.0
        )
        self._clock = clock
        self._sleeper = sleeper
        self._tokens = self.capacity
        self._last_refill = self._clock()
        self._lock = threading.Lock()

    def _refill_locked(self, now: float) -> None:
        elapsed = max(0.0, now - self._last_refill)
        if elapsed:
            self._tokens = min(
                self.capacity,
                self._tokens + elapsed * self.refill_per_second,
            )
            self._last_refill = now

    def acquire(self, weight: int | float) -> None:
        cost = max(0.0, float(weight))
        if cost > self.capacity:
            raise ValueError("request weight exceeds Binance.US minute capacity")
        while True:
            with self._lock:
                now = self._clock()
                self._refill_locked(now)
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                wait_seconds = (cost - self._tokens) / self.refill_per_second
            self._sleeper(max(0.001, wait_seconds))

    def reconcile_used_weight(self, used_weight_1m: Any) -> None:
        """Fail conservatively when Binance reports more usage than this process observed."""
        try:
            used = max(0.0, float(used_weight_1m))
        except (TypeError, ValueError):
            return
        with self._lock:
            now = self._clock()
            self._refill_locked(now)
            remaining = max(0.0, self.capacity - used)
            self._tokens = min(self._tokens, remaining)

    @property
    def available_tokens(self) -> float:
        with self._lock:
            now = self._clock()
            self._refill_locked(now)
            return self._tokens


class BinanceUsRawRequestLimiter:
    """Sliding-window guard for Binance.US raw-request ceilings."""

    def __init__(
        self,
        *,
        limit: int = BINANCE_US_RAW_REQUESTS_PER_FIVE_MINUTES,
        window_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = max(1.0, float(window_seconds))
        self._clock = clock
        self._sleeper = sleeper
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                cutoff = now - self.window_seconds
                while self._events and self._events[0] <= cutoff:
                    self._events.popleft()
                if len(self._events) < self.limit:
                    self._events.append(now)
                    return
                wait_seconds = self.window_seconds - (now - self._events[0])
            self._sleeper(max(0.001, wait_seconds))


def permission_sets_satisfied(
    permission_sets: Any,
    account_permissions: Iterable[str],
) -> bool:
    """Apply AND across permission sets and OR within each permission set."""
    if not isinstance(permission_sets, list) or not permission_sets:
        return False
    granted = {
        str(permission).upper().strip()
        for permission in account_permissions
        if str(permission).strip()
    }
    if not granted:
        return False

    for requirement_group in permission_sets:
        if not isinstance(requirement_group, list) or not requirement_group:
            return False
        options = {
            str(permission).upper().strip()
            for permission in requirement_group
            if str(permission).strip()
        }
        if not options or granted.isdisjoint(options):
            return False
    return True


def parse_symbol_rules(
    payload: Mapping[str, Any],
    account_permissions: Iterable[str],
) -> dict[str, Any]:
    """Normalize exchangeInfo without relying on the deprecated permissions field."""
    permission_sets = payload.get("permissionSets")
    permission_ok = permission_sets_satisfied(permission_sets, account_permissions)
    status = str(payload.get("status") or "").upper().strip()
    spot_allowed = payload.get("isSpotTradingAllowed") is not False
    tradable = status == "TRADING" and spot_allowed and permission_ok

    if status != "TRADING":
        reason = "BINANCE_US_SYMBOL_NOT_TRADING"
    elif not spot_allowed:
        reason = "BINANCE_US_SPOT_DISABLED"
    elif not permission_ok:
        reason = "BINANCE_US_PERMISSION_SETS_UNSATISFIED"
    else:
        reason = "BINANCE_US_SYMBOL_TRADABLE"

    filters = {
        str(item.get("filterType") or ""): dict(item)
        for item in payload.get("filters", [])
        if isinstance(item, Mapping) and item.get("filterType")
    }
    return {
        "symbol": str(payload.get("symbol") or "").upper().strip(),
        "base_asset": str(payload.get("baseAsset") or "").upper().strip(),
        "quote_asset": str(payload.get("quoteAsset") or "").upper().strip(),
        "status": status,
        "is_spot_trading_allowed": spot_allowed,
        "permission_sets": permission_sets if isinstance(permission_sets, list) else [],
        "permission_sets_satisfied": permission_ok,
        "tradable": tradable,
        "reason": reason,
        "filters": filters,
        "legacy_permissions_ignored": bool(payload.get("permissions")),
    }


def parse_trade_prevention_execution_report(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize the 2026 TRADE_PREVENTION fields; never use legacy l/L/Y values."""
    if str(event.get("e") or "") != "executionReport":
        return None
    if str(event.get("x") or "") != "TRADE_PREVENTION":
        return None
    return {
        "event": "TRADE_PREVENTION",
        "symbol": str(event.get("s") or "").upper().strip(),
        "order_id": event.get("i"),
        "order_status": event.get("X"),
        "event_time_ms": event.get("E"),
        "prevented_match_id": event.get("v"),
        "counter_order_id": event.get("U"),
        "trade_group_id": event.get("u"),
        "prevented_quantity": None if event.get("pl") is None else str(event.get("pl")),
        "prevented_price": None if event.get("pL") is None else str(event.get("pL")),
        "prevented_notional": None if event.get("pY") is None else str(event.get("pY")),
        "expiry_reason": event.get("eR"),
    }


def websocket_control_event(event: Mapping[str, Any]) -> dict[str, Any]:
    event_name = str(event.get("e") or "")
    if event_name == "serverShutdown":
        return {
            "event": event_name,
            "reconnect_required": True,
            "reason": "BINANCE_US_SERVER_SHUTDOWN",
        }
    return {
        "event": event_name,
        "reconnect_required": False,
        "reason": "",
    }


@dataclass(frozen=True)
class BinanceUsReferenceQuote:
    symbol: str
    native_symbol: str
    quote_currency: str
    bid: float
    ask: float
    price: float
    timestamp: str
    provider: str = "Binance.US"

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "native_symbol": self.native_symbol,
            "quote_currency": self.quote_currency,
            "bid": self.bid,
            "ask": self.ask,
            "price": self.price,
            "timestamp": self.timestamp,
            "provider": self.provider,
        }


class BinanceUsClient:
    def __init__(
        self,
        *,
        session: requests.Session | Any | None = None,
        base_url: str = BINANCE_US_BASE_URL,
        timeout_seconds: float = 5.0,
        token_bucket: BinanceUsTokenBucket | None = None,
        raw_request_limiter: BinanceUsRawRequestLimiter | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.base_url = str(base_url or BINANCE_US_BASE_URL).rstrip("/")
        self.timeout_seconds = max(0.5, float(timeout_seconds))
        self.token_bucket = token_bucket or BinanceUsTokenBucket()
        self.raw_request_limiter = raw_request_limiter or BinanceUsRawRequestLimiter()
        self._exchange_info_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        query = dict(params or {})
        weight = request_weight(path, query)
        self.token_bucket.acquire(weight)
        self.raw_request_limiter.acquire()

        response = self.session.get(
            f"{self.base_url}{path}",
            params=query,
            headers={
                "cache-control": "no-cache",
                "user-agent": "GARIBALDI-MARKET-ORACLE/1.0",
            },
            timeout=self.timeout_seconds,
        )
        self.token_bucket.reconcile_used_weight(
            response.headers.get("X-MBX-USED-WEIGHT-1M")
        )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RuntimeError(
                f"BINANCE_US_RATE_LIMITED retry_after={retry_after or 'unknown'}"
            )
        response.raise_for_status()
        return response.json()

    def exchange_info(
        self,
        native_symbol: str | None = None,
        *,
        show_permission_sets: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "showPermissionSets": "true" if show_permission_sets else "false",
        }
        if native_symbol:
            params["symbol"] = str(native_symbol).upper().strip()
        payload = self._get("/api/v3/exchangeInfo", params)
        if not isinstance(payload, Mapping):
            raise RuntimeError("BINANCE_US_EXCHANGE_INFO_INVALID")
        return dict(payload)

    def _account_permissions(self) -> set[str]:
        return {
            value.strip().upper()
            for value in os.getenv("BINANCE_US_ACCOUNT_PERMISSIONS", "SPOT").split(",")
            if value.strip()
        }

    def symbol_rules(
        self,
        native_symbol: str,
        *,
        account_permissions: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        native = str(native_symbol or "").upper().strip()
        if not native:
            return {"tradable": False, "reason": "BINANCE_US_SYMBOL_MISSING"}

        try:
            ttl_seconds = max(
                30,
                int(os.getenv("BINANCE_US_EXCHANGE_INFO_TTL_SECONDS", "300")),
            )
        except ValueError:
            ttl_seconds = 300

        now = time.monotonic()
        with self._cache_lock:
            cached = self._exchange_info_cache.get(native)
            if cached and now - cached[0] <= ttl_seconds:
                symbol_payload = cached[1]
            else:
                symbol_payload = {}

        if not symbol_payload:
            payload = self.exchange_info(native, show_permission_sets=True)
            symbols = payload.get("symbols")
            if not isinstance(symbols, list) or not symbols:
                return {
                    "symbol": native,
                    "tradable": False,
                    "reason": "BINANCE_US_SYMBOL_INFO_MISSING",
                }
            candidate = next(
                (
                    dict(item)
                    for item in symbols
                    if isinstance(item, Mapping)
                    and str(item.get("symbol") or "").upper().strip() == native
                ),
                None,
            )
            if candidate is None:
                return {
                    "symbol": native,
                    "tradable": False,
                    "reason": "BINANCE_US_SYMBOL_INFO_MISSING",
                }
            symbol_payload = candidate
            with self._cache_lock:
                self._exchange_info_cache[native] = (now, symbol_payload)

        return parse_symbol_rules(
            symbol_payload,
            account_permissions or self._account_permissions(),
        )

    def book_ticker(self, native_symbol: str) -> dict[str, Any]:
        native = str(native_symbol or "").upper().strip()
        payload = self._get("/api/v3/ticker/bookTicker", {"symbol": native})
        if not isinstance(payload, Mapping):
            raise RuntimeError("BINANCE_US_BOOK_TICKER_INVALID")
        return dict(payload)

    def order_book(self, native_symbol: str, *, limit: int = 100) -> dict[str, Any]:
        native = str(native_symbol or "").upper().strip()
        depth = max(1, min(5_000, int(limit)))
        payload = self._get(
            "/api/v3/depth",
            {"symbol": native, "limit": depth},
        )
        if not isinstance(payload, Mapping):
            raise RuntimeError("BINANCE_US_ORDER_BOOK_INVALID")
        return dict(payload)

    def reference_quote(self, symbol: str) -> dict[str, Any]:
        canonical = str(symbol or "").upper().strip()
        if not canonical.endswith("-USD"):
            return {
                "ok": False,
                "reason": "BINANCE_US_PAIR_UNSUPPORTED",
                "symbol": canonical,
            }
        base = canonical[:-4]
        native = f"{base}USD"

        rules = self.symbol_rules(native)
        if rules.get("tradable") is not True:
            return {
                "ok": False,
                "reason": str(rules.get("reason") or "BINANCE_US_SYMBOL_NOT_TRADABLE"),
                "symbol": canonical,
                "native_symbol": native,
                "rules": rules,
            }

        try:
            ticker = self.book_ticker(native)
        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            if getattr(response, "status_code", None) == 400:
                return {
                    "ok": False,
                    "reason": "BINANCE_US_PAIR_UNAVAILABLE",
                    "symbol": canonical,
                    "native_symbol": native,
                }
            raise

        bid = _finite_float(ticker.get("bidPrice"))
        ask = _finite_float(ticker.get("askPrice"))
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            return {
                "ok": False,
                "reason": "BINANCE_US_QUOTE_INVALID",
                "symbol": canonical,
                "native_symbol": native,
            }

        mid = (bid + ask) / 2.0
        timestamp = datetime.now(timezone.utc).isoformat()
        quote = BinanceUsReferenceQuote(
            symbol=canonical,
            native_symbol=native,
            quote_currency="USD",
            bid=bid,
            ask=ask,
            price=mid,
            timestamp=timestamp,
        )
        return {"ok": True, **quote.as_dict()}


_DEFAULT_CLIENT: BinanceUsClient | None = None
_DEFAULT_CLIENT_LOCK = threading.Lock()


def _default_client() -> BinanceUsClient:
    global _DEFAULT_CLIENT
    with _DEFAULT_CLIENT_LOCK:
        if _DEFAULT_CLIENT is None:
            _DEFAULT_CLIENT = BinanceUsClient()
        return _DEFAULT_CLIENT


def validate_binance_us_reference(
    symbol: str,
    oracle_price: Any,
    *,
    client: BinanceUsClient | None = None,
) -> dict[str, Any]:
    """Cross-check an Oracle paper mark against Binance.US's exact USD book."""
    reference = _finite_float(oracle_price)
    if reference is None or reference <= 0:
        return {"ok": False, "reason": "BINANCE_US_ORACLE_PRICE_INVALID"}

    try:
        quote = (client or _default_client()).reference_quote(symbol)
    except Exception as exc:
        log.warning(
            "Binance.US reference unavailable | symbol=%s | error=%s",
            str(symbol or "").upper().strip(),
            exc.__class__.__name__,
        )
        return {"ok": False, "reason": "BINANCE_US_REFERENCE_UNAVAILABLE"}

    if quote.get("ok") is not True:
        return {
            "ok": False,
            "reason": str(quote.get("reason") or "BINANCE_US_QUOTE_MISSING"),
        }

    bid = _finite_float(quote.get("bid"))
    ask = _finite_float(quote.get("ask"))
    mid = _finite_float(quote.get("price"))
    if bid is None or ask is None or mid is None or bid <= 0 or ask <= 0 or mid <= 0:
        return {"ok": False, "reason": "BINANCE_US_QUOTE_INVALID"}

    try:
        max_diff_pct = max(
            0.0,
            float(os.getenv("BINANCE_US_REFERENCE_MAX_DIFF_PCT", "1.00")),
        )
        max_spread_pct = max(
            0.0,
            float(os.getenv("BINANCE_US_REFERENCE_MAX_SPREAD_PCT", "1.50")),
        )
    except ValueError:
        return {"ok": False, "reason": "BINANCE_US_REFERENCE_CONFIG_INVALID"}

    spread_pct = ((ask - bid) / mid) * 100.0
    difference_pct = abs(reference - mid) / mid * 100.0
    context = {
        "reference_provider": "Binance.US",
        "reference_price": mid,
        "reference_timestamp": str(quote.get("timestamp") or ""),
        "spread_pct": spread_pct,
        "difference_pct": difference_pct,
        "reference_native_symbol": str(quote.get("native_symbol") or ""),
        "reference_quote_currency": str(quote.get("quote_currency") or ""),
    }

    if spread_pct > max_spread_pct:
        return {
            "ok": False,
            "reason": "BINANCE_US_SPREAD_TOO_WIDE",
            **context,
        }
    if difference_pct > max_diff_pct:
        return {
            "ok": False,
            "reason": "BINANCE_US_PRICE_DIVERGENCE",
            **context,
        }
    return {
        "ok": True,
        "reason": "BINANCE_US_REFERENCE_CONFIRMED",
        **context,
    }


def install_binance_us_reference_fallback() -> None:
    """Install Binance.US only as a fail-safe fallback for unavailable Coinbase data.

    A Coinbase divergence, stale quote, or wide spread remains a hard rejection.
    Binance.US therefore improves provider availability without weakening the
    existing independent-consensus execution gate.
    """
    import crypto_execution_guard as guard

    if getattr(guard, "_binance_us_reference_fallback_installed", False):
        return

    original_validation = guard._coinbase_reference_validation
    original_record = guard._quote_verification_record

    def composite_validation(symbol: str, oracle_price: Any) -> dict[str, Any]:
        coinbase = original_validation(symbol, oracle_price)
        if coinbase.get("ok") is True:
            return coinbase

        reason = str(coinbase.get("reason") or "")
        enabled = (
            os.getenv("BINANCE_US_REFERENCE_ENABLED", "true").strip().lower()
            == "true"
        )
        if not enabled or reason not in _COINBASE_FALLBACK_REASONS:
            return coinbase

        binance = validate_binance_us_reference(symbol, oracle_price)
        if binance.get("ok") is True:
            binance["fallback_from_provider"] = "Coinbase Exchange"
            binance["fallback_from_reason"] = reason
            return binance

        return {
            **coinbase,
            "fallback_provider": "Binance.US",
            "fallback_reason": str(
                binance.get("reason") or "BINANCE_US_REFERENCE_REJECTED"
            ),
        }

    def exchange_aware_record(
        symbol: str,
        oracle_quote: dict[str, Any],
        validation: dict[str, Any],
    ) -> dict[str, Any]:
        record = original_record(symbol, oracle_quote, validation)
        if str(validation.get("reference_provider") or "") == "Binance.US":
            payload = dict(record.get("payload") or {})
            payload["evidence_kind"] = "yahoo_exchange_execution_consensus"
            payload["attempted_secondary_provider"] = "Binance.US"
            payload["fallback_from_provider"] = validation.get(
                "fallback_from_provider"
            )
            payload["fallback_from_reason"] = validation.get("fallback_from_reason")
            record["payload"] = payload
        return record

    guard._coinbase_reference_validation = composite_validation
    guard._quote_verification_record = exchange_aware_record

    try:
        import crypto_quote_readiness_sampler as sampler

        sampler._coinbase_reference_validation = composite_validation
        sampler._quote_verification_record = exchange_aware_record
    except Exception:
        log.warning(
            "Binance.US sampler integration unavailable; execution guard remains patched"
        )

    guard._binance_us_reference_fallback_installed = True
    log.info(
        "Installed Binance.US Coinbase-unavailability fallback with 2026 permission/rate-limit semantics"
    )
