from __future__ import annotations

import os
from typing import Any


def _truthy(name: str, default: str = "true") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() == "true"


def _max_symbols(provider_count: int = 0, static_count: int = 0) -> int:
    """Return the surveillance ceiling.

    A configured positive ceiling is honored.  Zero/"all"/"auto" means retain
    the complete provider-reported USD universe instead of silently truncating
    discovery to a legacy watchlist-sized cap.
    """
    raw = str(os.getenv("CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS", "auto") or "auto").strip().lower()
    if raw in {"auto", "all", "0", "none", "unlimited"}:
        return max(1, int(provider_count or 0), int(static_count or 0))
    try:
        value = int(raw)
    except ValueError:
        return max(1, int(provider_count or 0), int(static_count or 0))
    return max(1, value)


def install_crypto_dynamic_universe_runtime(worker: Any) -> bool:
    """Expand the crypto discovery seed from broker-reported tradable USD pairs.

    This changes discovery only. It does not mark a pair capital-qualified and it
    does not bypass quote, liquidity, spread, model, risk or execution gates.
    Existing static/core symbols remain first and are never removed here.
    """
    if getattr(worker, "_crypto_dynamic_universe_runtime_installed", False):
        return False
    if not _truthy("CRYPTO_DYNAMIC_UNIVERSE_ENABLED", "true"):
        worker.log.info("CRYPTO_DYNAMIC_UNIVERSE | enabled=OFF")
        return False

    provider = getattr(worker, "_robinhood_current_marketdata_provider", None)
    if provider is None:
        return False

    try:
        provider_symbols = sorted(
            {
                str(symbol or "").upper().strip()
                for symbol in provider.tradable_symbols()
                if str(symbol or "").upper().strip().endswith("-USD")
            }
        )
    except Exception as exc:
        worker.log.info(
            "CRYPTO_DYNAMIC_UNIVERSE | status=DISCOVERY_UNAVAILABLE | reason=%s",
            exc.__class__.__name__,
        )
        return False

    import config

    watchlist = getattr(config, "CRYPTO_WATCHLIST", None)
    if not isinstance(watchlist, dict):
        return False

    existing = list(watchlist.keys())
    added: list[str] = []
    limit = _max_symbols(len(provider_symbols), len(watchlist))
    for symbol in provider_symbols:
        if len(watchlist) >= limit:
            break
        if symbol in watchlist:
            continue
        # Symbol identity is retained as the display name until a metadata
        # provider supplies a richer asset name. This avoids guessing identity.
        watchlist[symbol] = symbol.removesuffix("-USD")
        added.append(symbol)

    watchlists = getattr(config, "WATCHLISTS", None)
    if isinstance(watchlists, dict):
        watchlists["crypto"] = watchlist

    worker._crypto_dynamic_universe_runtime_installed = True
    worker._crypto_dynamic_universe = {
        "static_seed_count": len(existing),
        "broker_tradable_count": len(provider_symbols),
        "provider_coverage_count": sum(1 for symbol in provider_symbols if symbol in watchlist),
        "added_count": len(added),
        "active_seed_count": len(watchlist),
        "added_symbols": added,
    }
    worker.log.info(
        "CRYPTO_DYNAMIC_UNIVERSE | status=ACTIVE | static_seed=%d | broker_tradable=%d | added=%d | active_seed=%d | surveillance_limit=%d | execution_authorization=UNCHANGED",
        len(existing),
        len(provider_symbols),
        len(added),
        len(watchlist),
        limit,
    )
    return True
