from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from engine import OracleSignal


def _history(symbol="AAPL", price=123.45, interval="5m", quote_timestamp=None, verified=True):
    quote_timestamp = quote_timestamp or datetime.now(timezone.utc).isoformat()
    frame = pd.DataFrame(
        {
            "Open": [price - 2, price - 1, price],
            "High": [price, price + 1, price + 2],
            "Low": [price - 3, price - 2, price - 1],
            "Close": [price - 1, price - 0.5, price],
            "Volume": [1_000_000, 1_100_000, 1_200_000],
        },
        index=pd.date_range(datetime.now(timezone.utc) - timedelta(minutes=10), periods=3, freq="5min"),
    )
    frame.attrs["provider_route"] = {
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "provider": "unit-provider",
        "price": price,
        "current_price": price,
        "quote_timestamp": quote_timestamp,
        "interval": interval,
        "quote_verified": verified,
        "source_identity": f"unit:{symbol}:5d:{interval}",
        "cache_identity": f"cache:{symbol}:5d:{interval}",
        "ohlcv_fingerprint": f"ohlcv:{symbol}",
    }
    return frame


def _signal(symbol="AAPL", price=0.0, action="BUY"):
    return OracleSignal(
        symbol=symbol,
        price=price,
        score=.95,
        action=action,
        confidence=.9,
        momentum_5d=.05,
        momentum_20d=.15,
        rsi_14=55,
        volatility_20d=.2,
        trend_strength=.1,
        volume_ratio=2.0,
        news_sentiment=.1,
        macd_hist=.03,
        atr_pct=.02,
        bollinger_position=.5,
        regime="risk-on",
        reason="unit",
    )


def _quote(symbol="AAPL", price=100.0, **overrides):
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "symbol": symbol,
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "provider": "unit",
        "price": price,
        "quote_timestamp": now,
        "interval": "5m",
        "quote_verified": True,
        "source_identity": f"unit:{symbol}:5d:5m",
        "cache_identity": f"cache:{symbol}:5d:5m",
        "ohlcv_fingerprint": f"ohlcv:{symbol}",
    }
    payload.update(overrides)
    return payload


def test_verified_stock_quote_reaches_execution_with_price_above_zero(monkeypatch):
    import market_worker
    import oracle_bot

    history = _history("AAPL", 123.45)
    signal = _signal("AAPL", price=0.0)
    route = market_worker._attach_execution_metadata(signal, history, "fast")
    quote = market_worker._quote_payload_from_history("AAPL", history, signal.price, scan_type="fast")
    assert signal.price == 123.45
    assert quote["price"] == 123.45
    assert quote["quote_verified"] is True
    assert quote["requested_symbol"] == "AAPL"
    assert quote["provider_symbol"] == "AAPL"
    assert quote["scan_type"] == "fast"

    captured = {}
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_STOCK_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_NEW_ENTRIES", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_QUANT_TRADE_STANDARD", False)
    monkeypatch.setattr(oracle_bot, "_entry_forecast_gate", lambda *args, **kwargs: (True, "fresh"))
    monkeypatch.setattr(oracle_bot, "_penny_stock_gate", lambda *args, **kwargs: (True, "not penny"))
    monkeypatch.setattr(oracle_bot, "recent_trade", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "_open_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: None)

    def fake_buy(market, symbol, price, signal_obj, *args, **kwargs):
        captured["price"] = price
        captured["scan_type"] = getattr(signal_obj, "scan_type", None)
        captured["verified_quote"] = kwargs.get("verified_quote")
        return True, "paper buy executed", None

    monkeypatch.setattr(oracle_bot, "_buy", fake_buy)
    actions = oracle_bot.process_signals("cash", [signal], {"AAPL": quote})
    assert captured["price"] == 123.45
    assert captured["scan_type"] == "fast"
    assert captured["verified_quote"]["price"] == 123.45
    assert actions and actions[0]["price"] == 123.45


def test_price_never_silently_becomes_zero_and_missing_price_rejects(monkeypatch):
    import market_worker
    import oracle_bot

    history = _history("AAPL", 77.7)
    history.attrs["provider_route"].pop("price")
    history.attrs["provider_route"].pop("current_price")
    signal = _signal("AAPL", price=0.0)
    market_worker._attach_execution_metadata(signal, history, "deep")
    quote = market_worker._quote_payload_from_history("AAPL", history, signal.price, scan_type="deep")
    assert quote["price"] == 77.7

    monkeypatch.setattr(oracle_bot, "_buy", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("buy should not execute")))
    missing = _quote("AAPL", price=None)
    assert oracle_bot.process_signals("cash", [_signal("AAPL", 0.0)], {"AAPL": missing}) == []


