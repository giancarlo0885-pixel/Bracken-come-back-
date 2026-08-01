from portfolio_advisor import analyze_portfolio, simulate_trade
from prediction_engine import build_decisions
from datetime import datetime, timezone


def test_portfolio_analyzer_and_simulator():
    positions = [{"symbol": "AAA", "quantity": 2, "current_price": 100, "average_price": 90}]
    health = analyze_portfolio(800, positions)
    assert health.equity == 1000
    result = simulate_trade(800, positions, "buy", "BBB", 100, 50)
    assert result["after"]["position_count"] == 2
    assert result["verdict"] in {"BETTER", "MIXED", "WORSE"}


def test_decision_builder_is_plain_language():
    now = datetime.now(timezone.utc).isoformat()
    quote_time = now
    decisions = build_decisions(
        [{"market": "cash", "symbol": "AAA", "opportunity_score": 91, "payload": {"reason": "Strong evidence"}}],
        [{"market": "cash", "symbol": "AAA", "price": 100, "confidence": 0.92, "action": "BUY", "created_at": now, "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": quote_time}],
        [{"market": "cash", "symbol": "AAA", "requested_symbol": "AAA", "provider_symbol": "AAA", "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": quote_time, "target_price": 110, "low_price": 95, "high_price": 115, "probability_up": 0.7, "created_at": now}],
    )
    assert decisions[0]["action"] == "BUY"
    assert decisions[0]["expected_return"] == 10.0
    assert decisions[0]["confidence"] == 92.0
