from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import event_opportunity_scanner as scanner
import opportunity_radar as radar


def test_dangote_style_ipo_is_high_priority_research_event():
    now = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
    event = scanner.classify_headline(
        "Dangote Refinery set to open IPO September 14 in what could become Africa's biggest-ever share sale - Reuters",
        url="https://example.test/dangote",
        published_at="Sun, 13 Sep 2026 15:30:00 GMT",
        query_category="IPO_LISTING",
        now=now,
    )
    assert event is not None
    assert event.category == "IPO_LISTING"
    assert event.entity_name == "Dangote Refinery"
    assert event.primary_symbol == ""
    assert event.research_only is True
    assert event.score >= scanner.EVENT_OPPORTUNITY_PROMOTION_SCORE
    assert "high_authority_source" in event.factors


def test_explicit_us_ticker_can_be_promoted_after_quote_verification(monkeypatch):
    event = scanner.classify_headline(
        "Example Energy (NYSE:EXM) wins major refinery contract - Reuters",
        published_at="Sun, 13 Sep 2026 15:30:00 GMT",
        query_category="CONTRACT_CAPEX",
    )
    assert event is not None
    assert event.primary_symbol == "EXM"
    assert event.research_only is False

    monkeypatch.setattr(
        scanner,
        "scan_event_opportunities",
        lambda: [
            {
                "score": 91.0,
                "primary_symbol": "EXM",
                "entity_name": "Example Energy",
            }
        ],
    )
    monkeypatch.setattr(scanner, "_verified_symbol", lambda symbol: symbol == "EXM")
    assert scanner.active_event_watchlist() == {"EXM": "Example Energy"}


def test_query_membership_alone_cannot_manufacture_hot_event():
    score, category, factors = scanner.score_event(
        "Markets open mixed as investors await the week ahead",
        source="Example Blog",
        query_category="IPO_LISTING",
    )
    assert category == "IPO_LISTING"
    assert score <= 44.0
    assert "query_only_cap" in factors


def test_event_catalyst_strengthens_radar_without_bypassing_confirmation():
    base = SimpleNamespace(
        momentum_5d=0.0,
        momentum_20d=0.0,
        trend_strength=0.0,
        rsi_14=50.0,
        volume_ratio=1.0,
        news_sentiment=0.0,
        macd_hist=0.0,
        atr_pct=0.02,
        bollinger_position=0.5,
        volatility_20d=0.25,
        regime="mixed",
        price=10.0,
    )
    without_event = radar.assess_opportunity_radar(base, market="cash")
    base.external_catalyst_score = 92.0
    with_event = radar.assess_opportunity_radar(base, market="cash")

    assert with_event.catalyst_score == 92.0
    assert with_event.setup_score > without_event.setup_score
    assert with_event.primary_setup == "EVENT DRIVEN"
    assert with_event.approved is False
    assert "event catalyst is strong but price/volume confirmation is still limited" in with_event.warnings
