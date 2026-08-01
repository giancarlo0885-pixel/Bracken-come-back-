from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from realtime_runtime import cadence_for
from engine import OracleSignal
from market_sessions import quote_is_fresh


def _oracle_signal(symbol: str = "SOUN", price: float = 2.0) -> OracleSignal:
    return OracleSignal(
        symbol=symbol,
        price=price,
        score=.92,
        action="BUY",
        confidence=.86,
        momentum_5d=.04,
        momentum_20d=.12,
        rsi_14=58,
        volatility_20d=.35,
        trend_strength=.08,
        volume_ratio=2.0,
        news_sentiment=.2,
        macd_hist=.03,
        atr_pct=.04,
        bollinger_position=.55,
        regime="risk-on",
        reason="unit signal",
    )


def _verified_candidate(**overrides):
    now = datetime.now(timezone.utc).isoformat()
    candidate = {
        "symbol": "SOUN",
        "exchange": "NASDAQ",
        "price": 2.0,
        "daily_volume": 1_200_000,
        "avg_dollar_volume": 3_000_000,
        "primary_category": "penny_stock",
        "mover_tags": ["major_gainer"],
        "discovery_source": "polygon_snapshot",
        "discovery_timestamp": now,
        "quote_timestamp": now,
        "data_freshness_seconds": 30,
        "scanned_at": now,
        "tradeable": True,
        "risk_bucket": "strict_penny_controls",
        "payload": {},
    }
    candidate.update(overrides)
    return candidate


def _verified_quote(symbol: str = "AAPL", price: float = 100.0, **overrides):
    now = datetime.now(timezone.utc).isoformat()
    quote = {
        "symbol": symbol,
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "provider": "unit",
        "price": price,
        "quote_timestamp": now,
        "interval": "1d",
        "quote_verified": True,
        "source_identity": f"unit:{symbol}",
        "cache_identity": f"cache:{symbol}",
        "ohlcv_fingerprint": f"ohlcv:{symbol}",
    }
    quote.update(overrides)
    return quote


def test_always_on_runtime_has_fast_and_deep_cadences():
    stock = cadence_for("cash")
    crypto = cadence_for("crypto")
    assert stock.fast_scan_seconds >= 5
    assert crypto.fast_scan_seconds >= 5
    assert stock.deep_scan_seconds >= stock.fast_scan_seconds
    assert crypto.deep_scan_seconds >= crypto.fast_scan_seconds


def test_worker_has_rolling_fast_scan_and_auto_recovery():
    source = Path("market_worker.py").read_text()
    assert "def fast_scan_market" in source
    assert "fast_executor.submit(fast_scan_market" in source
    assert "Automatic recovery active" in source
    assert "No qualified trade this pass; scanning continues." in source
    assert "trade_cycle_lock" in source


def test_continuous_rotation_is_available_when_portfolio_is_full():
    source = Path("oracle_bot.py").read_text()
    assert "def _rotate_for_stronger_candidate" in source
    assert "continuous_rotation_to_" in source
    assert "no superior rotation" in source


def test_always_on_variables_are_documented():
    variables = Path("railway_variables.example").read_text()
    for name in (
        "ALWAYS_ON_TRADING",
        "FAST_SIGNAL_SCAN_ENABLED",
        "STOCK_FAST_SCAN_SECONDS",
        "CRYPTO_FAST_SCAN_SECONDS",
        "FAST_SCAN_BATCH_SIZE",
        "FAST_SCAN_TOP_RANKED",
        "WORKER_CYCLE_ERROR_BACKOFF_SECONDS",
    ):
        assert f"{name}=" in variables


def test_dashboard_exposes_always_on_status():
    source = Path("app.py").read_text()
    assert "Always-On Institutional Paper Broker" in source
    assert "fast_scan_seconds" in source
    assert "Auto recovery" in source


