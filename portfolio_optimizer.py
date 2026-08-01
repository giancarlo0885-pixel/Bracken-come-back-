from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from config import (
    MAX_POSITION_FRACTION,
    MIN_CASH_RESERVE_PCT,
    PENNY_STOCK_MAX_PORTFOLIO_PCT,
)


class PortfolioState(str, Enum):
    DEFENSIVE = "defensive"
    BALANCED = "balanced"
    GROWTH = "growth"
    AGGRESSIVE = "aggressive"
    CAPITAL_PRESERVATION = "capital preservation"


@dataclass
class PortfolioConstraints:
    maximum_position_size: float = MAX_POSITION_FRACTION
    maximum_sector_exposure: float = 0.30
    maximum_country_exposure: float = 0.60
    maximum_correlated_exposure: float = 0.35
    minimum_cash_reserve: float = MIN_CASH_RESERVE_PCT
    maximum_speculative_exposure: float = 0.08
    maximum_penny_stock_exposure: float = PENNY_STOCK_MAX_PORTFOLIO_PCT
    maximum_daily_turnover: float = 0.20
    maximum_new_entries_per_day: int = 3
    leverage_allowed: bool = False


def portfolio_fit_score(
    *,
    symbol: str,
    candidate: dict[str, Any],
    holdings: list[dict[str, Any]],
    constraints: PortfolioConstraints | None = None,
) -> tuple[float, list[str]]:
    constraints = constraints or PortfolioConstraints()
    equity = max(1.0, float(candidate.get("portfolio_equity") or 1.0))
    proposed_value = max(0.0, float(candidate.get("suggested_value") or 0.0))
    sector = str(candidate.get("sector") or "unknown")
    country = str(candidate.get("country") or "unknown")
    asset_class = str(candidate.get("asset_class") or "stock")
    sector_value = sum(float(item.get("market_value") or 0.0) for item in holdings if str(item.get("sector") or "unknown") == sector)
    country_value = sum(float(item.get("market_value") or 0.0) for item in holdings if str(item.get("country") or "unknown") == country)
    speculative_value = sum(float(item.get("market_value") or 0.0) for item in holdings if bool(item.get("speculative")))
    reasons: list[str] = []
    score = 100.0
    if proposed_value / equity > constraints.maximum_position_size:
        score -= 35
        reasons.append("position size would be too large")
    if (sector_value + proposed_value) / equity > constraints.maximum_sector_exposure:
        score -= 20
        reasons.append("sector exposure would be high")
    if (country_value + proposed_value) / equity > constraints.maximum_country_exposure:
        score -= 15
        reasons.append("country exposure would be high")
    if asset_class in {"penny_stock", "speculative"} and (speculative_value + proposed_value) / equity > constraints.maximum_speculative_exposure:
        score -= 30
        reasons.append("speculative exposure would be high")
    if asset_class == "penny_stock" and proposed_value / equity > constraints.maximum_penny_stock_exposure:
        score -= 40
        reasons.append("penny-stock exposure would exceed the pilot limit")
    return max(0.0, min(100.0, score)), reasons or ["portfolio fit is acceptable"]
