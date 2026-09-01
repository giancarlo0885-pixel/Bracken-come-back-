from __future__ import annotations

from typing import Any

import robinhood_crypto_api as rh


_MAX_PREFLIGHT_QUOTE_PROBES = 5


def estimated_price_side(side: str) -> str:
    """Normalize order intent to Robinhood's documented estimated-price book side."""
    value = str(side or "").strip().lower()
    mapped = {"buy": "ask", "sell": "bid", "bid": "bid", "ask": "ask", "both": "both"}.get(value)
    if mapped is None:
        raise ValueError("Robinhood estimated-price side must be buy/sell/bid/ask/both")
    return mapped


def normalized_quote_fields(quote: dict[str, Any]) -> dict[str, Any]:
    """Normalize documented Robinhood quote shapes without synthesizing a price.

    v2 uses bid/ask. Older read-only responses use the spread-inclusive bid/ask
    names. Both are broker-provided executable-side prices, so preserving those
    values is safe; a one-sided or non-positive quote still fails closed in
    robinhood_crypto_api.best_bid_ask.
    """
    normalized = dict(quote or {})
    if normalized.get("bid") in (None, "") and normalized.get("bid_price") in (None, ""):
        legacy_bid = normalized.get("bid_inclusive_of_sell_spread")
        if legacy_bid not in (None, ""):
            normalized["bid"] = legacy_bid
    if normalized.get("ask") in (None, "") and normalized.get("ask_price") in (None, ""):
        legacy_ask = normalized.get("ask_inclusive_of_buy_spread")
        if legacy_ask not in (None, ""):
            normalized["ask"] = legacy_ask
    return normalized


def install_robinhood_quote_compat() -> None:
    """Install read-only Robinhood quote compatibility and preflight resilience.

    This does not enable submission, relax spread/divergence checks, or invent
    missing prices. It only normalizes broker-provided quote field names, maps
    buy/sell intent to the documented estimated-price bid/ask side, and lets the
    startup connectivity probe try another API-tradable USD pair if one symbol
    returns no usable best bid/ask.
    """
    if getattr(rh, "_oracle_quote_compat_installed", False):
        return

    original_best_bid_ask = rh.best_bid_ask
    original_estimated_price = rh.RobinhoodCryptoClient.estimated_price
    original_preflight = rh.preflight

    def compatible_best_bid_ask(quote: dict[str, Any]):
        return original_best_bid_ask(normalized_quote_fields(quote))

    def compatible_estimated_price(self, symbol: str, side: str, quantity: Any):
        return original_estimated_price(self, symbol, estimated_price_side(side), quantity)

    def compatible_preflight(client=None, journal=None):
        result = original_preflight(client, journal)
        if result.get("QUOTE CHECK") == "PASS":
            return result
        if result.get("ROBINHOOD AUTH") != "PASS" or result.get("CRYPTO STATUS") != "PASS":
            return result

        active_client = client or rh.RobinhoodCryptoClient()
        try:
            pairs = active_client.trading_pairs()
            symbols = [
                str(pair.get("symbol") or "").upper().strip()
                for pair in pairs
                if pair.get("tradable") and str(pair.get("symbol") or "").upper().endswith("-USD")
            ]
            symbols = list(dict.fromkeys(symbol for symbol in symbols if symbol))
            symbols.sort(key=lambda symbol: (symbol != "BTC-USD", symbol))
            attempted: list[str] = []
            for symbol in symbols[:_MAX_PREFLIGHT_QUOTE_PROBES]:
                attempted.append(symbol)
                quotes = active_client.best_bid_ask_quotes(symbol)
                quote = next(
                    (
                        item
                        for item in quotes
                        if str(item.get("symbol") or "").upper().strip() == symbol
                    ),
                    None,
                )
                if quote and compatible_best_bid_ask(quote) is not None:
                    result["QUOTE CHECK"] = "PASS"
                    result["QUOTE PROBE SYMBOL"] = symbol
                    result["QUOTE CHECK REASON"] = "VALID_TRADABLE_USD_PAIR"
                    break
            if result.get("QUOTE CHECK") != "PASS":
                result["QUOTE CHECK REASON"] = "NO_VALID_BROKER_BID_ASK"
                result["QUOTE PROBE COUNT"] = len(attempted)
        except Exception as exc:
            result["QUOTE CHECK REASON"] = f"QUOTE_PROBE_{exc.__class__.__name__.upper()}"

        preflight_passed = all(
            result.get(key) == "PASS"
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
        result["LIVE TRADING ARMED/DISARMED"] = (
            "ARMED" if rh.live_arming_status(preflight_passed)["armed"] else "DISARMED"
        )
        return result

    rh.best_bid_ask = compatible_best_bid_ask
    rh.RobinhoodCryptoClient.estimated_price = compatible_estimated_price
    rh.preflight = compatible_preflight
    rh._oracle_quote_compat_installed = True
