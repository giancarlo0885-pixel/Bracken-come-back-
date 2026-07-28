from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if not math.isfinite(result) else result
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class AutonomousBrief:
    posture: str
    conviction_score: float
    deployment_score: float
    system_readiness_score: float
    top_actions: tuple[str, ...]
    opportunity_summary: str
    portfolio_summary: str
    risk_summary: str
    operations_summary: str
    executive_brief: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["top_actions"] = list(self.top_actions)
        return data


def build_autonomous_brief(context: dict[str, Any]) -> AutonomousBrief:
    """Create a deterministic, evidence-backed daily command brief.

    It only uses supplied platform state. Missing evidence lowers readiness
    instead of being fabricated.
    """
    opportunities = list(context.get("opportunities") or [])
    portfolios = list(context.get("portfolios") or [])
    workers = list(context.get("workers") or [])
    diagnostics = list(context.get("diagnostics") or [])
    risk_reasons = list(context.get("risk_reasons") or [])
    snapshot = context.get("snapshot")

    top = opportunities[0] if opportunities else {}
    top_score = _num(top.get("opportunity_score"))
    payload = top.get("payload") if isinstance(top.get("payload"), dict) else {}
    if not payload and top.get("payload"):
        try:
            import json
            parsed = json.loads(str(top.get("payload")))
            payload = parsed if isinstance(parsed, dict) else {}
        except Exception:
            payload = {}
    recommendation = str(payload.get("recommendation", "WATCH")).upper()
    probability = _num(payload.get("probability_of_profit"))
    quant = payload.get("quant") or {}
    net_ev = _num(quant.get("net_expected_value_pct")) * 100.0

    equity = sum(_num(p.get("equity")) for p in portfolios)
    cash = sum(_num(p.get("cash")) for p in portfolios)
    cash_pct = cash / equity * 100.0 if equity > 0 else 0.0

    expected_workers = 2
    healthy_statuses = {"starting", "running", "idle", "healthy", "online"}
    online_workers = sum(1 for w in workers if str(w.get("status", "")).strip().lower() in healthy_statuses)
    provider_total = len(diagnostics)
    provider_ok = sum(1 for d in diagnostics if bool(d.get("available", d.get("ok", False))) or str(d.get("status", "")).lower() in {"available", "configured", "healthy"})
    provider_pct = provider_ok / provider_total * 100.0 if provider_total else 0.0
    readiness = _clip(45.0 * min(1.0, online_workers / expected_workers) + 35.0 * provider_pct / 100.0 + (20.0 if opportunities else 0.0))

    conviction = _clip(0.50 * top_score + 0.30 * probability + 0.20 * _clip(50.0 + net_ev * 8.0)) if opportunities else 0.0
    risk_penalty = min(45.0, len(risk_reasons) * 8.0)
    deployment = _clip(0.55 * conviction + 0.25 * readiness + 0.20 * _clip(cash_pct * 4.0) - risk_penalty)

    if online_workers < expected_workers or readiness < 55:
        posture = "SYSTEM DEFENSE"
    elif deployment >= 78 and recommendation == "BUY":
        posture = "DEPLOY CAPITAL"
    elif deployment >= 60:
        posture = "SELECTIVE OFFENSE"
    elif deployment >= 42:
        posture = "WATCH AND PREPARE"
    else:
        posture = "PRESERVE CAPITAL"

    actions: list[str] = []
    if online_workers < expected_workers:
        actions.append("Restore all market workers before trusting new rankings.")
    if provider_pct < 70:
        actions.append("Repair provider coverage; intelligence confidence is degraded.")
    if cash_pct < 10:
        actions.append("Pause new buys and rebuild the portfolio cash reserve toward 10%.")
    if opportunities and recommendation == "BUY" and deployment >= 60:
        actions.append(f"Prioritize {top.get('symbol', 'the top setup')} only within its approved dollar ceiling.")
    if risk_reasons:
        actions.append("Resolve the highest-severity Risk Watch item before increasing exposure.")
    if not actions:
        actions.append("Continue normal scans and deploy only into Oracle-approved setups.")

    opportunity_summary = (
        f"Top setup {top.get('symbol', 'none')} is {recommendation} at {top_score:.1f}/100, "
        f"with {probability:.0f}% modeled probability and {net_ev:+.2f}% net expected value."
        if opportunities else "No ranked opportunities are available yet."
    )
    portfolio_summary = f"Combined equity is ${equity:,.2f}; cash is ${cash:,.2f} ({cash_pct:.1f}%)."
    risk_summary = "No active portfolio warnings." if not risk_reasons else f"{len(risk_reasons)} active risk condition(s) require attention."
    operations_summary = f"{online_workers}/{expected_workers} workers online; provider readiness {provider_pct:.0f}%."
    executive = (
        f"{posture}. {opportunity_summary} {portfolio_summary} {risk_summary} "
        f"{operations_summary} Capital deployment score is {deployment:.1f}/100."
    )
    return AutonomousBrief(
        posture=posture,
        conviction_score=round(conviction, 2),
        deployment_score=round(deployment, 2),
        system_readiness_score=round(readiness, 2),
        top_actions=tuple(actions[:5]),
        opportunity_summary=opportunity_summary,
        portfolio_summary=portfolio_summary,
        risk_summary=risk_summary,
        operations_summary=operations_summary,
        executive_brief=executive,
    )