def test_signal_price_cannot_substitute_for_missing_verified_provider_price():
    import market_worker

    history = pd.DataFrame(
        {"Close": [float("nan"), float("nan")], "Volume": [1_000, 1_100]},
        index=pd.date_range(datetime.now(timezone.utc) - timedelta(minutes=10), periods=2, freq="5min"),
    )
    history.attrs["provider_route"] = {
        "requested_symbol": "AAPL",
        "provider_symbol": "AAPL",
        "provider": "unit-provider",
        "quote_timestamp": datetime.now(timezone.utc).isoformat(),
        "interval": "5m",
        "quote_verified": True,
    }
    signal = _signal("AAPL", price=199.99)

    quote = market_worker._quote_payload_from_history("AAPL", history, signal.price, scan_type="fast")

    assert quote["price"] is None
    assert market_worker._execution_quote_payload_from_history("AAPL", history, signal.price, scan_type="fast") is None

def test_quote_payload_uses_observed_liquidity_and_verified_identity():
    import market_worker

    history = _history("AAPL", 100.0)
    quote = market_worker._quote_payload_from_history("AAPL", history, 100.0, scan_type="fast")

    assert quote["requested_symbol"] == "AAPL"
    assert quote["provider_symbol"] == "AAPL"
    assert quote["avg_dollar_volume"] > 0
    assert quote["tradeable"] is True



def test_unverified_and_stale_quotes_reject_before_execution(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "_buy", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("buy should not execute")))
    assert oracle_bot.process_signals("cash", [_signal("AAPL", 100.0)], {"AAPL": _quote("AAPL", 100.0, quote_verified=False)}) == []
    stale = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert oracle_bot.process_signals("cash", [_signal("AAPL", 100.0)], {"AAPL": _quote("AAPL", 100.0, quote_timestamp=stale, interval="5m")}) == []


def test_closed_stock_session_cannot_fake_realtime_quote(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "_buy", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("buy should not execute")))
    friday_daily = "2026-07-31T20:00:00+00:00"
    signal = _signal("AAPL", 100.0)
    signal.scan_type = "fast"
    quote = _quote("AAPL", 100.0, quote_timestamp=friday_daily, interval="5m")
    assert oracle_bot.process_signals("cash", [signal], {"AAPL": quote}) == []


def test_crypto_and_stock_fast_deep_signals_preserve_scan_type():
    import market_worker

    for market, symbol in (("crypto", "SOL-USD"), ("cash", "AAPL")):
        for scan_type in ("fast", "deep"):
            signal = _signal(symbol, 0.0)
            history = _history(symbol, 44.4, interval="5m" if scan_type == "fast" else "1d")
            route = market_worker._attach_execution_metadata(signal, history, scan_type)
            payload = market_worker._signal_payload(signal, route, scan_type)
            quote = market_worker._quote_payload_from_history(symbol, history, signal.price, scan_type=scan_type)
            assert signal.scan_type == scan_type
            assert payload["scan_type"] == scan_type
            assert payload["market_data_route"]["scan_type"] == scan_type
            assert quote["scan_type"] == scan_type


