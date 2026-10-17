"""Source grade, snapshot quality and fallback selection stay separate."""
from copy import deepcopy
from datetime import datetime, timezone

import pandas as pd
import pytest

import market_data as data
import oracle_bot
from provider_router import RoutedHistory


def snapshot(provider="Alpaca IEX", **changes):
    values = dict(symbol="MSFT", price=100.0, change_pct=0.0, volume=1.0,
                  timestamp=datetime.now(timezone.utc).isoformat(), interval="5m",
                  provider=provider, requested_symbol="MSFT", provider_symbol="MSFT",
                  quote_verified=True, provider_quote_verified=provider != "Yahoo Finance",
                  paper_reference_verified=provider == "Yahoo Finance", stale=False)
    return data.MarketSnapshot(**{**values, **changes})


@pytest.fixture(autouse=True)
def paper_mode(monkeypatch):
    monkeypatch.setattr(data, "EXECUTION_MODE", "paper")
    monkeypatch.setattr(data, "PAPER_BROKER_MODE", True)
    monkeypatch.setattr(oracle_bot, "EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "PAPER_BROKER_MODE", True)


def history_candidates(monkeypatch, candidates):
    calls = []
    def load(symbol, period, interval):
        calls.append((period, interval))
        return object()
    values = iter(candidates)
    monkeypatch.setattr(data, "get_history", load)
    monkeypatch.setattr(data, "_snapshot_from_history", lambda *args: next(values, None))
    return calls


def test_eligible_yahoo_mark_cannot_short_circuit_later_verified_provider(monkeypatch):
    paper, verified = snapshot("Yahoo Finance"), snapshot()
    calls = history_candidates(monkeypatch, [paper, verified])
    assert data.get_live_snapshot("MSFT") is verified
    assert calls == [("1d", "1m"), ("5d", "5m")]
    assert paper.to_quote_payload()["provider_quote_verified"] is False


def test_valid_paper_reference_remains_available_when_no_provider_verifies(monkeypatch):
    paper = snapshot("Yahoo Finance")
    calls = history_candidates(monkeypatch, [paper])
    assert data.get_live_snapshot("MSFT") is paper
    assert len(calls) == 4
    assert data.snapshot_is_verified(paper, "MSFT")
    assert paper.bid is paper.ask is None
    assert paper.to_quote_payload()["verified"] is False


def test_later_eligible_paper_mark_beats_an_earlier_stale_research_snapshot(monkeypatch):
    research = snapshot("Yahoo Finance", stale=True, quote_verified=False)
    paper = snapshot("Yahoo Finance")
    history_candidates(monkeypatch, [research, paper])
    assert data.get_live_snapshot("MSFT") is paper


@pytest.mark.parametrize("changes", [
    {"stale": True}, {"bid": 101.0, "ask": 100.0},
    {"bid": 100.0, "ask": None}, {"bid": float("nan"), "ask": 101.0},
    {"bid": 100.0, "ask": float("inf")}, {"price": True},
])
def test_snapshot_lookup_and_execution_agree_on_quality_veto(changes):
    quote = snapshot(**changes)
    assert not data.snapshot_is_verified(quote, "MSFT")
    assert oracle_bot._verified_quote_for("MSFT", {"MSFT": quote.to_quote_payload()}, "cash") is None


def test_explicit_unverified_nonreference_is_not_executable_in_paper_mode():
    quote = snapshot("Polygon", provider_quote_verified=False, paper_reference_verified=False)
    assert quote.to_quote_payload()["execution_quote_eligible"] is False
    assert not data.snapshot_is_verified(quote, "MSFT")
    assert oracle_bot._verified_quote_for("MSFT", {"MSFT": quote.to_quote_payload()}, "cash") is None


def test_yahoo_identity_cannot_override_explicit_negative_paper_reference():
    quote = snapshot("Yahoo Finance", paper_reference_verified=False)
    assert quote.to_quote_payload()["execution_quote_eligible"] is False
    assert not data.snapshot_is_verified(quote, "MSFT")
    assert oracle_bot._verified_quote_for("MSFT", {"MSFT": quote}, "cash") is None


@pytest.mark.parametrize("negative", [
    {"stale": True}, {"execution_quote_eligible": False},
    {"bid": float("nan"), "ask": float("inf")},
    {"bid": True, "ask": 101.0},
])
def test_history_snapshot_preserves_explicit_quality_veto(monkeypatch, negative):
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame({"Close": [100.0]}, index=pd.DatetimeIndex([now]))
    frame.attrs.update(requested_symbol="MSFT", provider_symbol="MSFT", provider_route={
        "provider": "Alpaca IEX", "requested_symbol": "MSFT", "provider_symbol": "MSFT",
        "quote_timestamp": now.isoformat(), "quote_verified": True,
        "provider_quote_verified": True, **negative,
    })
    quote = data._snapshot_from_history("MSFT", frame, "5m")
    assert quote is not None
    assert quote.to_quote_payload()["execution_quote_eligible"] is False
    assert not data.snapshot_is_verified(quote, "MSFT")


def test_paper_reference_never_becomes_a_live_quote(monkeypatch):
    quote = snapshot("Yahoo Finance")
    monkeypatch.setattr(data, "EXECUTION_MODE", "live")
    assert not data.snapshot_is_verified(quote, "MSFT")


def test_provider_verified_history_without_book_remains_supported():
    quote = snapshot()
    assert data.snapshot_is_verified(quote, "MSFT")
    assert quote.to_quote_payload()["provider_quote_verified"] is True
    assert quote.bid is quote.ask is None


@pytest.mark.parametrize("latest", [True, False, float("nan"), float("inf"), "invalid"])
def test_invalid_latest_close_cannot_become_a_fresh_numeric_quote(latest):
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame({"Close": pd.Series([100.0, latest], dtype=object).values},
                         index=pd.date_range(end=now, periods=2, freq="5min"))
    frame.attrs.update(requested_symbol="MSFT", provider_symbol="MSFT", provider_route={
        "provider": "Alpaca IEX", "requested_symbol": "MSFT", "provider_symbol": "MSFT",
        "quote_timestamp": now.isoformat(), "quote_verified": True, "provider_quote_verified": True,
    })
    assert data._snapshot_from_history("MSFT", frame, "5m") is None


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("quality", [
    {"stale": True}, {"execution_quote_eligible": False},
    {"bid": 101.0, "ask": 100.0},
    {"bid": float("nan"), "ask": float("inf")},
])
def test_history_routing_metadata_preserves_provider_quality_veto(monkeypatch, nested, quality):
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame({"Close": [100.0]}, index=pd.DatetimeIndex([now]))
    frame.attrs.update(requested_symbol="MSFT", provider_symbol="MSFT", quote_verified=True,
                       provider_quote_verified=True)
    frame.attrs.update({"provider_route": quality} if nested else quality)
    original_attrs = deepcopy(frame.attrs)
    monkeypatch.setattr(data, "route_history", lambda *args: RoutedHistory(
        frame, "Alpaca IEX", [], now.isoformat()))
    quote = data._snapshot_from_history("MSFT", data.get_history("MSFT", "5d", "5m"), "5m")
    assert quote is not None
    assert not data.snapshot_is_verified(quote, "MSFT")
    assert oracle_bot._verified_quote_for("MSFT", {"MSFT": quote}, "cash") is None
    assert frame.attrs == original_attrs
