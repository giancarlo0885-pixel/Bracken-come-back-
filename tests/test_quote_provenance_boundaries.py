from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quote_provenance import provider_verified, reference_allowed_in_mode


def test_explicit_provider_negative_overrides_legacy_eligibility():
    assert not provider_verified({"provider": "Robinhood Crypto", "quote_verified": True,
                                  "provider_quote_verified": False})


def test_paper_and_yahoo_identity_cannot_claim_provider_verification():
    assert not provider_verified({"provider": "Yahoo Finance", "quote_verified": True,
                                  "provider_quote_verified": True})
    assert not provider_verified({"provider": "Polygon", "quote_verified": True,
                                  "provider_quote_verified": True, "paper_reference_verified": True})


def test_provider_and_legacy_provider_verification_remain_supported():
    assert provider_verified({"provider": "Alpaca IEX", "quote_verified": True,
                              "provider_quote_verified": True})
    assert provider_verified({"provider": "Polygon", "quote_verified": True})


def test_paper_reference_cannot_cross_execution_modes_or_disable_flag():
    quote = {"provider": "Yahoo Finance", "quote_verified": True,
             "provider_quote_verified": False, "paper_reference_verified": True}
    assert reference_allowed_in_mode(quote, "paper", True)
    assert not reference_allowed_in_mode(quote, "live", True)
    assert not reference_allowed_in_mode(quote, "paper", False)
    # Missing legacy paper marker does not turn Yahoo into a live quote.
    assert not reference_allowed_in_mode({"provider": "Yahoo Finance"}, "live", True)


def test_explicit_nonbroker_research_marker_cannot_cross_into_live_execution():
    quote = {"provider": "Polygon", "quote_verified": True,
             "provider_quote_verified": False, "paper_reference_verified": False}
    assert not reference_allowed_in_mode(quote, "live", True)
    quote["provider_quote_verified"] = True
    assert reference_allowed_in_mode(quote, "live", True)


def _quote():
    return {"symbol": "MSFT", "requested_symbol": "MSFT", "provider_symbol": "MSFT",
            "provider": "Yahoo Finance", "price": 400.0, "interval": "1m",
            "quote_timestamp": datetime.now(timezone.utc).isoformat(),
            "quote_verified": True, "provider_quote_verified": False,
            "paper_reference_verified": True, "stale": False}


def test_final_execution_boundaries_reject_cached_paper_reference_in_live_mode(monkeypatch):
    import oracle_bot
    quote = _quote()
    monkeypatch.setattr(oracle_bot, "EXECUTION_MODE", "live")
    monkeypatch.setattr(oracle_bot, "PAPER_BROKER_MODE", True)
    assert oracle_bot._verified_quote_for("MSFT", {"MSFT": quote}, "cash") is None
    allowed, reason = oracle_bot._execution_quote_guard("cash", "MSFT", 400.0,
                                                       {"symbol": "MSFT"}, quote_metadata=quote)
    assert not allowed
    assert "paper reference" in reason
    assert "paper reference" in oracle_bot._quote_rejection_reason("MSFT", {"MSFT": quote})


def test_final_execution_boundary_rejects_explicit_negative_provider_provenance(monkeypatch):
    import oracle_bot
    quote = _quote()
    quote.update(provider="Polygon", paper_reference_verified=False)
    monkeypatch.setattr(oracle_bot, "EXECUTION_MODE", "live")
    monkeypatch.setattr(oracle_bot, "PAPER_BROKER_MODE", True)
    assert oracle_bot._verified_quote_for("MSFT", {"MSFT": quote}, "cash") is None
    allowed, _ = oracle_bot._execution_quote_guard("cash", "MSFT", 400.0,
                                                  {"symbol": "MSFT"}, quote_metadata=quote)
    assert not allowed


