from datetime import datetime, timezone

from advisor_engine import AdvisorProfile, generate_recommendation


def test_restricted_asset_cannot_render_nested_buy_identity() -> None:
    now = datetime.now(timezone.utc).isoformat()
    candidate = {
        "symbol": "AAPL",
        "name": "Apple",
        "market": "cash",
        "exchange": "NASDAQ",
        "currency": "USD",
        "verified_quote": {
            "symbol": "AAPL",
            "requested_symbol": "AAPL",
            "provider_symbol": "AAPL",
            "provider": "unit",
            "price": 200.0,
            "quote_timestamp": now,
            "interval": "5m",
            "quote_verified": True,
        },
        "confidence": 95,
        "opportunity_score": 95,
        "expected_return": 8,
        "expected_downside": 2,
        "data_quality_score": 90,
        "liquidity_value": 1_000_000,
        "validation_status": "approved",
        "catalyst": "unit-test catalyst",
        "investment_thesis": "unit-test thesis",
    }
    profile = AdvisorProfile(available_capital=10_000, restricted_assets=["AAPL"])

    recommendation = generate_recommendation(candidate, profile)

    assert recommendation.action == "AVOID"
    assert recommendation.oracle_judgment["final_judgment"]["action"] == "AVOID"
    assert recommendation.oracle_decision["final_judgment"] == "AVOID"
