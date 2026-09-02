from __future__ import annotations


def test_broker_anchored_quote_preserves_oracle_reference_and_uses_book_fraction():
    import paper_broker_reference_runtime as runtime

    quote = {
        "provider": "Yahoo Finance",
        "requested_symbol": "BTC-USD",
        "provider_symbol": "BTC-USD",
        "quote_verified": True,
        "price": 100.0,
    }
    reference = {
        "bid": 99.9,
        "ask": 100.1,
        "mid": 100.0,
        "spread_pct_points": 0.2,
        "spread_fraction": 0.002,
        "difference_pct": 0.0,
    }

    anchored = runtime._broker_anchored_quote(quote, oracle_price=100.0, reference=reference)

    assert anchored["provider"] == "Yahoo Finance"
    assert anchored["paper_oracle_reference_price"] == 100.0
    assert anchored["paper_broker_reference_verified"] is True
    assert anchored["paper_broker_mid"] == 100.0
    assert anchored["bid"] == 99.9
    assert anchored["ask"] == 100.1
    assert anchored["spread_pct"] == 0.002
    assert anchored["paper_execution_model_version"] == runtime.PAPER_EXECUTION_MODEL_VERSION


def test_versioned_shadow_readiness_excludes_legacy_execution_model(monkeypatch):
    import database
    import paper_broker_reference_runtime as runtime

    captured = {}

    def fake_row(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return {
            "evaluated": 100,
            "open_count": 2,
            "avg_outcome": 0.1,
            "avg_paper_error": 0.25,
            "p95_paper_error": 0.45,
        }

    monkeypatch.setattr(database, "row", fake_row)
    summary = runtime._versioned_shadow_summary(minimum_samples=100, maximum_paper_error_pct=1.0)

    assert "payload->>'evidence_kind'='passive_paper_execution_model'" in captured["sql"]
    assert "payload->>'paper_execution_model_version'=%s" in captured["sql"]
    assert captured["params"] == (runtime.PAPER_EXECUTION_MODEL_VERSION,)
    assert summary["ok"] is True
    assert summary["status"] == "PASS"
    assert summary["paper_execution_model_version"] == runtime.PAPER_EXECUTION_MODEL_VERSION


def test_validated_broker_reference_rejects_divergent_broker_book(monkeypatch):
    import paper_broker_reference_runtime as runtime

    class FakeClient:
        def best_bid_ask_quotes(self, *symbols):
            return [{"symbol": "BTC-USD", "bid_price": "104.9", "ask_price": "105.1"}]

    monkeypatch.setenv("ROBINHOOD_BROKER_PRICE_TOLERANCE_PCT", "0.75")
    result = runtime._validated_broker_reference("BTC-USD", 100.0, client=FakeClient())

    assert result["ok"] is False
    assert result["reason"] == "BROKER_PRICE_DIVERGENCE"
