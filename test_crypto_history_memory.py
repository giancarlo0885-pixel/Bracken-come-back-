from datetime import date

from crypto_history_memory import CATALOG_VERSION, crypto_history_context


def test_bitcoin_context_contains_protocol_cycle_and_institutional_history():
    context = crypto_history_context(
        {"symbol": "BTC-USD", "event_risk_score": 10},
        as_of=date(2026, 8, 31),
        limit=12,
    )
    ids = {event["event_id"] for event in context["events"]}
    assert context["catalog_version"] == CATALOG_VERSION
    assert "bitcoin-genesis" in ids
    assert "bitcoin-halving-2024" in ids
    assert "us-spot-bitcoin-etfs" in ids
    assert context["influences_decision"] is False
    assert context["score_adjustment"] == 0.0


def test_stablecoin_context_recalls_depeg_and_regulatory_history():
    context = crypto_history_context(
        {"symbol": "USDC-USD", "history_tags": ["stablecoin"]},
        as_of=date(2026, 8, 31),
        limit=12,
    )
    ids = {event["event_id"] for event in context["events"]}
    assert "terra-collapse" in ids
    assert "genius-act" in ids


def test_high_event_risk_surfaces_exchange_and_contagion_lessons():
    context = crypto_history_context(
        {"symbol": "ETH-USD", "event_risk_score": 90, "volatility_20d": 0.9},
        as_of=date(2026, 8, 31),
        limit=12,
    )
    ids = {event["event_id"] for event in context["events"]}
    assert "ftx-bankruptcy" in ids
    assert "mt-gox-collapse" in ids


def test_point_in_time_filter_prevents_future_leakage():
    context = crypto_history_context(
        {"symbol": "BTC-USD"},
        as_of=date(2020, 1, 1),
        limit=12,
    )
    assert all(event["event_date"] <= "2020-01-01" for event in context["events"])
    assert "bitcoin-halving-2020" not in {event["event_id"] for event in context["events"]}


def test_non_crypto_market_gets_no_history_context():
    context = crypto_history_context({"symbol": "AAPL"}, market="cash")
    assert context["enabled"] is False
    assert context["events"] == []
    assert context["influences_decision"] is False


def test_oracle_exposes_history_without_granting_execution_authority():
    from oracle_intelligence import evaluate_opportunity

    signal = {
        "symbol": "BTC-USD",
        "action": "BUY",
        "score": 0.91,
        "confidence": 0.88,
        "momentum_5d": 0.05,
        "momentum_20d": 0.11,
        "trend_strength": 0.06,
        "volume_ratio": 1.7,
        "volatility_20d": 0.22,
        "atr_pct": 0.018,
        "news_sentiment": 0.6,
        "relative_strength": 0.12,
        "spread_pct": 0.0005,
        "estimated_slippage_pct": 0.0004,
        "event_risk_score": 10,
    }
    decision = evaluate_opportunity(
        signal,
        market="crypto",
        historical_records=[],
        use_market_memory=False,
    )
    assert decision.crypto_history["enabled"] is True
    assert decision.crypto_history["events"]
    assert decision.crypto_history["influences_decision"] is False
    assert decision.crypto_history["score_adjustment"] == 0.0
