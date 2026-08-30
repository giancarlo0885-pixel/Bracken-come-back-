from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from config import (
    CRYPTO_MARGIN_INTEREST_APR,
    CRYPTO_MAINTENANCE_MARGIN_PCT,
    CRYPTO_PAPER_LEVERAGE,
    CRYPTO_STARTING_BALANCE,
    PAPER_BROKER_MODE,
    PAPER_BROKER_PROFILE,
    STOCK_MARGIN_INTEREST_APR,
    STOCK_MAINTENANCE_MARGIN_PCT,
    STOCK_PAPER_LEVERAGE,
    STOCK_STARTING_BALANCE,
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if result != result else result
    except (TypeError, ValueError):
        return default


def _small_account_profile(profile: Any | None = None) -> bool:
    text = str(profile if profile is not None else PAPER_BROKER_PROFILE).strip().lower()
    return text.startswith("small-account") or text in {"cash-paper", "robinhood-cash-paper"}


def market_starting_capital(market: str) -> float:
    return float(CRYPTO_STARTING_BALANCE if str(market).lower() == "crypto" else STOCK_STARTING_BALANCE)


def market_leverage_limit(market: str) -> float:
    if not PAPER_BROKER_MODE:
        return 1.0
    configured = float(CRYPTO_PAPER_LEVERAGE if str(market).lower() == "crypto" else STOCK_PAPER_LEVERAGE)
    # New small-account portfolios inherit the configured profile and are
    # deliberately cash-only. Existing explicit institutional profiles retain
    # their configured leverage semantics.
    if _small_account_profile():
        return 1.0
    return max(1.0, configured)


def market_maintenance_margin_pct(market: str) -> float:
    return float(
        CRYPTO_MAINTENANCE_MARGIN_PCT
        if str(market).lower() == "crypto"
        else STOCK_MAINTENANCE_MARGIN_PCT
    )


def market_margin_interest_apr(market: str) -> float:
    return float(CRYPTO_MARGIN_INTEREST_APR if str(market).lower() == "crypto" else STOCK_MARGIN_INTEREST_APR)


@dataclass(frozen=True)
class PaperBrokerAccount:
    market: str
    broker_profile: str
    starting_capital: float
    cash: float
    positions_value: float
    margin_debt: float
    equity: float
    gross_exposure: float
    leverage_limit: float
    leverage_used: float
    buying_power: float
    maintenance_requirement: float
    excess_liquidity: float
    margin_utilization_pct: float
    margin_call: bool
    margin_interest_accrued: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_account(
    market: str,
    portfolio: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
) -> PaperBrokerAccount:
    market = str(market or "cash").lower()
    portfolio = portfolio or {}
    positions = positions or []

    starting_capital = max(
        0.01,
        _number(portfolio.get("starting_balance"), market_starting_capital(market)),
    )
    cash = _number(portfolio.get("cash"), starting_capital)
    margin_debt = max(0.0, _number(portfolio.get("margin_debt"), 0.0))
    positions_value = sum(
        abs(_number(position.get("quantity")))
        * max(
            0.0,
            _number(
                position.get("current_price"),
                _number(position.get("average_price"), _number(position.get("entry_price"))),
            ),
        )
        for position in positions
    )
    gross_exposure = positions_value
    equity = cash + positions_value - margin_debt
    configured_leverage = max(1.0, _number(portfolio.get("leverage_limit"), market_leverage_limit(market)))
    explicit_profile = str(portfolio.get("broker_profile") or "").strip()
    broker_profile = explicit_profile or "institutional-paper"
    # Only an explicit persisted small-account profile can override an existing
    # account's leverage. This preserves generic/legacy account calculations
    # while keeping production's persisted small-account-paper rows cash-only.
    leverage_limit = 1.0 if explicit_profile and _small_account_profile(explicit_profile) else configured_leverage
    leverage_used = gross_exposure / equity if equity > 0 else leverage_limit
    buying_power = max(0.0, equity * leverage_limit - gross_exposure) if equity > 0 else 0.0
    maintenance_requirement = gross_exposure * market_maintenance_margin_pct(market)
    excess_liquidity = equity - maintenance_requirement
    margin_utilization_pct = (
        min(999.0, max(0.0, leverage_used / leverage_limit * 100.0))
        if leverage_limit > 0
        else 0.0
    )
    margin_call = equity <= 0 or excess_liquidity < 0

    return PaperBrokerAccount(
        market=market,
        broker_profile=broker_profile,
        starting_capital=round(starting_capital, 8),
        cash=round(cash, 8),
        positions_value=round(positions_value, 8),
        margin_debt=round(margin_debt, 8),
        equity=round(equity, 8),
        gross_exposure=round(gross_exposure, 8),
        leverage_limit=round(leverage_limit, 4),
        leverage_used=round(leverage_used, 4),
        buying_power=round(buying_power, 8),
        maintenance_requirement=round(maintenance_requirement, 8),
        excess_liquidity=round(excess_liquidity, 8),
        margin_utilization_pct=round(margin_utilization_pct, 2),
        margin_call=bool(margin_call),
        margin_interest_accrued=round(max(0.0, _number(portfolio.get("margin_interest_accrued"))), 8),
    )


def allocate_purchase(
    *,
    cash: float,
    margin_debt: float,
    trade_value: float,
    cash_reserve: float,
) -> tuple[float, float, float, float]:
    """Use excess cash first, then paper margin.

    Returns (new_cash, new_margin_debt, cash_used, borrowed).
    """
    cash = _number(cash)
    margin_debt = max(0.0, _number(margin_debt))
    trade_value = max(0.0, _number(trade_value))
    cash_reserve = max(0.0, _number(cash_reserve))
    cash_used = min(max(0.0, cash - cash_reserve), trade_value)
    borrowed = max(0.0, trade_value - cash_used)
    return cash - cash_used, margin_debt + borrowed, cash_used, borrowed


def allocate_sale(
    *,
    cash: float,
    margin_debt: float,
    sale_value: float,
) -> tuple[float, float, float]:
    """Repay paper margin first, then credit remaining sale proceeds to cash."""
    cash = _number(cash)
    margin_debt = max(0.0, _number(margin_debt))
    sale_value = max(0.0, _number(sale_value))
    repayment = min(margin_debt, sale_value)
    return cash + (sale_value - repayment), margin_debt - repayment, repayment


def accrued_interest(
    *,
    market: str,
    margin_debt: float,
    last_updated: Any,
    now: datetime | None = None,
) -> float:
    """Return realistic paper financing cost since the last accrual timestamp."""
    debt = max(0.0, _number(margin_debt))
    if debt <= 0:
        return 0.0
    now = now or datetime.now(timezone.utc)
    try:
        then = datetime.fromisoformat(str(last_updated).replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
    except Exception:
        return 0.0
    elapsed_seconds = max(0.0, (now - then).total_seconds())
    return debt * market_margin_interest_apr(market) * elapsed_seconds / (365.0 * 86400.0)
