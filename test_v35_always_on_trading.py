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
