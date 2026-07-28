from __future__ import annotations
from typing import Any
from config import ROTATION_MIN_SCORE_GAP

def rotation_plan(positions: list[dict[str,Any]], ranked: list[dict[str,Any]], min_gap: float = ROTATION_MIN_SCORE_GAP) -> list[dict[str,Any]]:
    if not positions or not ranked: return []
    held={str(p.get("symbol","")):p for p in positions}
    candidates=[r for r in ranked if r.get("action")=="BUY" and r.get("symbol") not in held]
    if not candidates: return []
    weakest=sorted(positions,key=lambda p: float(p.get("unrealized_pct",0) or 0))
    best=candidates[0]; plans=[]
    for pos in weakest[:2]:
        held_score=float(pos.get("opportunity_score",50) or 50)
        if float(best["opportunity_score"])-held_score >= min_gap:
            plans.append({"sell_symbol":pos["symbol"],"buy_symbol":best["symbol"],"score_gap":round(float(best["opportunity_score"])-held_score,2),"reason":"Rotate capital toward a materially stronger ranked opportunity."})
    return plans
