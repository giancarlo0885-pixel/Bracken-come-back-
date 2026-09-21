from __future__ import annotations

from datetime import datetime, timezone

import market_intelligence_bridge as bridge


def test_normalization_is_stable_and_uses_real_event_title():
    record = {
        "event": "Federal Reserve holds rates",
        "published_at": "2026-09-21T12:00:00+00:00",
        "source_url": "https://federalreserve.gov/example",
        "verified": True,
        "importance": "high",
    }
    first = bridge.normalize_monitor_record("macro_policy", "Federal Reserve official", record)
    second = bridge.normalize_monitor_record("macro_policy", "Federal Reserve official", record)

    assert first["event_key"] == second["event_key"]
    assert first["title"] == "Federal Reserve holds rates"
    assert first["verification_status"] == "verified"
    assert first["metadata"]["market_wide"] is True
    assert first["execution_impact"] == "NONE"


def test_source_free_verified_claim_is_downgraded():
    event = bridge.normalize_monitor_record(
        "AI_TECHNOLOGY",
        "Anonymous newsletter",
        {"title": "AI supplier wins mystery order", "verified": True, "confidence": 0.99},
    )

    assert event["verification_status"] == "unverified"
    assert event["confidence"] <= 0.35


def test_provider_records_dedupe_by_identity_without_colliding_across_etfs():
    spy = bridge.normalize_monitor_record(
        "ETF Flow",
        "Finnhub ETF Holdings",
        {"etf": "SPY", "symbol": "AAPL", "name": "Apple", "weight": 7.1},
    )
    spy_updated = bridge.normalize_monitor_record(
        "ETF Flow",
        "Finnhub ETF Holdings",
        {"etf": "SPY", "symbol": "AAPL", "name": "Apple", "weight": 7.2},
    )
    qqq = bridge.normalize_monitor_record(
        "ETF Flow",
        "Finnhub ETF Holdings",
        {"etf": "QQQ", "symbol": "AAPL", "name": "Apple", "weight": 8.9},
    )

    assert spy["event_key"] == spy_updated["event_key"]
    assert spy["event_key"] != qqq["event_key"]
    assert spy["title"] == "SPY holding: AAPL — Apple"


def test_brain_context_is_relevant_bounded_and_never_directional():
    sources = [
        {
            "source_key": "intel:verified",
            "provider": "SEC official",
            "category": "regulatory",
            "symbol": "NVDA",
            "title": "Nvidia filing confirms new supply agreement",
            "source_ref": "https://sec.gov/example",
            "observed_at": "2026-09-21T12:00:00+00:00",
            "confidence": 0.94,
            "freshness_score": 0.98,
            "source_quality": 0.97,
            "metadata": {
                "verification_status": "verified",
                "impact_score": 90,
                "affected_symbols": ["NVDA"],
                "direction": "positive",
            },
        },
        {
            "source_key": "intel:rumor",
            "provider": "social",
            "category": "AI_TECHNOLOGY",
            "symbol": "NVDA",
            "title": "Unverified AI rumor",
            "confidence": 1.0,
            "freshness_score": 1.0,
            "source_quality": 1.0,
            "metadata": {
                "verification_status": "unverified",
                "impact_score": 100,
                "affected_symbols": ["NVDA"],
            },
        },
        {
            "source_key": "intel:unrelated",
            "provider": "Reuters",
            "category": "SPACE_TECHNOLOGY",
            "symbol": "RKLB",
            "title": "Rocket Lab mission",
            "confidence": 0.84,
            "freshness_score": 1.0,
            "source_quality": 0.84,
            "metadata": {
                "verification_status": "reported",
                "impact_score": 80,
                "affected_symbols": ["RKLB"],
                "asset_classes": ["stocks"],
            },
        },
    ]

    context = bridge.brain_context_from_sources(sources, symbol="NVDA", market="cash")

    assert 0 < context["catalyst_score"] <= 92
    assert context["ranking_eligible_sources"] == 1
    assert any(item["verification_status"] == "unverified" for item in context["sources"])
    assert all(item["source_key"] != "intel:unrelated" for item in context["sources"])
    assert context["directional_trade_signal"] == "NONE"
    assert context["ranking_impact"] == "BOUNDED_CATALYST_ONLY"
    assert context["execution_impact"] == "NONE"


def test_structured_brief_keeps_fact_and_inference_separate(monkeypatch):
    saved: list[dict] = []

    def fake_save(category, provider, title, details, symbol=None, event_time=None, **kwargs):
        saved.append(
            {
                "category": category,
                "provider": provider,
                "title": title,
                "details": details,
                "symbol": symbol,
                "event_time": event_time,
                **kwargs,
            }
        )
        return {"event_key": kwargs["event_key"], "execution_impact": "NONE"}

    monkeypatch.setattr(bridge, "save_intelligence_event", fake_save)
    result = bridge.ingest_market_brief(
        {
            "brief_id": "2026-09-21",
            "generated_at": "2026-09-21T15:00:00+00:00",
            "developments": [
                {
                    "category": "AI_TECHNOLOGY",
                    "title": "Confirmed AI capacity agreement",
                    "fact": "The parties disclosed a signed capacity agreement.",
                    "inference": "Networking demand may rise.",
                    "affected_symbols": ["NVDA"],
                    "sources": [
                        {"provider": "Company filing", "url": "https://issuer.test/filing"},
                        {"provider": "Reuters", "url": "https://reuters.test/report"},
                    ],
                }
            ],
        }
    )

    assert result["events_persisted"] == 1
    assert result["execution_impact"] == "NONE"
    assert saved[0]["verification_status"] == "corroborated"
    assert saved[0]["details"]["fact"] == "The parties disclosed a signed capacity agreement."
    assert saved[0]["details"]["inference"] == "Networking demand may rise."
    assert saved[0]["source_url"] == "https://issuer.test/filing"


def test_source_cache_context_accepts_datetime_values():
    context = bridge.brain_context_from_sources(
        [
            {
                "source_key": "macro:1",
                "provider": "BLS official",
                "category": "MACRO_POLICY",
                "title": "CPI release",
                "observed_at": datetime(2026, 9, 21, tzinfo=timezone.utc),
                "confidence": 0.9,
                "freshness_score": 1.0,
                "source_quality": 0.97,
                "metadata": {
                    "verification_status": "verified",
                    "impact_score": 80,
                    "market_wide": True,
                    "asset_classes": ["stocks", "crypto"],
                },
            }
        ],
        symbol="BTC-USD",
        market="crypto",
    )
    assert context["catalyst_score"] > 0