def test_rejected_replacement_buy_does_not_emit_rotation_sell(monkeypatch):
    import oracle_bot

    rotation_candidate = {
        "market": "cash",
        "symbol": "OLD",
        "quantity": 1,
        "current_price": 10,
        "average_price": 9,
        "_rotation_action": {
            "market": "cash",
            "symbol": "OLD",
            "action": "SELL",
            "price": 10,
            "reason": "continuous_rotation_to_NEW",
            "rotation_target": "NEW",
        },
    }
    captured = {}

    monkeypatch.setattr(oracle_bot, "ENABLE_QUANT_TRADE_STANDARD", False)
    monkeypatch.setattr(oracle_bot, "DEFAULT_MAX_OPEN_POSITIONS", 1)
    monkeypatch.setattr(oracle_bot, "EXTRA_OPEN_POSITIONS", 0)
    monkeypatch.setattr(oracle_bot, "_entry_forecast_gate", lambda *args, **kwargs: (True, "fresh"))
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "_open_position_count", lambda market: 1)
    monkeypatch.setattr(oracle_bot, "recent_trade", lambda market, symbol: None)
    monkeypatch.setattr(oracle_bot, "_rotate_for_stronger_candidate", lambda *args, **kwargs: rotation_candidate)

    def reject_buy(*args, **kwargs):
        captured["rotation_candidate"] = kwargs.get("rotation_candidate")
        return False, "risk rejected", None

    monkeypatch.setattr(oracle_bot, "_buy", reject_buy)
    now = datetime.now(timezone.utc).isoformat()
    actions = oracle_bot.process_signals(
        "cash",
        [{
            "symbol": "NEW",
            "action": "BUY",
            "score": 95,
            "confidence": .95,
            "price": 20,
            "market_data_route": {
                "requested_symbol": "NEW",
                    "provider_symbol": "NEW",
                    "quote_timestamp": now,
                    "interval": "1d",
                    "quote_verified": True,
                },
            }],
            {"NEW": _verified_quote("NEW", 20, quote_timestamp=now)},
    )
    assert captured["rotation_candidate"] is rotation_candidate
    assert actions == []


def test_buy_portfolio_row_missing_returns_three_value_tuple(monkeypatch):
    import oracle_bot

    class MissingPortfolioConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *args, **kwargs):
            return self

        def fetchone(self):
            return None

    monkeypatch.setattr(
        oracle_bot,
        "portfolio_equity",
        lambda market: {
            "equity": 10_000,
            "buying_power": 10_000,
            "gross_exposure": 0,
            "leverage_limit": 1,
            "leverage_used": 0,
        },
    )
    monkeypatch.setattr(oracle_bot, "connect", lambda: MissingPortfolioConnection())

    result = oracle_bot._buy(
        "cash",
        "NEW",
        100,
        {
            "symbol": "NEW",
            "score": 90,
            "confidence": .9,
            "price": 100,
            "market_data_route": {
                "requested_symbol": "NEW",
                    "provider_symbol": "NEW",
                    "quote_timestamp": datetime.now(timezone.utc).isoformat(),
                    "interval": "1d",
                    "quote_verified": True,
                },
            },
        )
    assert result == (False, "portfolio row missing", None)


