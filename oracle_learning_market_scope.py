"""Validated SQL scope for market-owned paper learning passes."""


def learning_market_filter(market: str | None, column: str = "market") -> tuple[str, tuple[str, ...]]:
    # None preserves explicitly requested shared retrospective diagnostics.
    if market is None:
        return "", ()
    if market not in {"cash", "crypto"}:
        raise ValueError("learning market must be cash or crypto")
    if column not in {"market", "d.market", "e.market"}:
        raise ValueError("unsupported learning market column")
    return f" AND {column}=%s", (market,)
