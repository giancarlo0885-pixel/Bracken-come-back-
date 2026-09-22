from __future__ import annotations

import economic_calendar as ec


class _Response:
    def __init__(self, payload, *, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def test_eodhd_requests_full_seven_day_window_and_limit(monkeypatch):
    captured = {}

    def fake_get(url, *, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return _Response([
            {"date": "2026-09-22", "country": "US", "type": "Test Event"}
        ])

    monkeypatch.setattr(ec.requests, "get", fake_get)
    result = ec._fetch_eodhd("secret")

    assert result.available is True
    assert len(result.records) == 1
    assert captured["params"]["limit"] == 1000
    assert captured["params"]["api_token"] == "secret"
    assert captured["params"]["from"] <= captured["params"]["to"]


def test_finnhub_uses_token_query_and_surfaces_api_error(monkeypatch):
    captured = {}

    def fake_get(url, *, params, headers, timeout):
        captured["params"] = params
        return _Response({"error": "Premium access required"})

    monkeypatch.setattr(ec.requests, "get", fake_get)
    result = ec._fetch_finnhub("secret")

    assert captured["params"]["token"] == "secret"
    assert result.available is False
    assert result.records == []
    assert "Premium access required" in result.message


def test_dual_empty_calendar_is_degraded_not_proof_of_no_events(monkeypatch):
    monkeypatch.setattr(ec, "_get_key", lambda *names: "key")
    monkeypatch.setattr(
        ec,
        "_fetch_eodhd",
        lambda _key: ec.ProviderResult(True, "EODHD", [], "empty"),
    )
    monkeypatch.setattr(
        ec,
        "_fetch_finnhub",
        lambda _key: ec.ProviderResult(True, "Finnhub", [], "empty"),
    )
    monkeypatch.setattr(ec, "_record_provider_health", lambda *args, **kwargs: None)

    result = ec.fetch()

    assert result.available is True
    assert result.records == []
    assert result.provider == "EODHD + Finnhub"
    assert "unverified-empty" in result.message
