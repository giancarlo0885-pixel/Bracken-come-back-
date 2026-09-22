from __future__ import annotations
from typing import Any
import math

def _f(v: Any, default: float = 0.0) -> float:
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default

def integrity_flags(row: dict[str, Any]) -> list[str]:
    """Pure paper-research sanity checks; never authorizes or sizes a trade."""
    flags=[]
    for key in ("probability","confidence","win_probability"):
        if row.get(key) is not None and not 0 <= _f(row.get(key), -1) <= 1:
            flags.append(f"{key}_outside_0_1")
    for key in ("fees","spread_pct","slippage_pct","impact_pct","cost_pct"):
        if row.get(key) is not None and _f(row.get(key), -1) < 0:
            flags.append(f"{key}_negative")
    if row.get("profit_factor") is not None and _f(row.get("profit_factor"), -1) < 0:
        flags.append("profit_factor_negative")
    if row.get("mfe_pct") is not None and _f(row.get("mfe_pct")) < 0:
        flags.append("mfe_sign_invalid")
    if row.get("mae_pct") is not None and _f(row.get("mae_pct")) > 0:
        flags.append("mae_sign_invalid")
    if row.get("quantity") is not None and _f(row.get("quantity")) < 0:
        flags.append("quantity_negative")
    return flags

def learning_velocity(completed: int, elapsed_hours: float, target: int = 1000) -> dict[str, Any]:
    hours=max(0.0,_f(elapsed_hours))
    rate=(completed/hours) if hours>0 else 0.0
    remaining=max(0,target-int(completed))
    return {
        "trades_per_hour": rate,
        "trades_per_day": rate*24.0,
        "remaining": remaining,
        "eta_hours": (remaining/rate) if rate>0 else None,
    }

def challenger_verdict(challenger: dict[str, Any], control: dict[str, Any], min_samples: int = 1000) -> dict[str, Any]:
    """Evidence comparison only. It cannot promote a challenger to execution authority."""
    n=int(challenger.get("accepted_trades") or 0)
    ready=n>=min_samples
    better_expectancy=_f(challenger.get("expectancy")) > _f(control.get("expectancy"))
    better_pf=_f(challenger.get("profit_factor")) > _f(control.get("profit_factor"))
    return {
        "evidence_complete": ready,
        "beats_control_on_expectancy": ready and better_expectancy,
        "beats_control_on_profit_factor": ready and better_pf,
        "promotion_authorized": False,
        "execution_impact": "NONE",
    }
