from pathlib import Path

from realtime_runtime import cadence_for


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
