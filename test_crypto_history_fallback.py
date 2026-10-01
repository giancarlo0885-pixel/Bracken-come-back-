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
