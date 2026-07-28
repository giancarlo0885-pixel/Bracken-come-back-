from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _age_minutes(value: Any) -> float | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 60.0)


@dataclass(frozen=True)
class OracleOneVerdict:
    approved: bool
    verdict: str
    integrity_score: float
    execution_ceiling: float
    kill_switch_active: bool
    stale_data: bool
    invariant_failures: tuple[str, ...]
    warnings: tuple[str, ...]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["invariant_failures"] = list(self.invariant_failures)
        data["warnings"] = list(self.warnings)
        return data


def finalize_decision(
    signal: Any,
    *,
    recommendation: str,
    opportunity_score: float,
    probability_of_profit: float,
    risk_reward_ratio: float,
    quant: dict[str, Any],
    scenario: dict[str, Any],
    capital: dict[str, Any],
    portfolio_supercomputer: dict[str, Any],
    max_signal_age_minutes: float | None = None,
) -> OracleOneVerdict:
    """Final deterministic guardian for the entire Oracle pipeline.

    It never creates a trade idea. It only verifies that the upstream engines
    agree, the numbers are finite, the data is fresh enough, and execution is
    capped to an explicit approved dollar amount.
    """
    failures: list[str] = []
    warnings: list[str] = []
    kill_switch = _bool_env("ORACLE_KILL_SWITCH", False)
    if kill_switch:
        failures.append("Manual ORACLE_KILL_SWITCH is active.")

    score = _num(opportunity_score, -1.0)
    probability = _num(probability_of_profit, -1.0)
    rr = _num(risk_reward_ratio, -1.0)
    net_ev = _num(quant.get("net_expected_value_pct"), -1.0)
    scenario_ok = bool(scenario.get("approved", False)) and not bool(scenario.get("veto", False))
    capital_ok = bool(capital.get("approved", False))
    portfolio_ok = bool(portfolio_supercomputer.get("approved", False))
    ceiling = max(0.0, _num(portfolio_supercomputer.get("recommended_trade_value"), 0.0))

    if not (0.0 <= score <= 100.0):
        failures.append("Opportunity score is outside the valid 0-100 range.")
    if not (0.0 <= probability <= 100.0):
        failures.append("Probability of profit is outside the valid 0-100 range.")
    if rr <= 0.0:
        failures.append("Risk/reward ratio is invalid.")
    if net_ev <= 0.0 and str(recommendation).upper() == "BUY":
        failures.append("A BUY decision cannot have non-positive net expected value.")
    if str(recommendation).upper() == "BUY" and not scenario_ok:
        failures.append("Scenario Engine did not approve the trade.")
    if str(recommendation).upper() == "BUY" and not capital_ok:
        failures.append("Capital Allocation AI did not approve the trade.")
    if str(recommendation).upper() == "BUY" and not portfolio_ok:
        failures.append("Portfolio Supercomputer did not approve the trade.")
    if str(recommendation).upper() == "BUY" and ceiling <= 0.0:
        failures.append("No positive execution-dollar ceiling was approved.")

    configured_age = max_signal_age_minutes
    if configured_age is None:
        configured_age = _num(os.getenv("ORACLE_MAX_SIGNAL_AGE_MINUTES"), 30.0)
    created_at = signal.get("created_at") if isinstance(signal, dict) else getattr(signal, "created_at", None)
    age = _age_minutes(created_at)
    stale = bool(age is not None and configured_age > 0 and age > configured_age)
    if stale:
        failures.append(f"Signal is stale ({age:.1f} minutes old; limit {configured_age:.1f}).")
    elif age is None:
        warnings.append("Signal timestamp was unavailable; freshness could not be verified.")

    if score < 75:
        warnings.append("Final opportunity quality is below the STRONG threshold.")
    if probability < 55:
        warnings.append("Modeled probability of profit is below 55%.")
    if rr < 1.5:
        warnings.append("Risk/reward is below 1.5:1.")

    integrity = 100.0 - 18.0 * len(failures) - 4.0 * len(warnings)
    integrity = max(0.0, min(100.0, integrity))
    approved = str(recommendation).upper() == "BUY" and not failures
    verdict = "EXECUTE WITHIN CEILING" if approved else ("HOLD FOR REVIEW" if not failures else "BLOCK EXECUTION")
    summary = (
        f"{verdict}. Integrity {integrity:.0f}/100; approved execution ceiling ${ceiling:,.2f}."
        if approved else
        f"{verdict}. Integrity {integrity:.0f}/100; {failures[0] if failures else 'upstream recommendation is not BUY.'}"
    )
    return OracleOneVerdict(
        approved=approved,
        verdict=verdict,
        integrity_score=round(integrity, 2),
        execution_ceiling=round(ceiling, 2),
        kill_switch_active=kill_switch,
        stale_data=stale,
        invariant_failures=tuple(failures),
        warnings=tuple(warnings),
        summary=summary,
    )