def test_penny_stock_gate_rejects_stale_or_illiquid_data(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "_penny_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: None)
    stale = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    ok, reason = oracle_bot._penny_stock_gate(
        "cash",
        "SOUN",
        2.0,
        {"quote_timestamp": stale, "scanned_at": now, "tradeable": True, "exchange": "NASDAQ", "volume": 1_000_000, "avg_dollar_volume": 3_000_000, "primary_category": "penny_stock", "discovery_source": "unit"},
        90,
        .9,
    )
    assert ok is False
    assert "stale" in reason

    monkeypatch.setattr(oracle_bot, "quote_is_fresh", lambda *args, **kwargs: True)
    ok, reason = oracle_bot._penny_stock_gate(
        "cash",
        "SOUN",
        2.0,
        {"quote_timestamp": now, "scanned_at": now, "tradeable": True, "exchange": "NASDAQ", "volume": 1_000, "avg_dollar_volume": 3_000_000, "primary_category": "penny_stock", "discovery_source": "unit"},
        90,
        .9,
    )
    assert ok is False
    assert "volume" in reason


def test_penny_stock_gate_rejects_normal_quality_and_otc(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "_penny_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: None)
    now = datetime.now(timezone.utc).isoformat()
    ok, reason = oracle_bot._penny_stock_gate(
        "cash",
        "OTC",
        2.0,
        {"created_at": now, "exchange": "OTC", "volume": 1_000_000, "avg_dollar_volume": 3_000_000},
        90,
        .9,
    )
    assert ok is False
    assert "OTC" in reason

    ok, reason = oracle_bot._penny_stock_gate(
        "cash",
        "SOUN",
        2.0,
        {"quote_timestamp": now, "scanned_at": now, "tradeable": True, "exchange": "NASDAQ", "volume": 1_000_000, "avg_dollar_volume": 3_000_000, "primary_category": "penny_stock", "discovery_source": "unit"},
        70,
        .9,
    )
    assert ok is False
    assert "score" in reason


def test_real_oracle_signal_qualified_penny_can_pass_with_verified_metadata(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "_penny_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: _verified_candidate())
    monkeypatch.setattr(oracle_bot, "quote_is_fresh", lambda *args, **kwargs: True)

    ok, reason = oracle_bot._penny_stock_gate("cash", "SOUN", 2.0, _oracle_signal(), 92, .86)
    assert ok is True
    assert reason == "penny-stock controls passed"


def test_real_oracle_signal_penny_rejects_missing_metadata(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "_penny_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: None)

    ok, reason = oracle_bot._penny_stock_gate("cash", "SOUN", 2.0, _oracle_signal(), 92, .86)
    assert ok is False
    assert "exchange metadata is missing" in reason


def test_real_oracle_signal_penny_rejects_stale_verified_metadata(monkeypatch):
    import oracle_bot

    stale = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    monkeypatch.setattr(oracle_bot, "_penny_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: _verified_candidate(quote_timestamp=stale))

    ok, reason = oracle_bot._penny_stock_gate("cash", "SOUN", 2.0, _oracle_signal(), 92, .86)
    assert ok is False
    assert "stale" in reason


def test_real_oracle_signal_penny_allows_valid_weekend_daily_metadata(monkeypatch):
    import oracle_bot

    saturday = datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc)
    candidate = _verified_candidate(
        quote_timestamp="2026-07-31T20:00:00+00:00",
        scanned_at=datetime.now(timezone.utc).isoformat(),
    )
    monkeypatch.setattr(oracle_bot, "_penny_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: candidate)
    monkeypatch.setattr(
        oracle_bot,
        "quote_is_fresh",
        lambda value, interval, **kwargs: quote_is_fresh(value, interval, saturday, exchange=kwargs.get("exchange")),
    )

    ok, reason = oracle_bot._penny_stock_gate("cash", "SOUN", 2.0, _oracle_signal(), 92, .86)
    assert ok is True
    assert reason == "penny-stock controls passed"


def test_real_oracle_signal_penny_rejects_stale_database_candidate(monkeypatch):
    import oracle_bot

    old_scan = (datetime.now(timezone.utc) - timedelta(seconds=oracle_bot.GLOBAL_CANDIDATE_TTL_SECONDS + 5)).isoformat()
    monkeypatch.setattr(oracle_bot, "_penny_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: _verified_candidate(scanned_at=old_scan))
    monkeypatch.setattr(oracle_bot, "quote_is_fresh", lambda *args, **kwargs: True)

    ok, reason = oracle_bot._penny_stock_gate("cash", "SOUN", 2.0, _oracle_signal(), 92, .86)
    assert ok is False
    assert "expired" in reason


def test_real_oracle_signal_penny_rejects_non_tradeable_candidate(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "_penny_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: _verified_candidate(tradeable=False))
    monkeypatch.setattr(oracle_bot, "quote_is_fresh", lambda *args, **kwargs: True)

    ok, reason = oracle_bot._penny_stock_gate("cash", "SOUN", 2.0, _oracle_signal(), 92, .86)
    assert ok is False
    assert "not tradeable" in reason


def test_penny_portfolio_limit_allows_below_limit():
    import oracle_bot

    positions = [{"symbol": "OLD", "quantity": 10, "current_price": 2.0}]
    total, pct = oracle_bot._penny_portfolio_exposure_after(positions, 10_000, 100)
    assert total == 120
    assert pct < oracle_bot.PENNY_STOCK_MAX_PORTFOLIO_PCT


def test_penny_portfolio_limit_rejects_above_limit():
    import oracle_bot

    positions = [{"symbol": "OLD", "quantity": 70, "current_price": 2.0}]
    total, pct = oracle_bot._penny_portfolio_exposure_after(positions, 10_000, 100)
    assert total == 240
    assert pct > oracle_bot.PENNY_STOCK_MAX_PORTFOLIO_PCT


def test_execution_rejects_signal_quote_symbol_mismatch():
    import oracle_bot

    now = datetime.now(timezone.utc).isoformat()
    ok, reason = oracle_bot._execution_quote_guard(
        "cash",
        "F",
        12.34,
        {
            "symbol": "F",
            "market_data_route": {
                "requested_symbol": "GM",
                "provider_symbol": "GM",
                "quote_timestamp": now,
                "interval": "1d",
            },
        },
    )
    assert ok is False
    assert "identity mismatch" in reason


def test_duplicate_price_anomaly_blocks_execution(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "_entry_forecast_gate", lambda *args, **kwargs: (True, "fresh"))
    monkeypatch.setattr(oracle_bot, "_penny_stock_gate", lambda *args, **kwargs: (True, "not penny"))
    monkeypatch.setattr(oracle_bot, "ENABLE_QUANT_TRADE_STANDARD", False)

    def buy_should_not_run(*args, **kwargs):
        raise AssertionError("duplicate-price quarantined signals must not execute")

    monkeypatch.setattr(oracle_bot, "_buy", buy_should_not_run)
    now = datetime.now(timezone.utc).isoformat()
    signals = [
        {
            "symbol": symbol,
            "action": "BUY",
            "score": 95,
            "confidence": .95,
            "market_data_route": _verified_quote(
                symbol,
                88.59,
                quote_timestamp=now,
                cache_identity="same-cache-entry",
                ohlcv_fingerprint="same-ohlcv",
            ),
        }
        for symbol in ("GM", "F", "AAPL")
    ]
    actions = oracle_bot.process_signals(
        "cash",
        signals,
        {symbol: _verified_quote(symbol, 88.59, cache_identity="same-cache-entry", ohlcv_fingerprint="same-ohlcv") for symbol in ("GM", "F", "AAPL")},
    )
    assert actions == []


def test_enable_autotrade_false_blocks_buys(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", False)
    ok, reason, rotation = oracle_bot._buy(
        "cash",
        "AAPL",
        100,
        {"symbol": "AAPL", "market_data_route": _verified_quote("AAPL", 100)},
    )
    assert ok is False
    assert reason == "autotrade disabled"
    assert rotation is None


def test_enable_autotrade_false_blocks_sell_signals(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", False)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: {"symbol": "AAPL", "quantity": 1})
    monkeypatch.setattr(oracle_bot, "_close_position", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sell executed")))
    actions = oracle_bot.process_signals(
        "cash",
        [{"symbol": "AAPL", "action": "SELL", "price": 100, "market_data_route": _verified_quote("AAPL", 100)}],
        {"AAPL": _verified_quote("AAPL", 100)},
    )
    assert actions == []


def test_enable_autotrade_false_blocks_stop_losses_and_take_profits(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", False)
    monkeypatch.setattr(
        oracle_bot,
        "rows",
        lambda *args, **kwargs: [{"symbol": "AAPL", "quantity": 1, "entry_price": 100, "current_price": 80}],
    )
    monkeypatch.setattr(oracle_bot, "_close_position", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("exit executed")))
    assert oracle_bot.risk_exits("cash", {"AAPL": _verified_quote("AAPL", 80)}) == []


def test_scanning_and_signal_persistence_continue_when_execution_disabled(monkeypatch):
    import market_worker

    saved = {"signals": 0, "forecasts": 0}
    history = SimpleNamespace(
        attrs={
            "provider_route": _verified_quote(
                "AAPL",
                100,
                provider="unit-history",
                quote_timestamp=datetime.now(timezone.utc).isoformat(),
            )
        }
    )

    class Signal:
        symbol = "AAPL"
        price = 100.0
        score = 0.9
        action = "BUY"
        confidence = 0.9

        def to_dict(self):
            return {"symbol": self.symbol, "price": self.price}

    monkeypatch.setattr(market_worker, "ENABLE_AUTOTRADE", False)
    monkeypatch.setattr(market_worker, "_fast_candidate_batch", lambda market: [("AAPL", "Apple")])
    monkeypatch.setattr(market_worker, "_fast_discover_symbol", lambda *args, **kwargs: (Signal(), history))
    monkeypatch.setattr(market_worker, "save_json_signal", lambda *args, **kwargs: saved.__setitem__("signals", saved["signals"] + 1))
    monkeypatch.setattr(market_worker, "forecast_price", lambda *args, **kwargs: {"target_price": 105})
    monkeypatch.setattr(market_worker, "save_forecast", lambda *args, **kwargs: saved.__setitem__("forecasts", saved["forecasts"] + 1))
    monkeypatch.setattr(market_worker, "update_prices", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("update called")))
    monkeypatch.setattr(market_worker, "risk_exits", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("risk called")))
    monkeypatch.setattr(market_worker, "process_signals", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("process called")))
    monkeypatch.setattr(market_worker, "snapshot", lambda *args, **kwargs: None)

    assert market_worker.fast_scan_market("cash") == []
    assert saved == {"signals": 1, "forecasts": 1}


def test_mismatched_price_cannot_update_position_current_price(monkeypatch):
    import oracle_bot

    calls = []
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "rows", lambda *args, **kwargs: [{"symbol": "F", "quantity": 1}])
    monkeypatch.setattr(oracle_bot, "ensure_portfolio", lambda market: {"cash": 1000, "starting_balance": 1000, "margin_debt": 0, "leverage_limit": 4})
    monkeypatch.setattr(oracle_bot, "portfolio_equity", lambda market: {"margin_call": 0.0, "margin_utilization_pct": 0})
    monkeypatch.setattr(oracle_bot, "execute", lambda *args, **kwargs: calls.append(args))
    updated = oracle_bot.update_prices("cash", {"F": _verified_quote("GM", 88.59)})
    assert updated == 0
    assert calls == []


def test_mismatched_price_cannot_trigger_risk_exits(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(
        oracle_bot,
        "rows",
        lambda *args, **kwargs: [{"symbol": "F", "quantity": 1, "entry_price": 100, "highest_price": 120}],
    )
    monkeypatch.setattr(oracle_bot, "_close_position", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("exit executed")))
    assert oracle_bot.risk_exits("cash", {"F": _verified_quote("GM", 50)}) == []


def test_quote_verified_false_is_rejected():
    import oracle_bot

    ok, reason = oracle_bot._execution_quote_guard(
        "cash",
        "AAPL",
        100,
        {"symbol": "AAPL", "market_data_route": _verified_quote("AAPL", 100, quote_verified=False)},
    )
    assert ok is False
    assert "not provider verified" in reason


def test_missing_quote_identity_is_rejected():
    import oracle_bot

    payload = _verified_quote("AAPL", 100)
    payload.pop("requested_symbol")
    ok, reason = oracle_bot._execution_quote_guard(
        "cash",
        "AAPL",
        100,
        {"symbol": "AAPL", "market_data_route": payload},
    )
    assert ok is False
    assert "requested quote symbol is missing" in reason


def test_anomaly_quarantine_runs_before_update_prices_and_risk_exits(monkeypatch):
    import oracle_bot

    quote_a = _verified_quote("AAPL", 100, ohlcv_fingerprint="same-sequence")
    quote_b = _verified_quote("MSFT", 200, ohlcv_fingerprint="same-sequence")
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(
        oracle_bot,
        "rows",
        lambda *args, **kwargs: [
            {"symbol": "AAPL", "quantity": 1, "entry_price": 110, "highest_price": 120},
            {"symbol": "MSFT", "quantity": 1, "entry_price": 210, "highest_price": 220},
            ],
    )
    monkeypatch.setattr(oracle_bot, "ensure_portfolio", lambda market: {"cash": 1000, "starting_balance": 1000, "margin_debt": 0, "leverage_limit": 4})
    monkeypatch.setattr(oracle_bot, "execute", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("price updated")))
    monkeypatch.setattr(oracle_bot, "portfolio_equity", lambda market: {"margin_call": 0.0, "margin_utilization_pct": 0})
    monkeypatch.setattr(oracle_bot, "_close_position", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("exit executed")))
    quotes = {"AAPL": quote_a, "MSFT": quote_b}
    assert oracle_bot.update_prices("cash", quotes) == 0
    assert oracle_bot.risk_exits("cash", quotes) == []


def test_two_legitimate_stocks_same_rounded_price_not_automatically_quarantined():
    import oracle_bot

    quotes = {
        "AAPL": _verified_quote("AAPL", 88.59, source_identity="unit:a", cache_identity="cache:a", ohlcv_fingerprint="ohlcv:a"),
        "MSFT": _verified_quote("MSFT", 88.59, source_identity="unit:b", cache_identity="cache:b", ohlcv_fingerprint="ohlcv:b"),
    }
    assert oracle_bot._duplicate_price_anomaly_symbols(quotes) == set()


def test_corrupted_identical_ohlcv_cache_data_is_quarantined():
    import oracle_bot

    quotes = {
        "AAPL": _verified_quote("AAPL", 88.59, cache_identity="shared-cache", ohlcv_fingerprint="same"),
        "MSFT": _verified_quote("MSFT", 162.00, cache_identity="shared-cache", ohlcv_fingerprint="same"),
    }
    assert oracle_bot._duplicate_price_anomaly_symbols(quotes) == {"AAPL", "MSFT"}


def test_enable_autotrade_false_cannot_accrue_or_alter_margin_debt(monkeypatch):
    import oracle_bot

    portfolio = {
        "market": "cash",
        "cash": 1000,
        "starting_balance": 1000,
        "margin_debt": 500,
        "margin_interest_accrued": 0,
        "margin_interest_updated_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        "leverage_limit": 4,
    }
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", False)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: portfolio)
    monkeypatch.setattr(oracle_bot, "rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(oracle_bot, "_accrue_paper_margin_interest", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("interest accrued")))
    monkeypatch.setattr(oracle_bot, "execute", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("portfolio mutated")))
    equity = oracle_bot.portfolio_equity("cash")
    assert equity["margin_debt"] == 500


def test_enable_autotrade_false_snapshot_does_not_update_portfolios_table(monkeypatch):
    import oracle_bot

    portfolio = {
        "market": "cash",
        "cash": 1000,
        "starting_balance": 1000,
        "margin_debt": 0,
        "margin_interest_accrued": 0,
        "leverage_limit": 4,
    }
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", False)
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: portfolio)
    monkeypatch.setattr(oracle_bot, "rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(oracle_bot, "execute", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("snapshot mutated portfolio")))
    result = oracle_bot.snapshot("cash")
    assert result["equity"] == 1000


def test_process_signals_uses_verified_quote_price_not_signal_price(monkeypatch):
    import oracle_bot

    captured = {}
    quote = _verified_quote("AAPL", 100.0)
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_QUANT_TRADE_STANDARD", False)
    monkeypatch.setattr(oracle_bot, "_entry_forecast_gate", lambda *args, **kwargs: (True, "fresh"))
    monkeypatch.setattr(oracle_bot, "_penny_stock_gate", lambda *args, **kwargs: (True, "not penny"))
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "recent_trade", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "_open_position_count", lambda market: 0)

    def fake_buy(market, symbol, price, signal, *args, **kwargs):
        captured["price"] = price
        captured["verified_quote"] = kwargs.get("verified_quote")
        return True, "ok", None

    monkeypatch.setattr(oracle_bot, "_buy", fake_buy)
    actions = oracle_bot.process_signals(
        "cash",
        [{"symbol": "AAPL", "action": "BUY", "score": 95, "confidence": .95, "price": 100.05, "market_data_route": quote}],
        {"AAPL": quote},
    )
    assert captured["price"] == 100.0
    assert captured["verified_quote"] == quote
    assert actions[0]["price"] == 100.0


def test_signal_verified_quote_price_mismatch_is_rejected(monkeypatch):
    import oracle_bot

    quote = _verified_quote("AAPL", 100.0)
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "_buy", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("buy executed")))
    actions = oracle_bot.process_signals(
        "cash",
        [{"symbol": "AAPL", "action": "BUY", "score": 95, "confidence": .95, "price": 105.0, "market_data_route": quote}],
        {"AAPL": quote},
    )
    assert actions == []


def test_verified_margin_reduction_uses_exact_fresh_symbol_quote(monkeypatch):
    import oracle_bot

    closed = []
    quote = _verified_quote("AAPL", 80.0)
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(
        oracle_bot,
        "rows",
        lambda *args, **kwargs: [{"symbol": "AAPL", "quantity": 1, "entry_price": 100, "current_price": 120}],
    )
    monkeypatch.setattr(oracle_bot, "ensure_portfolio", lambda market: {"cash": 0, "starting_balance": 1000, "margin_debt": 500, "leverage_limit": 4})

    class Account:
        def __init__(self, call, utilization):
            self.margin_call = call
            self.margin_utilization_pct = utilization

    states = iter([Account(True, 101), Account(False, 1)])
    monkeypatch.setattr(oracle_bot, "build_account", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(oracle_bot, "_close_position", lambda market, position, price, reason, **kwargs: closed.append((position["symbol"], price, reason)) or True)
    monkeypatch.setattr(oracle_bot, "execute", lambda *args, **kwargs: None)
    oracle_bot.update_prices("cash", {"AAPL": quote})
    assert closed == [("AAPL", 80.0, oracle_bot.PAPER_MARGIN_REDUCTION_REASON)]


def test_margin_reduction_rejects_missing_stale_or_mismatched_quotes(monkeypatch):
    import oracle_bot

    stale = _verified_quote("AAPL", 80.0, quote_timestamp=(datetime.now(timezone.utc) - timedelta(days=7)).isoformat())
    mismatched = _verified_quote("MSFT", 80.0)
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(
        oracle_bot,
        "rows",
        lambda *args, **kwargs: [{"symbol": "AAPL", "quantity": 1, "entry_price": 100, "current_price": 120}],
    )
    monkeypatch.setattr(oracle_bot, "ensure_portfolio", lambda market: {"cash": 0, "starting_balance": 1000, "margin_debt": 500, "leverage_limit": 4})

    class Account:
        margin_call = True
        margin_utilization_pct = 101

    monkeypatch.setattr(oracle_bot, "build_account", lambda *args, **kwargs: Account())
    monkeypatch.setattr(oracle_bot, "_close_position", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("margin close executed")))
    monkeypatch.setattr(oracle_bot, "execute", lambda *args, **kwargs: None)
    assert oracle_bot.update_prices("cash", {}) == 0
    assert oracle_bot.update_prices("cash", {"AAPL": stale}) == 0
    assert oracle_bot.update_prices("cash", {"AAPL": mismatched}) == 0


def test_margin_reduction_defers_when_one_position_quote_is_missing(monkeypatch):
    import oracle_bot

    closed = []
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(
        oracle_bot,
        "rows",
        lambda *args, **kwargs: [
            {"symbol": "AAPL", "quantity": 1, "entry_price": 100, "current_price": 999},
            {"symbol": "MSFT", "quantity": 1, "entry_price": 200, "current_price": 999},
        ],
    )
    monkeypatch.setattr(oracle_bot, "ensure_portfolio", lambda market: {"cash": 0, "starting_balance": 1000, "margin_debt": 500, "leverage_limit": 4})
    monkeypatch.setattr(oracle_bot, "_close_position", lambda *args, **kwargs: closed.append(args) or True)
    monkeypatch.setattr(oracle_bot, "execute", lambda *args, **kwargs: None)
    updated = oracle_bot.update_prices("cash", {"AAPL": _verified_quote("AAPL", 80)})
    assert updated == 1
    assert closed == []


def test_margin_reduction_defers_when_any_position_quote_is_stale(monkeypatch):
    import oracle_bot

    stale = _verified_quote("MSFT", 180, quote_timestamp=(datetime.now(timezone.utc) - timedelta(days=7)).isoformat())
    closed = []
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(
        oracle_bot,
        "rows",
        lambda *args, **kwargs: [
            {"symbol": "AAPL", "quantity": 1, "entry_price": 100, "current_price": 999},
            {"symbol": "MSFT", "quantity": 1, "entry_price": 200, "current_price": 999},
        ],
    )
    monkeypatch.setattr(oracle_bot, "ensure_portfolio", lambda market: {"cash": 0, "starting_balance": 1000, "margin_debt": 500, "leverage_limit": 4})
    monkeypatch.setattr(oracle_bot, "_close_position", lambda *args, **kwargs: closed.append(args) or True)
    monkeypatch.setattr(oracle_bot, "execute", lambda *args, **kwargs: None)
    updated = oracle_bot.update_prices("cash", {"AAPL": _verified_quote("AAPL", 80), "MSFT": stale})
    assert updated == 1
    assert closed == []


def test_margin_reduction_defers_when_any_position_quote_is_quarantined(monkeypatch):
    import oracle_bot

    closed = []
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(
        oracle_bot,
        "rows",
        lambda *args, **kwargs: [
            {"symbol": "AAPL", "quantity": 1, "entry_price": 100, "current_price": 999},
            {"symbol": "MSFT", "quantity": 1, "entry_price": 200, "current_price": 999},
        ],
    )
    monkeypatch.setattr(oracle_bot, "ensure_portfolio", lambda market: {"cash": 0, "starting_balance": 1000, "margin_debt": 500, "leverage_limit": 4})
    monkeypatch.setattr(oracle_bot, "_close_position", lambda *args, **kwargs: closed.append(args) or True)
    monkeypatch.setattr(oracle_bot, "execute", lambda *args, **kwargs: None)
    quotes = {
        "AAPL": _verified_quote("AAPL", 80, ohlcv_fingerprint="same-portfolio"),
        "MSFT": _verified_quote("MSFT", 180, ohlcv_fingerprint="same-portfolio"),
    }
    assert oracle_bot.update_prices("cash", quotes) == 0
    assert closed == []


def test_margin_reduction_requires_every_position_fresh_exact_quote(monkeypatch):
    import oracle_bot

    closed = []
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(
        oracle_bot,
        "rows",
        lambda *args, **kwargs: [
            {"symbol": "AAPL", "quantity": 1, "entry_price": 100, "current_price": 999},
            {"symbol": "MSFT", "quantity": 1, "entry_price": 200, "current_price": 999},
        ],
    )
    monkeypatch.setattr(oracle_bot, "ensure_portfolio", lambda market: {"cash": 0, "starting_balance": 1000, "margin_debt": 500, "leverage_limit": 4})

    class Account:
        def __init__(self, call, utilization):
            self.margin_call = call
            self.margin_utilization_pct = utilization

    states = iter([Account(True, 101), Account(False, 1)])
    monkeypatch.setattr(oracle_bot, "build_account", lambda *args, **kwargs: next(states))
    monkeypatch.setattr(oracle_bot, "_close_position", lambda market, position, price, reason, **kwargs: closed.append((position["symbol"], price, reason)) or True)
    monkeypatch.setattr(oracle_bot, "execute", lambda *args, **kwargs: None)
    quotes = {"AAPL": _verified_quote("AAPL", 80), "MSFT": _verified_quote("MSFT", 180)}
    oracle_bot.update_prices("cash", quotes)
    assert closed == [("AAPL", 80.0, oracle_bot.PAPER_MARGIN_REDUCTION_REASON)]


def test_hold_never_executes_buy(monkeypatch):
    import oracle_bot

    quote = _verified_quote("AAPL", 100)
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "_buy", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HOLD bought")))
    actions = oracle_bot.process_signals(
        "cash",
        [{"symbol": "AAPL", "action": "HOLD", "score": 99, "confidence": .99, "price": 100, "market_data_route": quote}],
        {"AAPL": quote},
    )
    assert actions == []


def test_explicit_accumulate_can_proceed_through_entry_gates(monkeypatch):
    import oracle_bot

    quote = _verified_quote("AAPL", 100)
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_QUANT_TRADE_STANDARD", False)
    monkeypatch.setattr(oracle_bot, "_entry_forecast_gate", lambda *args, **kwargs: (True, "fresh"))
    monkeypatch.setattr(oracle_bot, "_penny_stock_gate", lambda *args, **kwargs: (True, "not penny"))
    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "recent_trade", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "_open_position_count", lambda market: 0)
    monkeypatch.setattr(oracle_bot, "_buy", lambda *args, **kwargs: (True, "ok", None))
    actions = oracle_bot.process_signals(
        "cash",
        [{"symbol": "AAPL", "action": "ACCUMULATE", "score": 95, "confidence": .95, "price": 100, "market_data_route": quote}],
        {"AAPL": quote},
    )
    assert actions and actions[0]["action"] == "ACCUMULATE"


