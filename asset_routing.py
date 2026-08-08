from __future__ import annotations


def normalize_symbol(value: object) -> str:
    return str(value or "").upper().strip()


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
