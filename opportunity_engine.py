from __future__ import annotations
from typing import Any

from oracle_intelligence import evaluate_opportunity
from market_memory import feature_vector


def _value(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _verified_execution_quote(symbol: str) -> dict[str, Any]:
    """Return a current execution-eligible quote without weakening provider gates.

    Opportunity ranking is built from research signals, whose source history can
    legitimately be daily or otherwise non-execution-grade.  The worker already
    requires a separate execution quote before trading.  The dashboard needs the
    same distinction, so ranking payloads carry a verified execution mark while
    preserving the original analysis provenance.
    """
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return {}
    try:
        from market_data import get_live_snapshot, snapshot_is_verified

        snapshot = get_live_snapshot(normalized)
        if snapshot is None or not snapshot_is_verified(snapshot, normalized):
            return {}
        payload = dict(snapshot.to_quote_payload())
    except Exception:
        return {}

    requested = str(payload.get("requested_symbol") or normalized).upper().strip()
    provider_symbol = str(payload.get("provider_symbol") or "").upper().strip()
    if requested != normalized or provider_symbol != normalized:
        return {}
    if payload.get("quote_verified") is not True or payload.get("stale") is True:
        return {}
    try:
        if float(payload.get("price") or 0.0) <= 0:
            return {}
    except (TypeError, ValueError):
        return {}
    if not payload.get("quote_timestamp"):
        return {}
    return payload


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

        # Keep analysis provenance immutable, but attach the independently
        # verified execution mark that the paper broker is allowed to use.
        symbol = str(_value(signal, "symbol", payload.get("symbol", "")) or payload.get("symbol", "")).upper().strip()
        execution_quote = _verified_execution_quote(symbol)
        if execution_quote:
            payload.update(
                {
                    "analysis_source_interval": payload.get("source_interval"),
                    "analysis_quote_timestamp": payload.get("source_quote_timestamp") or payload.get("quote_timestamp"),
                    "analysis_provider": payload.get("provider"),
                    "execution_price": execution_quote.get("price"),
                    "execution_quote_timestamp": execution_quote.get("quote_timestamp"),
                    "execution_quote_age_seconds": execution_quote.get("quote_age_seconds"),
                    "execution_source_interval": execution_quote.get("source_interval") or execution_quote.get("interval"),
                    "execution_requested_symbol": execution_quote.get("requested_symbol"),
                    "execution_provider_symbol": execution_quote.get("provider_symbol"),
                    "execution_provider": execution_quote.get("provider"),
                    "execution_quote_verified": True,
                    "execution_stale": False,
                    "provider_quote_verified": execution_quote.get("provider_quote_verified") is True,
                    "paper_reference_verified": execution_quote.get("paper_reference_verified") is True,
                    # These top-level fields describe the price displayed as
                    # tradable now; source_* above still describe analysis.
                    "price": execution_quote.get("price"),
                    "quote_timestamp": execution_quote.get("quote_timestamp"),
                    "quote_age_seconds": execution_quote.get("quote_age_seconds"),
                    "requested_symbol": execution_quote.get("requested_symbol"),
                    "provider_symbol": execution_quote.get("provider_symbol"),
                    "provider": execution_quote.get("provider"),
                    "quote_verified": True,
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
