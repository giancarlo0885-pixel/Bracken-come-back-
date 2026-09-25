from types import SimpleNamespace

import market_worker
from paper_strategy_economics import strategy_identity


def test_v39_opportunity_preserves_economics_identity_from_signal(monkeypatch):
    monkeypatch.setattr(market_worker, "_execution_quote_eligible", lambda quote: True)
    signal = SimpleNamespace(
        symbol="AAVE-USD",
        action="BUY",
        score=88.0,
        confidence=0.82,
        signal_id="signal-1",
        forecast_id="forecast-1",
        strategy="Oracle Council V3 | momentum=0.42 | rsi=57",
        regime="range__high_vol",
        cohort="fast",
        economic_cohort="fast_range_high_vol",
        model="oracle-forecast",
        model_version="v44",
    )
    prices = {
        "AAVE-USD": {
            "price": 250.0,
            "requested_symbol": "AAVE-USD",
            "provider_symbol": "AAVE-USD",
            "quote_verified": True,
            "tradeable": True,
            "avg_dollar_volume": 1_000_000_000.0,
            "spread_pct": 0.02,
        }
    }
    ranked = {
        "AAVE-USD": {
            "risk_score": 55.0,
            "spread_pct": 0.02,
            "liquidity": 1_000_000_000.0,
            "opportunity_score": 88.0,
        }
    }

    opportunity = market_worker._v39_signal_opportunity(
        "crypto", signal, prices, ranked, "fast"
    )

    assert opportunity["strategy"] == signal.strategy
    assert opportunity["regime"] == "range__high_vol"
    assert opportunity["cohort"] == "fast"
    assert opportunity["economic_cohort"] == "fast_range_high_vol"
    assert opportunity["model"] == "oracle-forecast"
    assert opportunity["model_version"] == "v44"
    assert strategy_identity(opportunity) == "oracle_council_v3"


def test_v39_opportunity_uses_ranked_economics_identity_as_fallback(monkeypatch):
    monkeypatch.setattr(market_worker, "_execution_quote_eligible", lambda quote: True)
    signal = SimpleNamespace(
        symbol="LINK-USD",
        action="BUY",
        score=80.0,
        confidence=0.75,
        signal_id="signal-2",
        forecast_id="forecast-2",
    )
    prices = {
        "LINK-USD": {
            "price": 14.0,
            "requested_symbol": "LINK-USD",
            "provider_symbol": "LINK-USD",
            "quote_verified": True,
            "tradeable": True,
            "avg_dollar_volume": 500_000_000.0,
            "spread_pct": 0.01,
        }
    }
    ranked = {
        "LINK-USD": {
            "strategy_name": "oracle_council_v3",
            "market_regime": "trend_up__high_vol",
            "cohort": "council_trend",
            "model": "ranked-model",
            "model_version": "v3",
            "risk_score": 50.0,
            "spread_pct": 0.01,
            "liquidity": 500_000_000.0,
        }
    }

    opportunity = market_worker._v39_signal_opportunity(
        "crypto", signal, prices, ranked, "deep"
    )

    assert opportunity["strategy_name"] == "oracle_council_v3"
    assert opportunity["regime"] == "trend_up__high_vol"
    assert opportunity["cohort"] == "council_trend"
    assert opportunity["model"] == "ranked-model"
    assert opportunity["model_version"] == "v3"
    assert strategy_identity(opportunity) == "oracle_council_v3"
