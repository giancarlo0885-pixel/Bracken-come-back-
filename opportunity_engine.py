from __future__ import annotations
from typing import Any

from oracle_intelligence import evaluate_opportunity
from market_memory import feature_vector


def _value(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def rank_opportunities(signals: list[Any], limit: int = 12, market: str | None = None) -> list[dict]:
    """Rank opportunities with the same quant standard used by execution.

    This removes the former mismatch where the dashboard used a simplified
    score while the worker approved trades with a different formula.
    """
    ranked: list[dict] = []
    for signal in signals:
        signal_market = str(_value(signal, "market", market or "cash") or market or "cash").lower()
        portfolio_context = None
        position_rows: list[dict[str, Any]] = []
        try:
            from database import row, rows
            portfolio_row = row("SELECT * FROM portfolios WHERE market=%s", (signal_market,)) or {}
            position_rows = rows("SELECT * FROM positions WHERE market=%s", (signal_market,)) or []
            cash = float(portfolio_row.get("cash", 0.0) or 0.0)
            invested = sum(float(p.get("quantity", 0.0) or 0.0) * float(p.get("current_price", 0.0) or 0.0) for p in position_rows)
            portfolio_context = {"cash": cash, "equity": cash + invested}
        except Exception:
            portfolio_context = None
        decision = evaluate_opportunity(
            signal, market=signal_market, portfolio=portfolio_context, positions=position_rows
        )
        payload = decision.to_dict()
        payload["features"] = feature_vector(signal)
        payload["council_score"] = round(float(_value(signal, "score", 0.0)) * (100 if float(_value(signal, "score", 0.0)) <= 1 else 1), 2)
        payload["confidence"] = round(float(_value(signal, "confidence", 0.0)) * (100 if float(_value(signal, "confidence", 0.0)) <= 1 else 1), 2)
        payload["approved"] = bool(decision.quant.get("approved"))
        ranked.append(payload)

    ranked.sort(
        key=lambda item: (
            bool(item.get("approved")),
            float(item.get("opportunity_score", 0.0)),
            float(item.get("quant", {}).get("net_expected_value_pct", 0.0)),
        ),
        reverse=True,
    )
    for index, item in enumerate(ranked[:limit], 1):
        item["rank"] = index
    return ranked[:limit]
