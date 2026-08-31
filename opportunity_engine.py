from __future__ import annotations
from typing import Any

from oracle_intelligence import evaluate_opportunity
from market_memory import feature_vector


def _value(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def rank_opportunities(signals: list[Any], limit: int = 12, market: str | None = None) -> list[dict]:
    """Rank opportunities with the same quant standard used by execution.

    This removes the former mismatch where the dashboard used a simplified
    score while the worker approved trades with a different formula. Ranked
    payloads also carry immutable signal/forecast/quote provenance so later
    learning can link a closed trade to the exact entry evidence rather than a
    newer decision for the same symbol.
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
        route = _value(signal, "market_data_route", {}) or {}
        signal_id = _value(signal, "signal_id", None)
        forecast_id = _value(signal, "forecast_id", None)
        correlation_id = (
            _value(signal, "decision_correlation_id", None)
            or _value(signal, "correlation_id", None)
            or route.get("decision_correlation_id")
            or route.get("correlation_id")
        )
        payload.update(
            {
                "signal_id": signal_id,
                "forecast_id": forecast_id,
                "entry_decision_id": f"signal:{signal_id}" if signal_id not in (None, "") else None,
                "decision_correlation_id": correlation_id,
                "scan_type": _value(signal, "scan_type", "") or route.get("scan_type"),
                "source_interval": _value(signal, "source_interval", "") or route.get("interval"),
                "source_quote_timestamp": _value(signal, "source_quote_timestamp", "") or route.get("quote_timestamp"),
                "quote_timestamp": _value(signal, "quote_timestamp", "") or route.get("quote_timestamp"),
                "quote_age_seconds": _value(signal, "quote_age_seconds", None) if _value(signal, "quote_age_seconds", None) is not None else route.get("quote_age_seconds"),
                "requested_symbol": _value(signal, "requested_symbol", "") or route.get("requested_symbol"),
                "provider_symbol": _value(signal, "provider_symbol", "") or route.get("provider_symbol"),
                "provider": _value(signal, "provider", "") or route.get("provider"),
                "quote_verified": bool(_value(signal, "quote_verified", False) or route.get("quote_verified") is True),
                "market_data_route": route,
            }
        )
        payload["features"] = feature_vector(signal)
        payload["council_score"] = round(float(_value(signal, "score", 0.0)) * (100 if float(_value(signal, "score", 0.0)) <= 1 else 1), 2)
        payload["confidence"] = round(float(_value(signal, "confidence", 0.0)) * (100 if float(_value(signal, "confidence", 0.0)) <= 1 else 1), 2)
        payload["confidence_kind"] = str(_value(signal, "confidence_kind", "HEURISTIC_SCORE") or "HEURISTIC_SCORE").upper()
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
