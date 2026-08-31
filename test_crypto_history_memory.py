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
