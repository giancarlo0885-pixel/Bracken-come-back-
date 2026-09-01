from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
import os
import threading
import time
from typing import Any, Callable


@dataclass(frozen=True)
class BrokerCapitalSnapshot:
    valid: bool
    complete: bool
    buying_power: float
    holdings_value: float
    equity: float
    gross_exposure: float
    buying_power_currency: str
    position_values: dict[str, float]
    tradable_quantities: dict[str, float]
    missing_quotes: tuple[str, ...]
    observed_at: str
    reason: str
    source: str = "robinhood_crypto_v2"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def sizing_allowed(self) -> bool:
        return self.valid and self.complete

    def portfolio_metrics(self) -> dict[str, Any]:
        """Return conservative account metrics consumed by Oracle risk/sizing code."""
        deployable = self.buying_power if self.sizing_allowed else 0.0
        equity = max(0.01, self.equity)
        exposure = max(0.0, self.gross_exposure)
        return {
            "cash": deployable,
            "equity": equity,
            "portfolio_equity": equity,
            "total_equity": equity,
            "buying_power": deployable,
            "buying_power_validated": self.sizing_allowed,
            "invested": exposure,
            "positions_value": exposure,
            "gross_exposure": exposure,
            "leverage_limit": 1.0,
            "leverage_used": (exposure / equity) if equity > 0 else 0.0,
            "margin_debt": 0.0,
            "broker_capital_source": self.source,
            "broker_capital_valid": self.valid,
            "broker_capital_complete": self.complete,
            "broker_capital_reason": self.reason,
            "broker_capital_snapshot_at": self.observed_at,
            "broker_position_values": dict(self.position_values),
            "broker_tradable_quantities": dict(self.tradable_quantities),
            "broker_missing_quotes": list(self.missing_quotes),
        }


