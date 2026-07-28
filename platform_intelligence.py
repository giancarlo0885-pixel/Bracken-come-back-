from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from typing import Any, Callable


def num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def confidence(value: Any) -> float:
    x = num(value)
    return max(0.0, min(100.0, x * 100 if x <= 1 else x))


def json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


@dataclass
class IntelligenceSnapshot:
    generated_at: str
    regime: str
    regime_score: float
    opportunity_count: int
    risk_level: str
    risk_score: float
    breadth: float
    bullish_signals: int
    bearish_signals: int
    neutral_signals: int
    top_opportunity: str | None
    top_opportunity_score: float
    active_alerts: int
    provider_coverage: float
    workers_online: int
    workers_expected: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_regime(signals: list[dict[str, Any]]) -> tuple[str, float, float]:
    if not signals:
        return "Insufficient Data", 50.0, 0.0
    scores = [num(x.get("score"), 50.0) for x in signals]
    actions = [str(x.get("action", "HOLD")).upper() for x in signals]
    bullish = sum(a == "BUY" for a in actions)
    bearish = sum(a == "SELL" for a in actions)
    breadth = (bullish - bearish) / max(1, len(actions)) * 100
    score = sum(scores) / len(scores)
    composite = score * 0.7 + (breadth + 100) / 2 * 0.3
    if composite >= 68:
        return "Risk-On", composite, breadth
    if composite >= 56:
        return "Constructive", composite, breadth
    if composite <= 34:
        return "Risk-Off", composite, breadth
    if composite <= 45:
        return "Defensive", composite, breadth
    return "Mixed", composite, breadth


def compute_risk(
    positions: list[dict[str, Any]],
    portfolio_metrics: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    workers_online: int,
    workers_expected: int,
) -> tuple[str, float, list[str]]:
    total_equity = sum(num(x.get("equity")) for x in portfolio_metrics)
    total_invested = sum(num(x.get("positions_value")) for x in portfolio_metrics)
    invested_pct = total_invested / total_equity * 100 if total_equity else 0
    values = [num(p.get("quantity")) * num(p.get("current_price")) for p in positions]
    concentration = max(values, default=0) / total_equity * 100 if total_equity else 0
    severe = sum(str(a.get("severity", "")).lower() in {"high", "critical"} for a in alerts)
    offline = max(0, workers_expected - workers_online)
    score = min(100.0, invested_pct * 0.25 + concentration * 0.9 + severe * 12 + offline * 18)
    reasons: list[str] = []
    if concentration > 30:
        reasons.append(f"Largest position is {concentration:.0f}% of total equity.")
    if invested_pct > 85:
        reasons.append(f"Only {100-invested_pct:.0f}% of equity remains in cash.")
    if severe:
        reasons.append(f"{severe} high-severity alert(s) require attention.")
    if offline:
        reasons.append(f"{offline} market worker(s) are not reporting online.")
    if not reasons:
        reasons.append("No major portfolio or system concentration warning is visible.")
    level = "Critical" if score >= 75 else "High" if score >= 55 else "Moderate" if score >= 30 else "Low"
    return level, round(score, 1), reasons


def build_snapshot(
    *,
    signals: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    portfolio_metrics: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    workers: list[dict[str, Any]],
    worker_online_fn: Callable[[Any], bool],
) -> tuple[IntelligenceSnapshot, list[str]]:
    regime, regime_score, breadth = classify_regime(signals)
    actions = [str(x.get("action", "HOLD")).upper() for x in signals]
    online = sum(worker_online_fn(x.get("status")) for x in workers)
    expected = max(2, len(workers))
    risk_level, risk_score, reasons = compute_risk(positions, portfolio_metrics, alerts, online, expected)
    configured = sum(bool(x.get("configured")) for x in diagnostics)
    coverage = configured / max(1, len(diagnostics)) * 100
    top = max(opportunities, key=lambda x: num(x.get("opportunity_score")), default={})
    snap = IntelligenceSnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
        regime=regime,
        regime_score=round(regime_score, 1),
        opportunity_count=len(opportunities),
        risk_level=risk_level,
        risk_score=risk_score,
        breadth=round(breadth, 1),
        bullish_signals=sum(a == "BUY" for a in actions),
        bearish_signals=sum(a == "SELL" for a in actions),
        neutral_signals=sum(a not in {"BUY", "SELL"} for a in actions),
        top_opportunity=str(top.get("symbol")) if top else None,
        top_opportunity_score=round(num(top.get("opportunity_score")), 1),
        active_alerts=len(alerts),
        provider_coverage=round(coverage, 1),
        workers_online=online,
        workers_expected=expected,
    )
    return snap, reasons


def deterministic_brief(snapshot: IntelligenceSnapshot, reasons: list[str]) -> str:
    top = (
        f"The highest-ranked visible opportunity is {snapshot.top_opportunity} "
        f"at {snapshot.top_opportunity_score:.1f}/100."
        if snapshot.top_opportunity else
        "No ranked opportunity is available yet; allow both workers to complete a scan."
    )
    return (
        f"Market conditions are classified as {snapshot.regime} with a regime score of "
        f"{snapshot.regime_score:.1f}/100 and breadth of {snapshot.breadth:+.1f}. "
        f"The latest signal set contains {snapshot.bullish_signals} bullish, "
        f"{snapshot.neutral_signals} neutral, and {snapshot.bearish_signals} bearish calls. "
        f"{top} Platform risk is {snapshot.risk_level.lower()} ({snapshot.risk_score:.1f}/100). "
        f"Primary watch item: {reasons[0]}"
    )