def test_displayed_saved_action_matches_executed_action():
    import market_worker

    signal = SimpleNamespace(action="HOLD", score=.95, confidence=.95)
    normalized = market_worker._normalize_starter_action(signal)
    assert normalized.action == "ACCUMULATE"


def test_rotation_rejects_stale_outgoing_quote(monkeypatch):
    import oracle_bot

    stale = _verified_quote("OLD", 7, quote_timestamp=(datetime.now(timezone.utc) - timedelta(days=7)).isoformat())
    monkeypatch.setattr(oracle_bot, "ROTATION_ENABLED", True)
    monkeypatch.setattr(oracle_bot, "ROTATION_MIN_SCORE_GAP", 1)
    monkeypatch.setattr(oracle_bot, "rows", lambda *args, **kwargs: [{"symbol": "OLD", "quantity": 1, "current_price": 999, "entry_price": 10}])
    monkeypatch.setattr(oracle_bot, "_latest_opportunity_score", lambda *args, **kwargs: 10)
    assert oracle_bot._rotate_for_stronger_candidate("cash", "NEW", 90, {"OLD": stale}) is None


def test_rotation_rejects_mismatched_outgoing_quote(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "ROTATION_ENABLED", True)
    monkeypatch.setattr(oracle_bot, "ROTATION_MIN_SCORE_GAP", 1)
    monkeypatch.setattr(oracle_bot, "rows", lambda *args, **kwargs: [{"symbol": "OLD", "quantity": 1, "current_price": 999, "entry_price": 10}])
    monkeypatch.setattr(oracle_bot, "_latest_opportunity_score", lambda *args, **kwargs: 10)
    assert oracle_bot._rotate_for_stronger_candidate("cash", "NEW", 90, {"OLD": _verified_quote("OTHER", 7)}) is None


