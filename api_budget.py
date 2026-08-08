from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


PRIORITY_ORDER = {
    "open_position": 1,
    "proposed_trade": 2,
    "top_ranked_opportunity": 3,
    "deep_research_candidate": 4,
    "background_discovery": 5,
}


@dataclass
class ProviderBudget:
    provider: str
    capability: str
    hourly_limit: int
    daily_limit: int
    requests_this_hour: int = 0
    requests_this_day: int = 0
    rate_limit_responses: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    fallback_usage: int = 0
    estimated_api_cost: float = 0.0
    capability_cooldown_seconds: int = 0

    def allow(self, priority: str) -> bool:
        if self.capability_cooldown_seconds > 0:
            return False
        if self.requests_this_hour >= self.hourly_limit:
            return PRIORITY_ORDER.get(priority, 99) <= PRIORITY_ORDER["top_ranked_opportunity"]
        return self.requests_this_day < self.daily_limit

    def record(self, *, cache_hit: bool = False, fallback: bool = False, cost: float = 0.0, rate_limited: bool = False) -> None:
        self.requests_this_hour += 0 if cache_hit else 1
        self.requests_this_day += 0 if cache_hit else 1
        self.cache_hits += 1 if cache_hit else 0
        self.cache_misses += 0 if cache_hit else 1
        self.fallback_usage += 1 if fallback else 0
        self.estimated_api_cost += max(0.0, cost)
        self.rate_limit_responses += 1 if rate_limited else 0


def budget_snapshot(budgets: list[ProviderBudget]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    return [{**budget.__dict__, "checked_at": now} for budget in budgets]
