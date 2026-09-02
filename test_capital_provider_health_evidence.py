from __future__ import annotations

from datetime import datetime, timedelta, timezone

import capital_data_health as health


def test_provider_health_uses_recent_confirmed_market_quote_evidence(monkeypatch) -> None:
    now = datetime.now(timezone.utc)

    def fake_rows(sql, params=()):
        assert "quote_verifications" in sql
        return [
            {
                "provider": "Coinbase Exchange",
                "last_seen": (now - timedelta(minutes=2)).isoformat(),
                "confirmed_samples": 12,
            },
            {
                "provider": "Yahoo Finance",
                "last_seen": (now - timedelta(minutes=3)).isoformat(),
                "confirmed_samples": 12,
            },
        ]

    monkeypatch.setattr(health, "rows", fake_rows)
    result = health.provider_health_summary(maximum_age_minutes=90)

    assert result["ok"] is True
    assert result["status"] == "PASS"
    assert result["source"] == "quote_verifications"
    assert result["configured_providers"] == 2
    assert result["healthy_providers"] == 2


def test_provider_health_fails_closed_without_recent_market_provider_evidence(monkeypatch) -> None:
    monkeypatch.setattr(health, "rows", lambda *args, **kwargs: [])

    result = health.provider_health_summary(maximum_age_minutes=90)

    assert result["ok"] is False
    assert result["status"] == "INSUFFICIENT_PROVIDER_HEALTH"
    assert result["configured_providers"] == 0
    assert result["healthy_providers"] == 0


def test_contextual_news_provider_does_not_satisfy_capital_provider_gate(monkeypatch) -> None:
    """The capital provider query must be sourced from quote verification evidence.

    Gemini/NewsAPI health is intentionally assessed by news_integrity_summary and
    cannot make the hard market-data provider check pass or fail by itself.
    """
    observed = {}

    def fake_rows(sql, params=()):
        observed["sql"] = sql
        return []

    monkeypatch.setattr(health, "rows", fake_rows)
    result = health.provider_health_summary()

    assert "FROM quote_verifications" in observed["sql"]
    assert "FROM provider_health" not in observed["sql"]
    assert result["ok"] is False