def test_missing_scan_type_remains_fail_closed(monkeypatch):
    import oracle_bot

    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(oracle_bot, "REQUIRE_TARGET_FOR_BUY", True)
    monkeypatch.setattr(oracle_bot, "model_execution_approved", lambda *args, **kwargs: (True, "approved"))
    monkeypatch.setattr(
        oracle_bot,
        "row",
        lambda *args, **kwargs: {
            "signal_id": 42,
            "target_price": 110,
            "low_price": 95,
            "high_price": 120,
            "probability_up": .7,
            "created_at": now,
            "requested_symbol": "BTC-USD",
            "provider_symbol": "BTC-USD",
            "source_interval": "5m",
            "source_quote_timestamp": now,
            "scan_type": "fast",
            "model": "unit",
            "model_version": "v1",
            "expected_move_pct": 10,
            "data_quality_score": 99,
            "forecast_id": "fcst",
            "symbol": "BTC-USD",
        },
    )
    ok, reason = oracle_bot._entry_forecast_gate(
        "crypto",
        "BTC-USD",
        100,
        SimpleNamespace(symbol="BTC-USD", signal_id=42, source_interval="5m"),
        _quote("BTC-USD", 100, interval="5m", quote_timestamp=now),
    )
    assert ok is False
    assert reason == "signal scan type is missing"


def test_successful_paper_entries_reduce_deployment_gap_and_protect_reserve():
    from global_pit_engine import capital_deployment_plan

    queue = [
        {"symbol": "AAPL", "qualified_for_capital": True, "opportunity_score": 95},
        {"symbol": "MSFT", "qualified_for_capital": True, "opportunity_score": 90},
    ]
    before = capital_deployment_plan(queue, equity=1_000_000, cash=500_000, positions=[])
    after = capital_deployment_plan(queue, equity=1_000_000, cash=420_000, positions=[{"symbol": "AAPL", "market_value": 80_000}])
    assert before["deployment_gap"] > after["deployment_gap"]
    assert sum(item["amount"] for item in before["allocations"]) <= 450_000
    assert before["reserve_cash_required"] == 50_000


def test_diversification_limits_remain_enforced():
    from global_pit_engine import capital_deployment_plan, GLOBAL_PIT_MAX_POSITION_PCT

    queue = [{"symbol": "AAPL", "qualified_for_capital": True, "opportunity_score": 100}]
    plan = capital_deployment_plan(queue, equity=1_000_000, cash=900_000, positions=[])
    assert plan["allocations"][0]["amount"] <= 1_000_000 * GLOBAL_PIT_MAX_POSITION_PCT


def test_capital_deployment_status_explains_real_blockers():
    from dashboard_helpers import capital_deployment_status

    metrics = {"equity": 100_000, "cash": 80_000}
    base = {"market": "cash", "action": "BUY", "trade_eligible": True, "quote_verified": True, "quote_timestamp": datetime.now(timezone.utc).isoformat()}
    assert capital_deployment_status(metrics, [], market="cash", session_label="closed")["message"].startswith("Stock capital deployment paused")
    blocked = capital_deployment_status(metrics, [{**base, "quote_timestamp": ""}], market="cash")
    assert blocked["status"] == "blocked_quote"
    assert "candidate quote failed execution verification" in blocked["message"]
    metadata = capital_deployment_status(metrics, [{**base}], market="cash")
    assert metadata["status"] == "blocked_metadata"
    assert "signal metadata incomplete" in metadata["message"]
    qualified = capital_deployment_status(metrics, [{**base, "scan_type": "fast", "source_interval": "5m", "requested_symbol": "AAPL", "provider_symbol": "AAPL"}], market="cash")
    assert qualified["message"].startswith("Too much capital is sitting in cash")


def test_enable_broker_submission_remains_false():
    import config

    assert config.ENABLE_BROKER_SUBMISSION is False


