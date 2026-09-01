import json
from datetime import datetime, timezone

from market_memory import assess_market_memory, feature_vector, record_closed_trade_memory, setup_similarity
from oracle_intelligence import evaluate_opportunity


def strong_signal():
    return {
        "symbol": "TEST", "score": 0.91, "confidence": 0.88,
        "momentum_5d": 0.05, "momentum_20d": 0.11,
        "trend_strength": 0.06, "volume_ratio": 1.7,
        "volatility_20d": 0.22, "atr_pct": 0.018,
        "news_sentiment": 0.6, "relative_strength": 0.12,
        "spread_pct": 0.0005, "estimated_slippage_pct": 0.0004,
        "event_risk_score": 10,
    }


def records(returns):
    f = feature_vector(strong_signal())
    return [
        {"symbol": f"OLD{i}", "market": "cash", "return_pct": ret,
         "payload": {"features": f}, "market_regime": "risk-on"}
        for i, ret in enumerate(returns)
    ]


def test_identical_setup_similarity_is_one():
    f = feature_vector(strong_signal())
    assert setup_similarity(f, f) == 1.0


def test_positive_history_supports_current_setup():
    memory = assess_market_memory(strong_signal(), records([0.04, 0.05, 0.02, 0.06, -0.01, 0.03, 0.07, 0.02]))
    assert memory.analog_count == 8
    assert memory.win_rate > 0.70
    assert memory.score_adjustment > 0
    assert not memory.veto


def test_repeated_negative_history_can_veto():
    memory = assess_market_memory(strong_signal(), records([-0.05, -0.04, -0.03, -0.06, -0.02, -0.07, -0.04, -0.03, -0.02, 0.01, -0.05, -0.04]))
    assert memory.win_rate < 0.30
    assert memory.score_adjustment < 0
    assert memory.veto


def test_oracle_exposes_market_memory_adjustment():
    decision = evaluate_opportunity(strong_signal(), historical_records=records([0.04, 0.03, 0.05, 0.02, 0.06, 0.03]))
    assert decision.memory["analog_count"] == 6
    assert decision.opportunity_score >= decision.base_opportunity_score
    assert "memory" in decision.to_dict()


def test_closed_trade_memory_uses_immutable_entry_decision_not_later_symbol_decision(monkeypatch):
    inserted = {}

    class Result:
        def __init__(self, value=None):
            self.value = value

        def fetchone(self):
            return self.value

    class FakeConn:
        def execute(self, sql, params=()):
            if "SELECT payload" in sql:
                assert "payload->>'decision_id'" in sql
                assert "ORDER BY created_at ASC" in sql
                assert params[2] == "decision-A"
                return Result(
                    {
                        "payload": {
                            "decision_id": "decision-A",
                            "features": {"alpha": 0.20, "momentum_20d": 0.10},
                            "reason": "entry decision A",
                            "opportunity_score": 70,
                        },
                        "opportunity_score": 70,
                        "created_at": "2026-01-01T14:30:00+00:00",
                    }
                )
            if "INSERT INTO trade_dna" in sql:
                inserted["sql"] = sql
                inserted["params"] = params
                return Result()
            raise AssertionError(f"unexpected SQL: {sql}")

    class FakeContext:
        def __enter__(self):
            return FakeConn()

        def __exit__(self, exc_type, exc, tb):
            return False

    import database

    monkeypatch.setattr(database, "connect", lambda: FakeContext())
    record_closed_trade_memory(
        market="cash",
        symbol="AAPL",
        position={
            "symbol": "AAPL",
            "opened_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
            "average_price": 100,
        },
        exit_price=110,
        pnl=10,
        exit_reason="unit close",
        quantity=1,
        entry_provenance={
            "entry_decision_id": "decision-A",
            "entry_signal_id": "signal-A",
            "entry_forecast_id": "forecast-A",
            "entry_quote_id": "quote-A",
            "decision_correlation_id": "corr-A",
            "feature_snapshot": {"alpha": 0.20, "momentum_20d": 0.10},
        },
    )

    params = inserted["params"]
    payload = json.loads(params[19])
    assert payload["entry_decision_id"] == "decision-A"
    assert payload["features"] == {"alpha": 0.20, "momentum_20d": 0.10}
    assert "decision-B" not in json.dumps(payload)
    assert params[-5:] == ("decision-A", "signal-A", "forecast-A", "quote-A", "corr-A")
