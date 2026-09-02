from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import robinhood_crypto_api as rh


_MAX_PAGES = 100


def _same_host_relative_path(base_url: str, next_link: str) -> str:
    """Convert a Robinhood pagination link into the exact signed relative path."""
    text = str(next_link or "").strip()
    if not text:
        raise RuntimeError("empty Robinhood pagination link")
    parsed = urlsplit(text)
    base = urlsplit(str(base_url or ""))
    if parsed.scheme or parsed.netloc:
        if parsed.scheme.lower() != base.scheme.lower() or parsed.netloc.lower() != base.netloc.lower():
            raise RuntimeError("Robinhood pagination host mismatch")
        path = parsed.path
        query = parsed.query
    else:
        relative = urlsplit(text)
        path = relative.path
        query = relative.query
    if not path.startswith("/api/"):
        raise RuntimeError("Robinhood pagination path invalid")
    return f"{path}?{query}" if query else path


def _paginated_results(client: Any, initial_path: str, *, max_pages: int = _MAX_PAGES) -> list[dict[str, Any]]:
    """Read all pages without broadening access beyond the configured Robinhood host."""
    path = str(initial_path or "").strip()
    if not path.startswith("/api/"):
        raise RuntimeError("Robinhood initial pagination path invalid")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _ in range(max(1, int(max_pages))):
        if path in seen:
            raise RuntimeError("Robinhood pagination loop detected")
        seen.add(path)
        payload = client.request("GET", path)
        records.extend(client._results(payload))
        if not isinstance(payload, dict):
            return records
        next_link = str(payload.get("next") or "").strip()
        if not next_link:
            return records
        path = _same_host_relative_path(client.base_url, next_link)
    raise RuntimeError("Robinhood pagination page limit exceeded")


def install_robinhood_pagination_compat() -> None:
    """Install complete fail-closed pagination for capital-relevant v2 reads.

    Robinhood v2 trading pairs, holdings, and orders are paginated. Reading only
    the first page can make a supported symbol look non-tradable, omit a holding
    from live-capital exposure, or omit an order from reconciliation. This patch
    only changes read completeness; it never submits, previews, or cancels orders.
    """
    if getattr(rh, "_oracle_pagination_compat_installed", False):
        return

    def paginated_trading_pairs(self):
        records = _paginated_results(self, "/api/v2/crypto/trading/trading_pairs/")
        return [rh.parse_trading_pair(item) for item in records]

    def paginated_holdings(self, account_number: str):
        from urllib.parse import urlencode

        query = urlencode({"account_number": str(account_number or "").strip()})
        return _paginated_results(self, f"/api/v2/crypto/trading/holdings/?{query}")

    def paginated_orders(self, account_number: str):
        from urllib.parse import urlencode

        query = urlencode({"account_number": str(account_number or "").strip()})
        return _paginated_results(self, f"/api/v2/crypto/trading/orders/?{query}")

    rh.RobinhoodCryptoClient.trading_pairs = paginated_trading_pairs
    rh.RobinhoodCryptoClient.holdings = paginated_holdings
    rh.RobinhoodCryptoClient.orders = paginated_orders
    rh._oracle_pagination_compat_installed = True