def _finite_nonnegative(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _quote_bid(quote: dict[str, Any]) -> float | None:
    for key in ("bid", "bid_price"):
        value = _finite_nonnegative(quote.get(key))
        if value is not None and value > 0:
            return value
    return None


def _empty_snapshot(reason: str) -> BrokerCapitalSnapshot:
    return BrokerCapitalSnapshot(
        valid=False,
        complete=False,
        buying_power=0.0,
        holdings_value=0.0,
        equity=0.0,
        gross_exposure=0.0,
        buying_power_currency="UNKNOWN",
        position_values={},
        tradable_quantities={},
        missing_quotes=(),
        observed_at=datetime.now(timezone.utc).isoformat(),
        reason=reason,
    )


def build_robinhood_capital_snapshot(client: Any) -> BrokerCapitalSnapshot:
    """Reconstruct live crypto capital from Robinhood v2 account + holdings.

    Robinhood v2 exposes account buying power directly. Total crypto equity is
    conservatively reconstructed as buying power plus each holding marked at the
    current broker bid. If any positive holding cannot be marked, new-entry sizing
    fails closed instead of pretending the unpriced exposure does not exist.
    """
    try:
        account = dict(client.account_details() or {})
    except Exception:
        return _empty_snapshot("BROKER_ACCOUNT_UNAVAILABLE")

    account_status = str(account.get("status") or "").strip().lower()
    account_number = str(account.get("account_number") or "").strip()
    currency = str(account.get("buying_power_currency") or "").strip().upper()
    buying_power = _finite_nonnegative(account.get("buying_power"))
    if account_status != "active" or not account_number:
        return _empty_snapshot("BROKER_ACCOUNT_INACTIVE_OR_MISSING")
    if currency != "USD":
        return _empty_snapshot("BROKER_BUYING_POWER_CURRENCY_NOT_USD")
    if buying_power is None:
        return _empty_snapshot("BROKER_BUYING_POWER_INVALID")

    try:
        holdings = list(client.holdings(account_number) or [])
    except Exception:
        snapshot = _empty_snapshot("BROKER_HOLDINGS_UNAVAILABLE")
        return BrokerCapitalSnapshot(
            **{
                **snapshot.to_dict(),
                "valid": True,
                "buying_power": buying_power,
                "equity": buying_power,
                "buying_power_currency": currency,
            }
        )

    quantities: dict[str, float] = {}
    tradable_quantities: dict[str, float] = {}
    for holding in holdings:
        if not isinstance(holding, dict):
            continue
        asset = str(holding.get("asset_code") or "").strip().upper()
        if not asset:
            continue
        total = _finite_nonnegative(holding.get("total_quantity"))
        available = _finite_nonnegative(holding.get("quantity_available_for_trading"))
        if total is None or total <= 0:
            continue
        symbol = f"{asset}-USD"
        quantities[symbol] = quantities.get(symbol, 0.0) + total
        tradable_quantities[symbol] = tradable_quantities.get(symbol, 0.0) + max(0.0, available or 0.0)

    quotes: list[dict[str, Any]] = []
    if quantities:
        try:
            quotes = list(client.best_bid_ask_quotes(*sorted(quantities)) or [])
        except Exception:
            quotes = []
    quote_by_symbol = {
        str(item.get("symbol") or "").strip().upper(): item
        for item in quotes
        if isinstance(item, dict) and item.get("symbol")
    }

    position_values: dict[str, float] = {}
    missing: list[str] = []
    for symbol, quantity in quantities.items():
        bid = _quote_bid(quote_by_symbol.get(symbol, {}))
        if bid is None:
            missing.append(symbol)
            continue
        position_values[symbol] = quantity * bid

    holdings_value = sum(position_values.values())
    complete = not missing
    equity = buying_power + holdings_value
    return BrokerCapitalSnapshot(
        valid=True,
        complete=complete,
        buying_power=buying_power,
        holdings_value=holdings_value,
        equity=equity,
        gross_exposure=holdings_value,
        buying_power_currency=currency,
        position_values=position_values,
        tradable_quantities=tradable_quantities,
        missing_quotes=tuple(sorted(missing)),
        observed_at=datetime.now(timezone.utc).isoformat(),
        reason="BROKER_CAPITAL_VERIFIED" if complete else "BROKER_HOLDING_QUOTE_INCOMPLETE",
    )


class LiveBrokerCapitalProvider:
    def __init__(
        self,
        client_factory: Callable[[], Any] | None = None,
        *,
        ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client_factory = client_factory or self._default_client_factory
        configured_ttl = os.getenv("BROKER_CAPITAL_SNAPSHOT_TTL_SECONDS", "2")
        try:
            parsed_ttl = float(configured_ttl)
        except (TypeError, ValueError):
            parsed_ttl = 2.0
        self.ttl_seconds = max(0.0, parsed_ttl if ttl_seconds is None else float(ttl_seconds))
        self.clock = clock
        self._lock = threading.Lock()
        self._cached: tuple[float, BrokerCapitalSnapshot] | None = None

    @staticmethod
    def _default_client_factory() -> Any:
        from robinhood_crypto_api import RobinhoodCryptoClient

        return RobinhoodCryptoClient()

    def snapshot(self, *, fresh: bool = False) -> BrokerCapitalSnapshot:
        now = self.clock()
        with self._lock:
            if not fresh and self._cached is not None:
                observed, snapshot = self._cached
                if now - observed <= self.ttl_seconds:
                    return snapshot
        try:
            snapshot = build_robinhood_capital_snapshot(self.client_factory())
        except Exception:
            snapshot = _empty_snapshot("BROKER_CAPITAL_REFRESH_FAILED")
        with self._lock:
            self._cached = (now, snapshot)
        return snapshot

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None


def _live_crypto_capital_enabled(market: Any) -> bool:
    enabled = os.getenv("BROKER_CAPITAL_SIZING_ENABLED", "true").strip().lower() == "true"
    execution_mode = os.getenv("EXECUTION_MODE", "paper").strip().lower()
    robinhood_enabled = os.getenv("ROBINHOOD_CRYPTO_ENABLED", "false").strip().lower() == "true"
    return enabled and execution_mode == "live" and robinhood_enabled and str(market or "").strip().lower() == "crypto"


def install_live_broker_capital_sizing(
    oracle_module: Any | None = None,
    *,
    provider: LiveBrokerCapitalProvider | None = None,
) -> None:
    """Make live crypto sizing follow current broker capital, fail-closed.

    The patch is intentionally scoped to Oracle's live crypto runtime. Paper mode
    and stock sizing are untouched. The final allocator remains authoritative for
    reserve, risk, concentration, liquidity, drawdown and minimum-notional gates.
    """
    if oracle_module is None:
        import oracle_bot as oracle_module

    if getattr(oracle_module, "_live_broker_capital_sizing_installed", False):
        return

    provider = provider or LiveBrokerCapitalProvider()
    original_portfolio_equity = oracle_module.portfolio_equity
    original_allocator = oracle_module.adaptive_capital_allocation

    def broker_aware_portfolio_equity(market: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        base = dict(original_portfolio_equity(market, *args, **kwargs) or {})
        if not _live_crypto_capital_enabled(market):
            return base
        snapshot = provider.snapshot(fresh=False)
        base.update(snapshot.portfolio_metrics())
        return base

    def broker_aware_allocator(*args: Any, **kwargs: Any) -> Any:
        market = kwargs.get("market")
        if not _live_crypto_capital_enabled(market):
            return original_allocator(*args, **kwargs)

        # Sizing gets a fresh broker read for every proposed live entry so account
        # growth/losses and prior fills change the next position automatically.
        snapshot = provider.snapshot(fresh=True)
        symbol = str(kwargs.get("symbol") or "").strip().upper()
        kwargs = dict(kwargs)
        kwargs["equity"] = max(0.01, snapshot.equity)
        kwargs["cash"] = snapshot.buying_power if snapshot.sizing_allowed else 0.0
        kwargs["buying_power"] = snapshot.buying_power if snapshot.sizing_allowed else 0.0
        kwargs["buying_power_validated"] = snapshot.sizing_allowed
        kwargs["current_exposure"] = max(
            float(kwargs.get("current_exposure") or 0.0),
            snapshot.gross_exposure,
        )
        kwargs["existing_position_value"] = max(
            float(kwargs.get("existing_position_value") or 0.0),
            float(snapshot.position_values.get(symbol, 0.0)),
        )
        return original_allocator(*args, **kwargs)

    oracle_module.portfolio_equity = broker_aware_portfolio_equity
    oracle_module.adaptive_capital_allocation = broker_aware_allocator
    oracle_module._live_broker_capital_provider = provider
    oracle_module._live_broker_capital_sizing_installed = True
