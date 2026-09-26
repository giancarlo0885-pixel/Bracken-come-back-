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


def test_ai_space_quantum_macro_and_commodity_headlines_are_classified():
    cases = [
        ("Nvidia launches major AI chip platform - Reuters", "AI_TECHNOLOGY", "NVDA"),
        ("Rocket Lab wins satellite launch contract - Reuters", "SPACE_TECHNOLOGY", "RKLB"),
        ("IonQ demonstrates quantum error correction milestone - Reuters", "QUANTUM_TECHNOLOGY", "IONQ"),
        ("Federal Reserve raises rates after inflation report - Reuters", "MACRO_POLICY", ""),
        ("Brent crude oil supply disruption lifts market - Reuters", "COMMODITIES", ""),
    ]
    for title, expected_category, expected_symbol in cases:
        event = scanner.classify_headline(title, query_category=expected_category)
        assert event is not None
        assert event.category == expected_category
        assert event.primary_symbol == expected_symbol


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


def test_event_table_bootstrap_runs_only_once_per_process(monkeypatch):
    statements = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=()):
            statements.append(sql)
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])

    monkeypatch.setattr(scanner, "connect", lambda: Conn())
    monkeypatch.setattr(scanner, "_TABLES_READY", False)

    scanner._ensure_tables()
    scanner._ensure_tables()

    assert sum("CREATE TABLE IF NOT EXISTS event_opportunity_candidates" in sql for sql in statements) == 1
    assert sum("CREATE INDEX IF NOT EXISTS idx_event_opportunity_score" in sql for sql in statements) == 1
    assert sum("CREATE INDEX IF NOT EXISTS idx_event_opportunity_symbol" in sql for sql in statements) == 1
    assert sum("CREATE TABLE IF NOT EXISTS event_opportunity_scanner_status" in sql for sql in statements) == 1


def test_duplicate_event_upsert_skips_noop_row_rewrite(monkeypatch):
    statements = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=()):
            statements.append(sql)
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])

    event = scanner.EventOpportunity(
        event_id="stable-event",
        category="AI_TECHNOLOGY",
        title="Nvidia launches AI platform",
        entity_name="Nvidia",
        primary_symbol="NVDA",
        symbol_candidates=["NVDA"],
        source="Reuters",
        url="https://example.test/event",
        published_at="2026-09-26T00:00:00+00:00",
        detected_at="2026-09-26T01:00:00+00:00",
        score=88.0,
        source_quality=1.0,
        research_only=False,
        query_category="AI_TECHNOLOGY",
        factors=["definitive_event"],
    )
    monkeypatch.setattr(scanner, "connect", lambda: Conn())
    monkeypatch.setattr(scanner, "ingest_monitor_record", lambda *args, **kwargs: None)

    scanner._persist_event(event)

    upsert = next(sql for sql in statements if "INSERT INTO event_opportunity_candidates" in sql)
    assert "WHERE EXCLUDED.category IS DISTINCT FROM event_opportunity_candidates.category" in upsert
    assert "EXCLUDED.score > event_opportunity_candidates.score" in upsert
    assert "detected_at IS DISTINCT FROM" not in upsert