def test_paper_execution_keeps_fresh_reference_and_rejects_stale_or_mismatched(monkeypatch):
    import oracle_bot
    monkeypatch.setattr(oracle_bot, "EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "PAPER_BROKER_MODE", True)
    monkeypatch.setattr(oracle_bot, "quote_is_fresh", lambda *args, **kwargs: True)
    quote = _quote()
    assert oracle_bot._verified_quote_for("MSFT", {"MSFT": quote}, "cash") is not None
    quote["provider_symbol"] = "GOOG"
    assert oracle_bot._verified_quote_for("MSFT", {"MSFT": quote}, "cash") is None
    quote["provider_symbol"] = "MSFT"
    monkeypatch.setattr(oracle_bot, "quote_is_fresh", lambda *args, **kwargs: False)
    assert oracle_bot._verified_quote_for("MSFT", {"MSFT": quote}, "cash") is None


def test_snapshot_payload_separates_provider_verification_from_paper_eligibility(monkeypatch):
    import market_data
    snapshot = market_data.MarketSnapshot(symbol="MSFT", price=400.0, change_pct=0.0,
        volume=100.0, timestamp=datetime.now(timezone.utc).isoformat(), interval="1m",
        requested_symbol="MSFT", provider_symbol="MSFT", provider="Yahoo Finance",
        quote_verified=True, provider_quote_verified=False, paper_reference_verified=True,
        stale=False)
    payload = snapshot.to_quote_payload()
    assert payload["verified"] is False
    assert payload["execution_quote_eligible"] is True
    assert payload["bid"] is None and payload["ask"] is None
    monkeypatch.setattr(market_data, "EXECUTION_MODE", "live")
    assert not market_data.snapshot_is_verified(snapshot, "MSFT")


def test_stale_provider_history_keeps_identity_without_execution_eligibility(monkeypatch):
    import pandas as pd
    import market_data
    frame = pd.DataFrame({"Close": [400.0]}, index=pd.DatetimeIndex(["2026-01-01T10:00:00Z"]))
    frame.attrs.update(requested_symbol="MSFT", provider_symbol="MSFT", provider_route={
        "provider": "Alpaca IEX", "requested_symbol": "MSFT", "provider_symbol": "MSFT",
        "quote_verified": True, "provider_quote_verified": True})
    monkeypatch.setattr(market_data, "quote_is_fresh", lambda *args, **kwargs: False)
    snapshot = market_data._snapshot_from_history("MSFT", frame, "1m")
    assert snapshot is not None
    assert snapshot.stale is True
    payload = snapshot.to_quote_payload()
    assert payload["verified"] is True
    assert payload["execution_quote_eligible"] is False
    assert not market_data.snapshot_is_verified(snapshot, "MSFT")


def test_cached_paper_history_does_not_become_provider_verified(monkeypatch):
    import pandas as pd
    import market_data
    from provider_router import RoutedHistory
    frame = pd.DataFrame({"Close": [400.0]}, index=pd.DatetimeIndex([datetime.now(timezone.utc)]))
    frame.attrs.update(provider="Yahoo Finance", requested_symbol="MSFT", provider_symbol="MSFT",
                       quote_verified=True, provider_quote_verified=False, paper_reference_verified=True)
    routed = RoutedHistory(frame, "Yahoo Finance", [], datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(market_data, "route_history", lambda *args: routed)
    monkeypatch.setattr(market_data, "EXECUTION_MODE", "live")
    result = market_data.get_history("MSFT", "1d", "1m")
    assert result.attrs["provider_route"]["provider_quote_verified"] is False
    assert result.attrs["provider_route"]["quote_verified"] is False


def test_broker_book_reuse_does_not_override_explicit_unverified_provenance():
    from paper_broker_reference_runtime import _verified_quote_as_broker_book
    quote = _quote()
    quote.update(provider="Robinhood Crypto", requested_symbol="BTC-USD", provider_symbol="BTC-USD",
                 symbol="BTC-USD", bid=100.0, ask=101.0, paper_reference_verified=False)
    assert _verified_quote_as_broker_book("BTC-USD", quote) is None
    quote["provider_quote_verified"] = True
    assert _verified_quote_as_broker_book("BTC-USD", quote) is not None


def test_default_yahoo_download_uses_raising_history_helper(monkeypatch):
    import pandas as pd
    import market_data
    import runtime_provider_reliability
    calls = []
    def retrieve(module, symbol, period, interval):
        calls.append((module, symbol, period, interval))
        return pd.DataFrame()
    monkeypatch.setattr(runtime_provider_reliability, "yahoo_reference_history", retrieve)
    market_data._download_yahoo("MSFT", "1d", "1m")
    assert calls == [(market_data, "MSFT", "1d", "1m")]


@pytest.mark.parametrize("market,symbol", [("cash", "MSFT"), ("crypto", "BTC-USD")])
@pytest.mark.parametrize("changes,reason", [({"stale": True}, "stale"),
    ({"execution_quote_eligible": False}, "ineligible"),
    ({"bid": 402, "ask": 401}, "crossed"), ({"bid": None, "ask": 401}, "invalid"),
    ({"bid": 399, "ask": None}, "invalid"), ({"bid": "NaN", "ask": 401}, "invalid"),
    ({"bid": 399, "ask": "Infinity"}, "invalid"), ({"bid": True, "ask": 401}, "invalid"),
    ({"price": True}, "invalid")])
def test_final_quote_boundaries_honor_explicit_quality_rejection(monkeypatch, market, symbol, changes, reason):
    import oracle_bot
    monkeypatch.setattr(oracle_bot, "EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "PAPER_BROKER_MODE", True)
    quote = {**_quote(), "symbol": symbol, "requested_symbol": symbol,
             "provider_symbol": symbol, **changes}
    assert oracle_bot._verified_quote_for(symbol, {symbol: quote}, market) is None
    assert reason in oracle_bot._quote_rejection_reason(symbol, {symbol: quote}, market)
    # Both handoffs must reject: explicit quote metadata and signal metadata.
    for signal, metadata in (({"symbol": symbol}, quote), (quote, None)):
        allowed, actual_reason = oracle_bot._execution_quote_guard(market, symbol, 400, signal,
                                                                  quote_metadata=metadata)
        assert allowed is False
        assert reason in actual_reason


@pytest.mark.parametrize("book", [{}, {"bid": None, "ask": None}, {"bid": 399.9, "ask": 400.1}])
def test_final_paper_reference_accepts_unknown_or_coherent_book_without_inventing_sides(monkeypatch, book):
    import oracle_bot
    monkeypatch.setattr(oracle_bot, "EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "PAPER_BROKER_MODE", True)
    quote = {**_quote(), "execution_quote_eligible": True, **book}
    accepted = oracle_bot._verified_quote_for("MSFT", {"MSFT": quote}, "cash")
    assert accepted == quote
    assert provider_verified(accepted) is False
    allowed, _ = oracle_bot._execution_quote_guard("cash", "MSFT", 400, quote)
    assert allowed is True
    assert {key: accepted[key] for key in ("bid", "ask") if key in accepted} == book


def test_signal_execution_eligibility_false_survives_metadata_extraction():
    import oracle_bot
    assert oracle_bot._quote_identity_metadata({"execution_quote_eligible": False})["execution_quote_eligible"] is False


def test_final_execution_rejects_boolean_execution_price(monkeypatch):
    import oracle_bot
    monkeypatch.setattr(oracle_bot, "EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "PAPER_BROKER_MODE", True)
    allowed, reason = oracle_bot._execution_quote_guard("cash", "MSFT", True,
        {**_quote(), "price": 1})
    assert allowed is False
    assert "invalid" in reason


def test_final_quote_metadata_cannot_fill_missing_side_from_an_earlier_signal_book(monkeypatch):
    import oracle_bot
    monkeypatch.setattr(oracle_bot, "EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "PAPER_BROKER_MODE", True)
    signal = {**_quote(), "bid": 399, "ask": 401}
    current = {**_quote(), "bid": None, "ask": 401}
    allowed, reason = oracle_bot._execution_quote_guard("cash", "MSFT", 400, signal,
                                                       quote_metadata=current)
    assert allowed is False
    assert "invalid" in reason
    # Explicitly unknown sides remain unknown; old signal sides cannot supply
    # a fabricated book for a legitimate current paper-reference mark.
    current["ask"] = None
    allowed, _ = oracle_bot._execution_quote_guard("cash", "MSFT", 400, signal,
                                                 quote_metadata=current)
    assert allowed is True
