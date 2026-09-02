from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping
import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

WS_CONNECT_WEIGHT = 2
WS_SERVER_PING_INTERVAL_SECONDS = 20.0
WS_PONG_DEADLINE_SECONDS = 60.0
MY_FILTERS_WEIGHT = 40


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite():
        return None
    return result


def signed_my_filters(
    *,
    api_key: str,
    secret_key: str,
    symbol: str,
    session: Any | None = None,
    base_url: str = "https://api.binance.us",
    recv_window: int = 5000,
    timestamp_ms: int | None = None,
    timeout_seconds: float = 5.0,
    acquire_weight: Callable[[int], Any] | None = None,
) -> dict[str, Any]:
    """Fetch account-specific filters via signed GET /api/v3/myFilters.

    Secrets are supplied by the caller and are never logged or returned.
    """
    native = str(symbol or "").upper().strip()
    if not native:
        raise ValueError("BINANCE_US_SYMBOL_MISSING")
    key = str(api_key or "").strip()
    secret = str(secret_key or "")
    if not key or not secret:
        raise ValueError("BINANCE_US_API_CREDENTIALS_MISSING")

    window = max(1, min(60_000, int(recv_window)))
    ts = int(timestamp_ms if timestamp_ms is not None else time.time() * 1000)
    params = {"symbol": native, "recvWindow": window, "timestamp": ts}
    query = urlencode(params)
    params["signature"] = hmac.new(
        secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if acquire_weight is not None:
        acquire_weight(MY_FILTERS_WEIGHT)

    client = session or requests.Session()
    response = client.get(
        f"{str(base_url).rstrip('/')}/api/v3/myFilters",
        params=params,
        headers={
            "X-MBX-APIKEY": key,
            "user-agent": "GARIBALDI-MARKET-ORACLE/1.0",
        },
        timeout=max(0.5, float(timeout_seconds)),
    )
    if getattr(response, "status_code", None) == 429:
        retry_after = getattr(response, "headers", {}).get("Retry-After")
        raise RuntimeError(
            f"BINANCE_US_RATE_LIMITED retry_after={retry_after or 'unknown'}"
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise RuntimeError("BINANCE_US_MY_FILTERS_INVALID")
    return dict(payload)


def normalize_my_filters(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize signed /api/v3/myFilters into keyed filter groups."""

    def keyed(items: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(items, list):
            return {}
        return {
            str(item.get("filterType") or ""): dict(item)
            for item in items
            if isinstance(item, Mapping) and item.get("filterType")
        }

    asset_filters = payload.get("assetFilters")
    max_asset: dict[str, dict[str, Any]] = {}
    if isinstance(asset_filters, list):
        for item in asset_filters:
            if not isinstance(item, Mapping) or item.get("filterType") != "MAX_ASSET":
                continue
            asset = str(item.get("asset") or "").upper().strip()
            if asset:
                max_asset[asset] = dict(item)

    return {
        "exchange_filters": keyed(payload.get("exchangeFilters")),
        "symbol_filters": keyed(payload.get("symbolFilters")),
        "max_asset_filters": max_asset,
    }


def validate_2026_order_filters(
    *,
    base_asset: str,
    quote_asset: str,
    quantity: Any,
    price: Any | None,
    my_filters: Mapping[str, Any],
    open_order_lists: int = 0,
    is_order_list: bool = False,
) -> dict[str, Any]:
    """Preflight Binance.US filters introduced by the 2026-08-31 upgrade."""
    qty = _decimal(quantity)
    px = _decimal(price) if price is not None else None
    if qty is None or qty <= 0:
        return {"ok": False, "reason": "BINANCE_US_QUANTITY_INVALID"}
    if price is not None and (px is None or px <= 0):
        return {"ok": False, "reason": "BINANCE_US_PRICE_INVALID"}

    normalized = normalize_my_filters(my_filters)
    symbol_filters = normalized["symbol_filters"]
    if is_order_list:
        order_list_filter = symbol_filters.get("MAX_NUM_ORDER_LISTS")
        if order_list_filter:
            raw_limit = order_list_filter.get("maxNumOrderLists")
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "reason": "BINANCE_US_MAX_NUM_ORDER_LISTS_INVALID",
                }
            if open_order_lists >= limit:
                return {
                    "ok": False,
                    "reason": "BINANCE_US_MAX_NUM_ORDER_LISTS_EXCEEDED",
                    "limit": limit,
                    "open_order_lists": open_order_lists,
                }

    base = str(base_asset or "").upper().strip()
    quote = str(quote_asset or "").upper().strip()
    for asset, filter_payload in normalized["max_asset_filters"].items():
        limit = _decimal(filter_payload.get("limit"))
        if limit is None or limit < 0:
            return {
                "ok": False,
                "reason": "BINANCE_US_MAX_ASSET_FILTER_INVALID",
                "asset": asset,
            }
        if asset == base:
            transacted = qty
        elif asset == quote:
            if px is None:
                return {
                    "ok": False,
                    "reason": "BINANCE_US_MAX_ASSET_REQUIRES_NOTIONAL",
                    "asset": asset,
                }
            transacted = qty * px
        else:
            continue
        if transacted > limit:
            return {
                "ok": False,
                "reason": "BINANCE_US_MAX_ASSET_EXCEEDED",
                "asset": asset,
                "limit": str(limit),
                "transacted": str(transacted),
            }

    return {"ok": True, "reason": "BINANCE_US_2026_FILTERS_CONFIRMED"}


def normalize_execution_report(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize order lifecycle events, including 2026 expiryReason/eR semantics."""
    if str(event.get("e") or "") != "executionReport":
        return None
    execution_type = str(event.get("x") or "").upper().strip()
    order_status = str(event.get("X") or "").upper().strip()
    expiry_reason = event.get("eR")
    expired = (
        order_status == "EXPIRED"
        or execution_type == "EXPIRED"
        or expiry_reason not in (None, "", "NONE")
    )
    return {
        "event": "executionReport",
        "symbol": str(event.get("s") or "").upper().strip(),
        "order_id": event.get("i"),
        "execution_type": execution_type,
        "order_status": order_status,
        "event_time_ms": event.get("E"),
        "expired": expired,
        "expiry_reason": None if expiry_reason in (None, "") else str(expiry_reason),
        "terminal": order_status
        in {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"},
    }


@dataclass
class BinanceUsWebSocketGuard:
    """Protocol guard for Binance.US WebSocket heartbeat and shutdown semantics."""

    clock: Callable[[], float] = time.monotonic
    pong_deadline_seconds: float = WS_PONG_DEADLINE_SECONDS
    last_ping_at: float | None = None
    last_pong_at: float | None = None
    reconnect_required: bool = False
    reconnect_reason: str = ""

    def on_open(self) -> None:
        now = self.clock()
        self.last_ping_at = now
        self.last_pong_at = now
        self.reconnect_required = False
        self.reconnect_reason = ""

    def on_ping(self, send_pong: Callable[[bytes], Any], payload: bytes = b"") -> None:
        """Respond immediately to server ping and record pong delivery."""
        now = self.clock()
        self.last_ping_at = now
        send_pong(payload)
        self.last_pong_at = self.clock()

    def on_text_event(self, event: Mapping[str, Any]) -> bool:
        if str(event.get("e") or "") == "serverShutdown":
            self.reconnect_required = True
            self.reconnect_reason = "BINANCE_US_SERVER_SHUTDOWN"
            return True
        return False

    def heartbeat_ok(self) -> bool:
        if self.reconnect_required:
            return False
        if self.last_ping_at is None or self.last_pong_at is None:
            return True
        if self.last_pong_at >= self.last_ping_at:
            return True
        return (self.clock() - self.last_ping_at) < self.pong_deadline_seconds

    def require_reconnect_if_stale(self) -> bool:
        if self.heartbeat_ok():
            return False
        if not self.reconnect_required:
            self.reconnect_required = True
            self.reconnect_reason = "BINANCE_US_PONG_DEADLINE_EXCEEDED"
        return True
