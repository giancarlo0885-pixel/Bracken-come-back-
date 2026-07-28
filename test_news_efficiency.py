from __future__ import annotations

import news_intelligence as ni


def _reset_state():
    ni._window_started = 0.0
    ni._window_requests = 0
    ni._cooldown_until = 0.0


def test_non_priority_news_is_deferred(monkeypatch):
    monkeypatch.setattr(ni, "ENABLE_NEWS", True)
    result = ni.get_news_sentiment("Example Corp EX", priority=False)
    assert result.source == "Deferred"
    assert result.headlines == []


def test_newsapi_budget_stops_requests(monkeypatch):
    _reset_state()
    monkeypatch.setattr(ni, "NEWSAPI_MAX_REQUESTS_PER_12H", 2)
    assert ni._budget_allows_request() is True
    assert ni._budget_allows_request() is True
    assert ni._budget_allows_request() is False


def test_rate_limit_activates_cooldown(monkeypatch):
    _reset_state()
    monkeypatch.setattr(ni, "NEWSAPI_RATE_LIMIT_COOLDOWN_SECONDS", 3600)
    ni._activate_cooldown("test")
    state = ni.provider_state()
    assert state["newsapi_cooldown_active"] is True
    assert state["newsapi_cooldown_remaining_seconds"] > 0
