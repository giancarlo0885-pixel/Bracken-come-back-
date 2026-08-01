from pathlib import Path


def test_worker_has_two_speed_live_runtime():
    source = Path("market_worker.py").read_text()
    assert "def live_position_pulse" in source
    assert "deep_executor.submit(scan_market" in source
    assert "cadence.pulse_seconds" in source
    assert "last_pulse" in source


def test_dashboard_auto_refresh_and_native_cards():
    source = Path("app.py").read_text()
    assert "st_autorefresh" in source
    assert "with st.container(border=True):" in source
    assert "metric_html" not in source
    assert "LIVE PAPER TRADING" in source
    assert "use_container_width" not in source
    assert 'width="stretch"' in source


def test_realtime_variables_are_documented():
    variables = Path("railway_variables.example").read_text()
    for name in (
        "STOCK_PULSE_SECONDS",
        "CRYPTO_PULSE_SECONDS",
        "STOCK_DEEP_SCAN_SECONDS",
        "CRYPTO_DEEP_SCAN_SECONDS",
        "UI_REFRESH_SECONDS",
    ):
        assert f"{name}=" in variables