def test_rotation_uses_verified_outgoing_quote_not_position_price(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "ROTATION_ENABLED", True)
    monkeypatch.setattr(oracle_bot, "ROTATION_MIN_SCORE_GAP", 1)
    monkeypatch.setattr(oracle_bot, "rows", lambda *args, **kwargs: [{"symbol": "OLD", "quantity": 1, "current_price": 999, "entry_price": 10}])
    monkeypatch.setattr(oracle_bot, "_latest_opportunity_score", lambda *args, **kwargs: 10)
    candidate = oracle_bot._rotate_for_stronger_candidate("cash", "NEW", 90, {"OLD": _verified_quote("OLD", 7)})
    assert candidate is not None
    assert candidate["_rotation_action"]["price"] == 7


def test_automated_close_position_rejects_missing_quote_metadata(monkeypatch):
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "connect", lambda: (_ for _ in ()).throw(AssertionError("database mutated")))
    assert oracle_bot._close_position("cash", {"symbol": "AAPL", "quantity": 1, "entry_price": 100}, 90, "stop_loss") is False


def test_margin_call_calculation_uses_verified_current_prices(monkeypatch):
    import oracle_bot

    captured = {}
    quote = _verified_quote("AAPL", 80)
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "PAPER_BROKER_MODE", True)
    monkeypatch.setattr(oracle_bot, "ensure_portfolio", lambda market: {"cash": 0, "starting_balance": 1000, "margin_debt": 500, "leverage_limit": 4})
    monkeypatch.setattr(oracle_bot, "rows", lambda *args, **kwargs: [{"symbol": "AAPL", "quantity": 1, "entry_price": 100, "current_price": 999}])

    class Account:
        margin_call = False
        margin_utilization_pct = 0

    def fake_build_account(market, portfolio, positions):
        captured["current_price"] = positions[0]["current_price"]
        return Account()

    monkeypatch.setattr(oracle_bot, "build_account", fake_build_account)
    monkeypatch.setattr(oracle_bot, "execute", lambda *args, **kwargs: None)
    oracle_bot.update_prices("cash", {"AAPL": quote})
    assert captured["current_price"] == 80


def test_crypto_freshness_uses_crypto_setting(monkeypatch):
    import oracle_bot

    captured = {}
    quote = _verified_quote("BTC-USD", 100, interval="1m")

    def fake_fresh(timestamp, interval, **kwargs):
        captured["max_age"] = kwargs.get("max_intraday_age_seconds")
        return True

    monkeypatch.setattr(oracle_bot, "quote_is_fresh", fake_fresh)
    assert oracle_bot._verified_quote_for("BTC-USD", {"BTC-USD": quote}, "crypto") is not None
    assert captured["max_age"] == oracle_bot.DECISION_CRYPTO_MAX_AGE_MINUTES * 60