def test_v39_optimizer_amount_reaches_buy_and_caps_execution(monkeypatch):
    import oracle_bot

    signal = _signal("AAPL", price=100.0)
    signal.signal_id = 42
    signal.scan_type = "fast"
    signal.source_interval = "5m"
    signal.planned_trade_value = 600_000
    signal.v39_optimizer_approved_amount = 600_000
    quote = _quote("AAPL", 100.0, avg_dollar_volume=100_000_000)
    captured = {}

    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_STOCK_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_NEW_ENTRIES", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_QUANT_TRADE_STANDARD", False)
    monkeypatch.setattr(oracle_bot, "_entry_forecast_gate", lambda *args, **kwargs: (True, "approved"))
    monkeypatch.setattr(oracle_bot, "_penny_stock_gate", lambda *args, **kwargs: (True, "not penny"))
    monkeypatch.setattr(oracle_bot, "recent_trade", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "_open_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: None)

    def fake_buy(market, symbol, price, signal_obj, *args, **kwargs):
        captured["target_trade_value"] = kwargs.get("target_trade_value")
        return True, "paper buy executed", None

    monkeypatch.setattr(oracle_bot, "_buy", fake_buy)

    actions = oracle_bot.process_signals("cash", [signal], {"AAPL": quote})

    assert captured["target_trade_value"] == 600_000
    assert actions[0]["optimizer_approved_amount"] == 600_000
    assert actions[0]["planned_trade_value"] <= 600_000


def test_v39_optimizer_uses_stricter_oracle_sizing(monkeypatch):
    import oracle_bot
    from quant_trade_standard import QuantTradeAssessment

    signal = _signal("AAPL", price=100.0)
    signal.signal_id = 42
    signal.scan_type = "fast"
    signal.source_interval = "5m"
    signal.planned_trade_value = 600_000
    quote = _quote("AAPL", 100.0, avg_dollar_volume=100_000_000)
    captured = {}

    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_STOCK_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_NEW_ENTRIES", True)
    monkeypatch.setattr(oracle_bot, "_entry_forecast_gate", lambda *args, **kwargs: (True, "approved"))
    monkeypatch.setattr(oracle_bot, "_penny_stock_gate", lambda *args, **kwargs: (True, "not penny"))
    monkeypatch.setattr(oracle_bot, "recent_trade", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "_open_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "portfolio_equity", lambda market: {"equity": 1_000_000})
    monkeypatch.setattr(oracle_bot, "rows", lambda *args, **kwargs: [])

    class Decision:
        recommendation = "BUY"
        reason = "unit"
        scenario = {"position_multiplier": 1.0, "summary": ""}
        global_intelligence = {"position_multiplier": 1.0, "summary": ""}
        capital = {"final_multiplier": 1.0, "summary": ""}
        radar = {"position_multiplier": 1.0, "summary": ""}
        portfolio_supercomputer = {"position_multiplier": 1.0, "recommended_trade_value": 450_000, "summary": ""}
        explainability = {"summary": ""}

    assessment = QuantTradeAssessment(
        alpha_score=90,
        execution_score=90,
        risk_score=90,
        relative_value_score=90,
        trade_quality=90,
        gross_expected_value_pct=0.02,
        estimated_cost_pct=0.001,
        net_expected_value_pct=0.019,
        adverse_selection_score=10,
        position_multiplier=1.0,
        approved=True,
        classification="approved",
        reason="unit",
    )

    monkeypatch.setattr(oracle_bot, "evaluate_opportunity", lambda *args, **kwargs: Decision())
    monkeypatch.setattr(oracle_bot, "assess_trade", lambda *args, **kwargs: assessment)

    def fake_buy(market, symbol, price, signal_obj, *args, **kwargs):
        captured["target_trade_value"] = kwargs.get("target_trade_value")
        return True, "paper buy executed", None

    monkeypatch.setattr(oracle_bot, "_buy", fake_buy)

    actions = oracle_bot.process_signals("cash", [signal], {"AAPL": quote})

    assert captured["target_trade_value"] == 450_000
    assert actions[0]["planned_trade_value"] == 450_000
    assert actions[0]["optimizer_approved_amount"] == 600_000


def test_v39_signal_opportunity_does_not_mark_signal_id_as_forecast_approved():
    import market_worker

    signal = _signal("AAPL", price=100.0)
    signal.signal_id = 123
    signal.forecast_id = "forecast-123"
    quote = _quote("AAPL", 100.0, avg_dollar_volume=25_000_000, spread_pct=0.001, tradeable=True)
    opportunity = market_worker._v39_signal_opportunity(
        "cash",
        signal,
        {"AAPL": quote},
        {"AAPL": {"risk_score": 25, "spread_pct": 0.001, "liquidity": 25_000_000}},
        "fast",
    )

    assert "forecast_approved" not in opportunity["stages"]
    assert opportunity["qualified_for_capital"] is True


def test_v39_signal_opportunity_requires_forecast_id_for_capital():
    import market_worker

    signal = _signal("AAPL", price=100.0)
    signal.signal_id = 123
    quote = _quote("AAPL", 100.0, avg_dollar_volume=25_000_000, spread_pct=0.001, tradeable=True)

    opportunity = market_worker._v39_signal_opportunity(
        "cash",
        signal,
        {"AAPL": quote},
        {"AAPL": {"risk_score": 25, "spread_pct": 0.001, "liquidity": 25_000_000, "sector": "Technology"}},
        "fast",
    )

    assert opportunity["qualified_for_capital"] is False


def test_v39_iterative_execution_reloads_optimizer_after_success(monkeypatch):
    import market_worker

    signals = [_signal("AAPL", 100.0), _signal("MSFT", 200.0)]
    prices = {"AAPL": _quote("AAPL", 100.0), "MSFT": _quote("MSFT", 200.0)}
    calls = []

    def fake_prioritize(market, remaining, prices_arg, ranked, scan_type):
        calls.append([item.symbol for item in remaining])
        for item in remaining:
            item.planned_trade_value = 1_000
            item.v39_optimizer_approved_amount = 1_000
            item.v39_optimizer_allocation = {"symbol": item.symbol, "amount": 1_000}
        return list(remaining)

    def fake_process(market, batch, prices=None):
        return [{"symbol": batch[0].symbol, "action": "BUY"}]

    monkeypatch.setattr(market_worker, "_v39_prioritize_signals", fake_prioritize)
    monkeypatch.setattr(market_worker, "process_signals", fake_process)

    actions = market_worker._v39_execute_iterative("cash", signals, prices, [], "fast")

    assert [action["symbol"] for action in actions] == ["AAPL", "MSFT"]
    assert calls == [["AAPL", "MSFT"], ["MSFT"]]


def test_v39_optimizer_rejection_cannot_execute_buy(monkeypatch):
    import market_worker

    signal = _signal("AAPL", 100.0)
    prices = {"AAPL": _quote("AAPL", 100.0)}
    process_calls = []
    events = []

    monkeypatch.setattr(market_worker, "GLOBAL_PIT_MODE", True)
    monkeypatch.setattr(market_worker, "_v39_prioritize_signals", lambda *args, **kwargs: [signal])
    monkeypatch.setattr(market_worker, "process_signals", lambda *args, **kwargs: process_calls.append(args) or [])
    monkeypatch.setattr(
        market_worker,
        "_v39_record_event",
        lambda market, symbol, stage, payload, rejection_reason=None: events.append((stage, rejection_reason)),
    )

    actions = market_worker._v39_execute_iterative("cash", [signal], prices, [], "fast")

    assert actions == []
    assert process_calls == []
    assert ("portfolio_rejected", "optimizer_allocation_required") in events


def test_v39_optimizer_amount_is_upper_bound(monkeypatch):
    import oracle_bot
    from quant_trade_standard import QuantTradeAssessment

    signal = _signal("AAPL", price=100.0)
    signal.signal_id = 42
    signal.forecast_id = "forecast-42"
    signal.scan_type = "fast"
    signal.source_interval = "5m"
    signal.planned_trade_value = 1_000
    quote = _quote("AAPL", 100.0, avg_dollar_volume=100_000_000, sector="Technology")
    captured = {}

    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_STOCK_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_NEW_ENTRIES", True)
    monkeypatch.setattr(oracle_bot, "_entry_forecast_gate", lambda *args, **kwargs: (True, "approved"))
    monkeypatch.setattr(oracle_bot, "_penny_stock_gate", lambda *args, **kwargs: (True, "not penny"))
    monkeypatch.setattr(oracle_bot, "recent_trade", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "_open_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "portfolio_equity", lambda market: {"equity": 1_000_000})
    monkeypatch.setattr(oracle_bot, "rows", lambda *args, **kwargs: [])

    class Decision:
        recommendation = "BUY"
        reason = "unit"
        scenario = {"position_multiplier": 1.0, "summary": ""}
        global_intelligence = {"position_multiplier": 1.0, "summary": ""}
        capital = {"final_multiplier": 1.0, "summary": ""}
        radar = {"position_multiplier": 1.0, "summary": ""}
        portfolio_supercomputer = {"position_multiplier": 1.0, "recommended_trade_value": 50_000, "summary": ""}
        explainability = {"summary": ""}

    assessment = QuantTradeAssessment(
        alpha_score=90,
        execution_score=90,
        risk_score=90,
        relative_value_score=90,
        trade_quality=90,
        gross_expected_value_pct=0.02,
        estimated_cost_pct=0.001,
        net_expected_value_pct=0.019,
        adverse_selection_score=10,
        position_multiplier=1.0,
        approved=True,
        classification="approved",
        reason="unit",
    )

    monkeypatch.setattr(oracle_bot, "evaluate_opportunity", lambda *args, **kwargs: Decision())
    monkeypatch.setattr(oracle_bot, "assess_trade", lambda *args, **kwargs: assessment)

    def fake_buy(market, symbol, price, signal_obj, *args, **kwargs):
        captured["target_trade_value"] = kwargs.get("target_trade_value")
        return True, "paper buy executed", None

    monkeypatch.setattr(oracle_bot, "_buy", fake_buy)

    oracle_bot.process_signals("cash", [signal], {"AAPL": quote})

    assert captured["target_trade_value"] == 1_000


def test_closed_stock_market_stale_intraday_quote_reports_precise_reason(caplog):
    import oracle_bot

    signal = _signal("AAPL", price=100.0)
    signal.signal_id = 1
    signal.forecast_id = "forecast-1"
    stale_quote = _quote(
        "AAPL",
        100.0,
        quote_timestamp=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        interval="5m",
        exchange="NASDAQ",
    )

    with caplog.at_level("INFO", logger="oracle-bot"):
        actions = oracle_bot.process_signals("cash", [signal], {"AAPL": stale_quote})

    assert actions == []
    assert "MARKET_CLOSED_STALE_INTRADAY_QUOTE" in caplog.text or "verified quote timestamp is stale" in caplog.text


def test_valid_regular_session_quote_keeps_positive_price(monkeypatch):
    import market_worker

    timestamp = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc).isoformat()
    history = _history("AAPL", price=123.45, interval="5m", quote_timestamp=timestamp)
    signal = _signal("AAPL", price=0.0)

    route = market_worker._attach_execution_metadata(signal, history, "fast")
    quote = market_worker._quote_payload_from_history("AAPL", history, signal.price, scan_type="fast")

    assert signal.price == pytest.approx(123.45)
    assert route["price"] == pytest.approx(123.45)
    assert quote["price"] == pytest.approx(123.45)


def test_v39_executed_action_records_sequential_funnel_stages(monkeypatch):
    import market_worker

    captured = []
    monkeypatch.setattr(
        market_worker,
        "_v39_record_event",
        lambda market, symbol, stage, payload, rejection_reason=None: captured.append((stage, payload)),
    )

    market_worker._v39_record_actions(
        "cash",
        [
            {
                "symbol": "AAPL",
                "action": "BUY",
                "signal_id": 42,
                "forecast_id": "forecast-42",
                "created_at": "2026-01-01T10:00:00+00:00",
                "planned_trade_value": 450_000,
                "optimizer_approved_amount": 600_000,
            }
        ],
    )

    assert [stage for stage, _ in captured] == [
        "forecast_approved",
        "portfolio_approved",
        "execution_approved",
        "paper_trade_executed",
    ]
    assert all(payload["signal_id"] == 42 for _, payload in captured)
    assert all(payload["forecast_id"] == "forecast-42" for _, payload in captured)
