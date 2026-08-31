from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import base64
import json
import re
import time
import uuid
from typing import Any, Callable
from urllib.parse import urlencode

import requests

from config import (
    ENABLE_AUTOTRADE,
    ENABLE_BROKER_SUBMISSION,
    ENABLE_CRYPTO_AUTOTRADE,
    GLOBAL_KILL_SWITCH,
    LIVE_ORDER_APPROVAL_MODE,
    LIVE_TRADING_ARMED,
    ROBINHOOD_CRYPTO_API_KEY,
    ROBINHOOD_CRYPTO_BASE_URL,
    ROBINHOOD_CRYPTO_ENABLED,
    ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64,
)


ORDER_STATES = {
    "CREATED",
    "PREFLIGHT",
    "PREVIEWED",
    "SUBMITTING",
    "SUBMITTED",
    "OPEN",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "UNKNOWN_RECONCILE_REQUIRED",
}
SECRET_KEYS = {"api_key", "private_key", "signature", "authorization", "x-api-key", "x-signature"}
DEFAULT_BROKER_PRICE_TOLERANCE_PCT = Decimal("0.75")
DEFAULT_BROKER_MAX_SPREAD_PCT = Decimal("1.50")


def _redact(value: Any) -> str:
    text = str(value)
    for secret in (ROBINHOOD_CRYPTO_API_KEY, ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(
        r"(?i)(api[_-]?key|apikey|private[_-]?key|signature|authorization|x-api-key|x-signature)=([^&\s]+)",
        r"\1=[REDACTED]",
        text,
    )
    return text


def _json_body(body: Any) -> str:
    if body in (None, ""):
        return ""
    if isinstance(body, str):
        return body
    return json.dumps(body, separators=(",", ":"), sort_keys=True)


def signing_message(api_key: str, timestamp: str, path: str, method: str, body: Any = "") -> bytes:
    return f"{api_key}{timestamp}{path}{method.upper()}{_json_body(body)}".encode("utf-8")


def sign_message(
    private_key_base64: str,
    message: bytes,
    signer: Callable[[bytes, bytes], bytes] | None = None,
) -> str:
    try:
        private_key = base64.b64decode(private_key_base64, validate=True)
    except Exception as exc:
        raise ValueError("ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64 is malformed") from exc
    if signer is not None:
        return base64.b64encode(signer(private_key, message)).decode("ascii")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except Exception as exc:
        raise RuntimeError("cryptography Ed25519 support is required for Robinhood signing") from exc
    try:
        signature = Ed25519PrivateKey.from_private_bytes(private_key).sign(message)
    except Exception as exc:
        raise ValueError("ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64 is invalid") from exc
    return base64.b64encode(signature).decode("ascii")


def signed_headers(
    api_key: str,
    private_key_base64: str,
    path: str,
    method: str,
    body: Any = "",
    *,
    now: float | None = None,
    signer: Callable[[bytes, bytes], bytes] | None = None,
) -> dict[str, str]:
    timestamp = str(int(now if now is not None else time.time()))
    message = signing_message(api_key, timestamp, path, method, body)
    return {
        "x-api-key": api_key,
        "x-timestamp": timestamp,
        "x-signature": sign_message(private_key_base64, message, signer=signer),
        "content-type": "application/json",
    }


def timestamp_is_fresh(timestamp: str, *, now: float | None = None, max_age_seconds: int = 30) -> bool:
    try:
        value = int(timestamp)
    except Exception:
        return False
    return abs((now if now is not None else time.time()) - value) <= max_age_seconds


def decimal_down(value: Any, increment: Any) -> Decimal:
    number = Decimal(str(value))
    step = Decimal(str(increment))
    if step <= 0:
        return number
    return (number / step).to_integral_value(rounding=ROUND_DOWN) * step


def parse_trading_pair(raw: dict[str, Any]) -> dict[str, Any]:
    symbol = str(raw.get("symbol") or raw.get("id") or raw.get("asset_symbol") or "").upper()
    status = str(raw.get("status") or "").lower()
    is_api_tradable = bool(raw.get("is_api_tradable"))
    return {
        "symbol": symbol,
        "status": status,
        "is_api_tradable": is_api_tradable,
        "asset_increment": str(raw.get("asset_increment") or "0.00000001"),
        "quote_increment": str(raw.get("quote_increment") or "0.01"),
        "min_order_amount": Decimal(str(raw.get("min_order_amount") or raw.get("min_order_size") or "0")),
        "max_order_size": Decimal(str(raw.get("max_order_size") or "0")),
        "raw": raw,
        "tradable": bool(symbol and status in {"tradable", "active"} and is_api_tradable),
    }


def best_bid_ask(quote: dict[str, Any]) -> dict[str, Decimal] | None:
    try:
        bid = Decimal(str(quote.get("bid_price") or quote.get("bid") or "0"))
        ask = Decimal(str(quote.get("ask_price") or quote.get("ask") or "0"))
    except Exception:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / Decimal("2")
    spread_pct = ((ask - bid) / mid) * Decimal("100") if mid > 0 else Decimal("100")
    return {"bid": bid, "ask": ask, "mid": mid, "spread_pct": spread_pct}


def validate_broker_market_reference(
    symbol: str,
    oracle_price: Any,
    quote: dict[str, Any],
    *,
    max_price_difference_pct: Any = DEFAULT_BROKER_PRICE_TOLERANCE_PCT,
    max_spread_pct: Any = DEFAULT_BROKER_MAX_SPREAD_PCT,
) -> dict[str, Any]:
    """Fail closed when the Oracle mark disagrees with Robinhood's executable market.

    This is a read-only pre-trade integrity gate. It does not submit an order.
    """
    requested = str(symbol or "").upper().strip()
    quote_symbol = str(quote.get("symbol") or "").upper().strip()
    if not requested or quote_symbol != requested:
        return {"ok": False, "reason": "BROKER_SYMBOL_MISMATCH"}

    book = best_bid_ask(quote)
    if book is None:
        return {"ok": False, "reason": "BROKER_QUOTE_INVALID"}

    try:
        reference = Decimal(str(oracle_price))
        price_tolerance = Decimal(str(max_price_difference_pct))
        spread_limit = Decimal(str(max_spread_pct))
    except Exception:
        return {"ok": False, "reason": "BROKER_REFERENCE_INVALID"}

    if reference <= 0 or price_tolerance < 0 or spread_limit < 0:
        return {"ok": False, "reason": "BROKER_REFERENCE_INVALID"}

    difference_pct = abs(book["mid"] - reference) / book["mid"] * Decimal("100")
    if book["spread_pct"] > spread_limit:
        return {
            "ok": False,
            "reason": "BROKER_SPREAD_TOO_WIDE",
            "difference_pct": float(difference_pct),
            "spread_pct": float(book["spread_pct"]),
            "broker_mid": float(book["mid"]),
        }
    if difference_pct > price_tolerance:
        return {
            "ok": False,
            "reason": "BROKER_PRICE_DIVERGENCE",
            "difference_pct": float(difference_pct),
            "spread_pct": float(book["spread_pct"]),
            "broker_mid": float(book["mid"]),
        }
    return {
        "ok": True,
        "reason": "BROKER_PRICE_CONFIRMED",
        "difference_pct": float(difference_pct),
        "spread_pct": float(book["spread_pct"]),
        "broker_mid": float(book["mid"]),
        "bid": float(book["bid"]),
        "ask": float(book["ask"]),
    }


def validate_order_amount(pair: dict[str, Any], amount: Any, quantity: Any | None = None) -> dict[str, Any]:
    quote_amount = Decimal(str(amount))
    if quote_amount <= 0:
        return {"ok": False, "reason": "MIN_ORDER_NOT_MET"}
    if pair.get("min_order_amount") and quote_amount < Decimal(pair["min_order_amount"]):
        return {"ok": False, "reason": "MIN_ORDER_NOT_MET"}
    if quantity is not None and pair.get("max_order_size") and Decimal(str(quantity)) > Decimal(pair["max_order_size"]):
        return {"ok": False, "reason": "MAX_ORDER_EXCEEDED"}
    return {"ok": True}


def live_arming_status(preflight_passed: bool) -> dict[str, Any]:
    reasons = []
    if not ENABLE_AUTOTRADE:
        reasons.append("ENABLE_AUTOTRADE=false")
    if not ENABLE_CRYPTO_AUTOTRADE:
        reasons.append("ENABLE_CRYPTO_AUTOTRADE=false")
    if not ENABLE_BROKER_SUBMISSION:
        reasons.append("ENABLE_BROKER_SUBMISSION=false")
    if not LIVE_TRADING_ARMED:
        reasons.append("LIVE_TRADING_ARMED=false")
    if GLOBAL_KILL_SWITCH:
        reasons.append("GLOBAL_KILL_SWITCH=true")
    if LIVE_ORDER_APPROVAL_MODE not in {"manual", "preauthorized"}:
        reasons.append("LIVE_ORDER_APPROVAL_MODE invalid")
    if not preflight_passed:
        reasons.append("broker preflight not passed")
    return {"armed": not reasons and LIVE_ORDER_APPROVAL_MODE == "preauthorized", "reasons": reasons}


@dataclass
class OrderJournal:
    records: dict[str, dict[str, Any]] = field(default_factory=dict)

    def create(self, symbol: str, side: str, payload: dict[str, Any]) -> dict[str, Any]:
        client_order_id = str(payload.get("client_order_id") or uuid.uuid4())
        record = {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "state": "CREATED",
            "payload": payload,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.records[client_order_id] = record
        return record

    def transition(self, client_order_id: str, state: str, **extra: Any) -> dict[str, Any]:
        if state not in ORDER_STATES:
            raise ValueError("unknown order state")
        record = self.records[client_order_id]
        record.update(extra)
        record["state"] = state
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        return record

    def unfinished(self) -> list[dict[str, Any]]:
        done = {"FILLED", "CANCELED", "REJECTED"}
        return [record for record in self.records.values() if record.get("state") not in done]

    def has_duplicate(self, symbol: str, side: str) -> bool:
        symbol = str(symbol or "").upper()
        side = str(side or "").upper()
        return any(
            str(record.get("symbol") or "").upper() == symbol
            and str(record.get("side") or "").upper() == side
            and record.get("state") not in {"FILLED", "CANCELED", "REJECTED"}
            for record in self.records.values()
        )


def mark_submit_timeout(journal: OrderJournal, client_order_id: str) -> dict[str, Any]:
    return journal.transition(client_order_id, "UNKNOWN_RECONCILE_REQUIRED", reason="submit timeout; reconcile before retry")


def reconcile_unfinished_orders(
    journal: OrderJournal,
    lookup: Callable[[str], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    reconciled = []
    for record in journal.unfinished():
        client_order_id = str(record.get("client_order_id") or "")
        remote = lookup(client_order_id)
        if not remote:
            reconciled.append(
                journal.transition(client_order_id, "UNKNOWN_RECONCILE_REQUIRED", reason="remote order not found")
            )
            continue
        remote_state = str(remote.get("state") or remote.get("status") or "").upper()
        state = remote_state if remote_state in ORDER_STATES else "UNKNOWN_RECONCILE_REQUIRED"
        reconciled.append(journal.transition(client_order_id, state, remote_order=remote))
    return reconciled


class RobinhoodCryptoClient:
    def __init__(
        self,
        *,
        api_key: str = ROBINHOOD_CRYPTO_API_KEY,
        private_key_base64: str = ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64,
        base_url: str = ROBINHOOD_CRYPTO_BASE_URL,
        session: Any = requests,
        signer: Callable[[bytes, bytes], bytes] | None = None,
    ) -> None:
        self.api_key = api_key
        self.private_key_base64 = private_key_base64
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.signer = signer

    def configured(self) -> dict[str, Any]:
        if not ROBINHOOD_CRYPTO_ENABLED:
            return {"ok": False, "reason": "ROBINHOOD_CRYPTO_ENABLED=false"}
        if not self.api_key:
            return {"ok": False, "reason": "ROBINHOOD_CRYPTO_API_KEY missing"}
        if not self.private_key_base64:
            return {"ok": False, "reason": "ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64 missing"}
        return {"ok": True}

    def request(self, method: str, path: str, body: Any = None) -> Any:
        configured = self.configured()
        if not configured["ok"]:
            raise RuntimeError(configured["reason"])
        payload = _json_body(body)
        headers = signed_headers(self.api_key, self.private_key_base64, path, method, payload, signer=self.signer)
        try:
            response = self.session.request(
                method.upper(),
                f"{self.base_url}{path}",
                data=payload or None,
                headers=headers,
                timeout=15,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise RuntimeError(_redact(exc)) from exc

    @staticmethod
    def _results(data: Any) -> list[dict[str, Any]]:
        records = data.get("results", data) if isinstance(data, dict) else data
        return [item for item in (records or []) if isinstance(item, dict)]

    def trading_pairs(self) -> list[dict[str, Any]]:
        return [parse_trading_pair(item) for item in self._results(self.request("GET", "/api/v2/crypto/trading/trading_pairs/"))]

    def account_details(self) -> dict[str, Any]:
        """Read the first v2 crypto account using Robinhood's documented account schema."""
        records = self._results(self.request("GET", "/api/v2/crypto/trading/accounts/"))
        return records[0] if records else {}

    def holdings(self, account_number: str) -> list[dict[str, Any]]:
        query = urlencode({"account_number": str(account_number or "").strip()})
        return self._results(self.request("GET", f"/api/v2/crypto/trading/holdings/?{query}"))

    def orders(self, account_number: str) -> list[dict[str, Any]]:
        query = urlencode({"account_number": str(account_number or "").strip()})
        return self._results(self.request("GET", f"/api/v2/crypto/trading/orders/?{query}"))

    def best_bid_ask_quotes(self, *symbols: str) -> list[dict[str, Any]]:
        normalized = [str(symbol or "").upper().strip() for symbol in symbols if str(symbol or "").strip()]
        query = urlencode([("symbol", symbol) for symbol in normalized])
        path = "/api/v2/crypto/marketdata/best_bid_ask/"
        if query:
            path = f"{path}?{query}"
        return self._results(self.request("GET", path))

    def estimated_price(self, symbol: str, side: str, quantity: Any) -> list[dict[str, Any]]:
        params = [
            ("symbol", str(symbol or "").upper().strip()),
            ("side", str(side or "").lower().strip()),
            ("quantity", str(quantity)),
        ]
        path = f"/api/v2/crypto/trading/estimated_price/?{urlencode(params)}"
        return self._results(self.request("GET", path))


def buying_power_state(account: dict[str, Any]) -> str:
    """Classify buying power without logging or returning the numeric balance."""
    raw = account.get("buying_power")
    if raw in (None, ""):
        return "MISSING"
    try:
        value = Decimal(str(raw))
    except Exception:
        return "INVALID"
    if not value.is_finite() or value < 0:
        return "INVALID"
    if value == 0:
        return "ZERO"
    return "POSITIVE"


def _buying_power_ok(account: dict[str, Any]) -> bool:
    return buying_power_state(account) == "POSITIVE"


def preflight(client: RobinhoodCryptoClient | None = None, journal: OrderJournal | None = None) -> dict[str, Any]:
    client = client or RobinhoodCryptoClient()
    journal = journal or OrderJournal()
    configured = client.configured()
    result = {
        "ROBINHOOD CONNECTION": "CONFIGURED" if configured["ok"] else "DISCONNECTED",
        "ROBINHOOD AUTH": "NOT_CHECKED",
        "ACCOUNT STATUS": "UNKNOWN",
        "CRYPTO STATUS": "UNKNOWN",
        "TRADABLE PAIR COUNT": 0,
        "QUOTE CHECK": "NOT_RUN",
        "BUYING POWER CHECK": "NOT_RUN",
        "BUYING POWER STATE": "NOT_CHECKED",
        "BUYING POWER CURRENCY": "UNKNOWN",
        "HOLDINGS CHECK": "NOT_RUN",
        "ORDERS CHECK": "NOT_RUN",
        "ORDER PREVIEW CAPABILITY": "DIRECT_API_NO_PREVIEW",
        "LIVE TRADING ARMED/DISARMED": "DISARMED",
        "ORDER JOURNAL": "PASS" if journal is not None else "MISSING",
        "reason": configured.get("reason", ""),
    }
    if not configured["ok"]:
        return result

    try:
        pairs = client.trading_pairs()
        tradable_pairs = [pair for pair in pairs if pair["tradable"]]
        result["ROBINHOOD AUTH"] = "PASS"
        result["CRYPTO STATUS"] = "PASS" if tradable_pairs else "FAIL"
        result["TRADABLE PAIR COUNT"] = len(tradable_pairs)

        account = client.account_details()
        account_status = str(account.get("status") or "").lower()
        account_number = str(account.get("account_number") or "").strip()
        result["ACCOUNT STATUS"] = "PASS" if account_status == "active" and account_number else "FAIL"
        bp_state = buying_power_state(account)
        result["BUYING POWER STATE"] = bp_state
        result["BUYING POWER CHECK"] = "PASS" if bp_state == "POSITIVE" else "FAIL"
        result["BUYING POWER CURRENCY"] = str(account.get("buying_power_currency") or "UNKNOWN").upper()

        if account_number:
            client.holdings(account_number)
            result["HOLDINGS CHECK"] = "PASS"
            client.orders(account_number)
            result["ORDERS CHECK"] = "PASS"
        else:
            result["HOLDINGS CHECK"] = "FAIL"
            result["ORDERS CHECK"] = "FAIL"

        if tradable_pairs:
            preferred = next((pair for pair in tradable_pairs if pair["symbol"] == "BTC-USD"), tradable_pairs[0])
            quotes = client.best_bid_ask_quotes(preferred["symbol"])
            quote = next(
                (
                    item
                    for item in quotes
                    if str(item.get("symbol") or "").upper().strip() == preferred["symbol"]
                ),
                None,
            )
            result["QUOTE CHECK"] = "PASS" if quote and best_bid_ask(quote) is not None else "FAIL"
        else:
            result["QUOTE CHECK"] = "FAIL"
    except Exception as exc:
        result["ROBINHOOD AUTH"] = "FAIL"
        result["reason"] = _redact(exc)

    preflight_passed = all(
        result[key] == "PASS"
        for key in (
            "ROBINHOOD AUTH",
            "ACCOUNT STATUS",
            "CRYPTO STATUS",
            "QUOTE CHECK",
            "BUYING POWER CHECK",
            "HOLDINGS CHECK",
            "ORDERS CHECK",
            "ORDER JOURNAL",
        )
    )
    result["LIVE TRADING ARMED/DISARMED"] = "ARMED" if live_arming_status(preflight_passed)["armed"] else "DISARMED"
    return result
