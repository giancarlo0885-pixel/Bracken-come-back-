from __future__ import annotations

import provider_diagnostics as pdx
from api_manager import resolve_api_key


def test_alias_detection(monkeypatch):
    monkeypatch.delenv("NEWS_API_KEY", raising=False)
    monkeypatch.setenv("NEWSAPI_KEY", "example")
    assert resolve_api_key("NEWS_API_KEY") == "example"


def test_missing_provider_is_not_configured(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    result = pdx.diagnose_provider("FRED_API_KEY", force=True)
    assert result.configured is False
    assert result.status == "not_configured"


def test_plan_limit_classification():
    class Response:
        status_code = 403
        text = "Please upgrade your subscription plan"
    result = pdx._classify_http("FINNHUB_API_KEY", Response(), 12.0)
    assert result.status == "plan_limited"
    assert result.configured is True


def test_rate_limit_classification():
    class Response:
        status_code = 429
        text = "Too many requests"
    result = pdx._classify_http("NEWS_API_KEY", Response(), 9.0)
    assert result.status == "rate_limited"
