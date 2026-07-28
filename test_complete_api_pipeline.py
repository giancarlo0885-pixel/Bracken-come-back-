import pandas as pd
import provider_router


def test_router_falls_back_and_records_attempts(monkeypatch):
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: type("S", (), {"get": lambda self, n: None})())
    idx = pd.date_range("2026-01-01", periods=3, tz="UTC")
    frame = pd.DataFrame({"Close":[1,2,3], "Volume":[4,5,6]}, index=idx)
    routed = provider_router.route_history("TEST", "1y", "1d", lambda *args: frame)
    assert routed.provider == "Yahoo Finance"
    assert routed.metadata()["records"] == 3
    assert routed.attempts[-1].ok is True
