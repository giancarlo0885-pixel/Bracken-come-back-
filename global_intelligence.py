from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


def _num(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return default if not math.isfinite(x) else x
    except (TypeError, ValueError):
        return default


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _clip(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class GlobalIntelligenceAssessment:
    global_score: float
    macro_alignment_score: float
    cross_asset_confirmation_score: float
    sector_alignment_score: float
    liquidity_regime_score: float
    risk_on_score: float
    score_adjustment: float
    position_multiplier: float
    approved: bool
    veto: bool
    regime: str
    verdict: str
    drivers: tuple[str, ...]
    conflicts: tuple[str, ...]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["drivers"] = list(self.drivers)
        d["conflicts"] = list(self.conflicts)
        return d


def assess_global_intelligence(signal: Any, context: dict[str, Any] | None = None, *, market: str = "cash") -> GlobalIntelligenceAssessment:
    """Score whether the wider financial system supports a trade.

    Inputs are optional and safely neutral when unavailable. Expected context keys may
    include vix, dxy_change_pct, treasury_10y_change_bps, oil_change_pct,
    gold_change_pct, spy_change_pct, bitcoin_change_pct, market_breadth,
    sector_relative_strength, credit_spread_change_bps, and liquidity_score.
    """
    c = dict(context or {})
    # Signal-level values override aggregate context when a provider supplies them.
    for key in (
        "vix", "dxy_change_pct", "treasury_10y_change_bps", "oil_change_pct",
        "gold_change_pct", "spy_change_pct", "bitcoin_change_pct", "market_breadth",
        "sector_relative_strength", "credit_spread_change_bps", "liquidity_score",
    ):
        val = _value(signal, key, None)
        if val is not None:
            c[key] = val

    action = str(_value(signal, "action", "HOLD") or "HOLD").upper()
    bullish = action in {"BUY", "LONG", "ACCUMULATE"}
    sector = str(_value(signal, "sector", "UNKNOWN") or "UNKNOWN").upper()
    vix = _num(c.get("vix"), 20.0)
    dxy = _num(c.get("dxy_change_pct"), 0.0)
    rates = _num(c.get("treasury_10y_change_bps"), 0.0)
    spy = _num(c.get("spy_change_pct"), 0.0)
    btc = _num(c.get("bitcoin_change_pct"), 0.0)
    breadth = _num(c.get("market_breadth"), 0.0)
    sector_rs = _num(c.get("sector_relative_strength"), 0.0)
    credit = _num(c.get("credit_spread_change_bps"), 0.0)
    liquidity = _clip(_num(c.get("liquidity_score"), 60.0))
    oil = _num(c.get("oil_change_pct"), 0.0)
    gold = _num(c.get("gold_change_pct"), 0.0)

    risk_on = 50.0
    risk_on += _clip(spy * 8.0, -18, 18)
    risk_on += _clip(breadth * 0.22, -16, 16)
    risk_on += _clip(btc * 3.0, -10, 10)
    risk_on -= _clip((vix - 20.0) * 1.6, -12, 28)
    risk_on -= _clip(credit * 0.8, -10, 20)
    risk_on -= _clip(dxy * 4.0, -8, 12)
    risk_on = _clip(risk_on)

    macro = 58.0
    if bullish:
        macro += _clip(spy * 7.0, -16, 16)
        macro += _clip(breadth * 0.18, -12, 12)
        macro -= _clip(max(0.0, rates) * 0.55, 0, 16)
        macro -= _clip(max(0.0, dxy) * 3.0, 0, 10)
    else:
        macro += _clip(-spy * 6.0, -14, 14)
        macro += _clip(-breadth * 0.15, -10, 10)
    if sector in {"ENERGY", "OIL", "MATERIALS"}:
        macro += _clip(oil * 4.0, -12, 12)
    if sector in {"GOLD", "METALS", "MINING"}:
        macro += _clip(gold * 4.0, -12, 12)
    if sector in {"TECHNOLOGY", "SEMICONDUCTORS", "GROWTH"}:
        macro -= _clip(max(0.0, rates) * 0.45, 0, 12)
    macro = _clip(macro)

    cross_asset = 52.0
    cross_asset += _clip(spy * 5.0, -12, 12) if bullish else _clip(-spy * 5.0, -12, 12)
    if market == "crypto":
        cross_asset += _clip(btc * 6.0, -20, 20)
        cross_asset -= _clip(max(0.0, dxy) * 4.0, 0, 12)
    else:
        cross_asset += _clip(btc * 1.5, -5, 5)
    cross_asset = _clip(cross_asset)

    sector_score = _clip(55.0 + sector_rs * 10.0 + (breadth * 0.08 if bullish else -breadth * 0.08))
    liquidity_regime = _clip(0.65 * liquidity + 0.35 * (100.0 - _clip((vix - 12.0) * 3.2)))
    global_score = _clip(0.34 * macro + 0.26 * cross_asset + 0.22 * sector_score + 0.18 * liquidity_regime)

    drivers: list[str] = []
    conflicts: list[str] = []
    if risk_on >= 62: drivers.append("broad risk appetite is supportive")
    if sector_score >= 68: drivers.append("sector leadership confirms the setup")
    if cross_asset >= 66: drivers.append("cross-asset markets confirm direction")
    if liquidity_regime >= 68: drivers.append("liquidity and volatility conditions are favorable")
    if vix >= 32: conflicts.append("volatility regime is stressed")
    if bullish and breadth <= -35: conflicts.append("market breadth strongly contradicts a long entry")
    if bullish and sector_score <= 35: conflicts.append("sector relative strength is materially weak")
    if credit >= 18: conflicts.append("credit conditions are deteriorating")
    if market == "crypto" and bullish and dxy >= 1.5: conflicts.append("sharp dollar strength pressures crypto risk assets")

    adjustment = _clip((global_score - 50.0) * 0.18, -7.0, 7.0)
    extreme_conflict = len(conflicts) >= 2 and global_score < 38
    veto = bool(extreme_conflict)
    approved = not veto
    multiplier = _clip(0.55 + global_score / 115.0, 0.45, 1.22)
    if veto: multiplier = min(multiplier, 0.35)
    regime = "RISK-ON" if risk_on >= 62 else ("RISK-OFF" if risk_on <= 40 else "MIXED")
    verdict = "GLOBAL CONFIRMATION" if global_score >= 70 else ("NEUTRAL GLOBAL BACKDROP" if global_score >= 48 else ("GLOBAL CONFLICT" if not veto else "GLOBAL VETO"))
    summary = f"{verdict}: global score {global_score:.1f}/100, macro {macro:.1f}, cross-asset {cross_asset:.1f}, sector {sector_score:.1f}, liquidity {liquidity_regime:.1f}."
    if drivers: summary += " Supports: " + "; ".join(drivers) + "."
    if conflicts: summary += " Conflicts: " + "; ".join(conflicts) + "."

    return GlobalIntelligenceAssessment(
        round(global_score, 2), round(macro, 2), round(cross_asset, 2), round(sector_score, 2),
        round(liquidity_regime, 2), round(risk_on, 2), round(adjustment, 2), round(multiplier, 4),
        approved, veto, regime, verdict, tuple(drivers), tuple(conflicts), summary,
    )
