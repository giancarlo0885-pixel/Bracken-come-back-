import pandas as pd
import provider_router as router


class _Response:
    status_code = 200
    def raise_for_status(self):
        return None
    def json(self):
        return [[1700000000, 99, 102, 100, 101, 1234], [1700000300, 100, 103, 101, 102, 1500]]


def test_coinbase_public_history_preserves_exact_identity(monkeypatch):
    monkeypatch.setattr(router.requests, "get", lambda *args, **kwargs: _Response())
    frame = router._coinbase_public_history("TEST-USD", "5d", "5m")
    assert not frame.empty
    assert frame.attrs["provider"] == "Coinbase Exchange"
    assert frame.attrs["requested_symbol"] == "TEST-USD"
    assert frame.attrs["provider_symbol"] == "TEST-USD"
    assert frame.attrs["source_mode"] == "public_crypto_history_fallback"
    assert frame.attrs["quote_verified"] is False


def test_coinbase_history_rejects_unsupported_interval(monkeypatch):
    called = False
    def _get(*args, **kwargs):
        nonlocal called
        called = True
        return _Response()
    monkeypatch.setattr(router.requests, "get", _get)
    assert router._coinbase_public_history("TEST-USD", "1y", "1wk").empty
    assert called is False


def test_crypto_route_uses_coinbase_when_yahoo_is_empty(monkeypatch):
    monkeypatch.setattr(router, "get_api_settings", lambda: {})
    monkeypatch.setattr(router, "symbol_is_unavailable", lambda *args, **kwargs: False)
    monkeypatch.setattr(router, "_coinbase_public_history", lambda *args, **kwargs: router._stamp_frame(
        pd.DataFrame({"Close": [100.0, 101.0]}, index=pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z"])),
        "Coinbase Exchange", "TEST-USD", "TEST-USD", "5d", "5m", True, True, False, "TEST-USD"
    ))
    yahoo_called = False
    def yahoo(*args):
        nonlocal yahoo_called
        yahoo_called = True
        return pd.DataFrame()
    result = router.route_history("TEST-USD", "5d", "5m", yahoo)
    assert result.provider == "Coinbase Exchange"
    assert yahoo_called is True
    assert result.frame.attrs["quote_verified"] is False
