from oracle_brain_feedback import summarize_outcome_memory
from oracle_brain_learning import _normalized_strategy_identity


def _rows(count: int, *, return_pct: float, net_pnl: float, strategy: str = "Oracle Council V3 dynamic rationale"):
    return [
        {
            "symbol": "AAVE-USD",
            "strategy": strategy,
            "regime": "risk_on",
            "return_pct": return_pct,
            "net_pnl": net_pnl,
            "confidence": 0.95,
            "freshness_score": 0.95,
        }
        for _ in range(count)
    ]


def test_brain_strategy_identity_matches_economics_identity():
    assert _normalized_strategy_identity("Oracle Council V3 | momentum=0.31 | rsi=54") == "oracle_council_v3"
    assert _normalized_strategy_identity("Always-on 5m market pulse. Momentum is changing.") == "always_on_5m_market_pulse"


def test_mature_positive_exact_outcomes_support_ranking():
    result = summarize_outcome_memory(
        _rows(30, return_pct=0.40, net_pnl=1.0),
        strategy="Oracle Council V3 | momentum=0.99",
        regime="risk_on",
        symbol="AAVE-USD",
        min_samples=30,
    )
    assert result["mature"] is True
    assert result["polarity"] == "positive"
    assert 0 < result["ranking_adjustment"] <= 3.0
    assert result["execution_impact"] == "BOUNDED_RANKING_ONLY"


def test_mature_negative_exact_outcomes_penalize_ranking():
    result = summarize_outcome_memory(
        _rows(30, return_pct=-0.40, net_pnl=-1.0),
        strategy="Oracle Council V3 | momentum=0.99",
        regime="risk_on",
        symbol="AAVE-USD",
        min_samples=30,
    )
    assert result["mature"] is True
    assert result["polarity"] == "negative"
    assert -4.0 <= result["ranking_adjustment"] < 0


def test_immature_outcomes_have_zero_ranking_influence():
    result = summarize_outcome_memory(
        _rows(12, return_pct=2.0, net_pnl=5.0),
        strategy="Oracle Council V3",
        regime="risk_on",
        symbol="AAVE-USD",
        min_samples=30,
    )
    assert result["mature"] is False
    assert result["polarity"] == "insufficient"
    assert result["ranking_adjustment"] == 0.0
    assert result["execution_impact"] == "NONE"
