from pathlib import Path
from datetime import datetime, timedelta, timezone

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
    actions = oracle_bot.process_signals(
        "cash",
        [{"symbol": "NEW", "action": "BUY", "score": 95, "confidence": .95, "price": 20}],
        {"NEW": 20},
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
        {"symbol": "NEW", "score": 90, "confidence": .9, "price": 100},
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
