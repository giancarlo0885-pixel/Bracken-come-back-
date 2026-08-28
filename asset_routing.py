from __future__ import annotations

import os


def normalize_symbol(value: object) -> str:
    return str(value or "").upper().strip()


US_LISTED_SUFFIXES = {".US"}
FOREIGN_SUFFIXES = {
    ".AX", ".PA", ".AS", ".SW", ".MI", ".MC", ".ST", ".CO", ".HE", ".OL",
    ".T", ".HK", ".SS", ".SZ", ".NS", ".BO", ".JO", ".SA", ".MX", ".TA",
    ".L", ".DE", ".TO", ".V",
}
US_EXCHANGES = {"", "US", "USA", "NASDAQ", "NYSE", "NYSEAMERICAN", "NYSE ARCA", "NYSEARCA", "AMEX", "BATS", "CBOE"}


def infer_asset_class(symbol: object, market: str = "") -> str:
    text = normalize_symbol(symbol)
    market_text = str(market or "").lower()
    if market_text == "crypto" or text.endswith("-USD") or "/" in text:
        return "crypto"
    if market_text == "forex" or text.endswith("=X"):
        return "forex"
    if market_text == "commodity" or text in {"GC=F", "SI=F", "CL=F", "NG=F"}:
        return "commodity"
    if market_text == "index" or text.startswith("^"):
        return "index"
    if "." in text and not text.endswith(".US"):
        return "international_equity"
    if text in {"SPY", "QQQ", "IWM", "DIA", "RSP", "VTV", "VUG"} or text.startswith(("XL", "IBB", "IHI", "SMH")):
        return "etf"
    return "stock"


def market_scope() -> str:
    return os.getenv("MARKET_SCOPE", "US_CRYPTO").strip().upper() or "US_CRYPTO"


def is_us_listed_symbol(symbol: object, exchange: object = "", region: object = "") -> bool:
    text = normalize_symbol(symbol)
    if not text:
        return False
    if infer_asset_class(text) in {"crypto", "forex", "commodity", "index"}:
        return False
    if any(text.endswith(suffix) for suffix in FOREIGN_SUFFIXES):
        return False
    if text.endswith(".US"):
        return True
    if "." in text:
        return False
    exchange_text = normalize_symbol(exchange)
    region_text = normalize_symbol(region)
    if exchange_text and exchange_text not in US_EXCHANGES:
        return False
    if region_text and region_text not in {"UNITED STATES", "US", "USA", "NORTH AMERICA"}:
        return False
    return True


def is_in_market_scope(symbol: object, market: str = "", exchange: object = "", region: object = "") -> bool:
    scope = market_scope()
    asset_class = infer_asset_class(symbol, market)
    if scope != "US_CRYPTO":
        return True
    if asset_class == "crypto":
        return True
    if asset_class in {"stock", "etf"}:
        return is_us_listed_symbol(symbol, exchange, region)
    return False
