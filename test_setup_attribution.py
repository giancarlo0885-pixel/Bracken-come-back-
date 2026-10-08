from types import SimpleNamespace

from oracle_council import deliberate
from setup_attribution import attach_setup_attribution, classify_entry_setup


def test_classifies_dip_rebound():
    signal = SimpleNamespace(
        rsi_14=39,
        momentum_5d=0.035,
        momentum_20d=0.10,
        trend_strength=0.05,
        volume_ratio=1.15,
    )
    result = classify_entry_setup(signal)
    assert result.tag == "dip_rebound"
    assert result.scores["dip_rebound"] > result.scores["mean_reversion"]


def test_classifies_momentum_breakout():
    signal = SimpleNamespace(
        rsi_14=63,
        momentum_5d=0.075,
        momentum_20d=0.18,
        trend_strength=0.08,
        volume_ratio=1.9,
    )
    result = classify_entry_setup(signal)
    assert result.tag == "momentum_breakout"


def test_classifies_trend_continuation():
    signal = SimpleNamespace(
        rsi_14=56,
        momentum_5d=0.025,
        momentum_20d=0.12,
        trend_strength=0.095,
        volume_ratio=1.05,
    )
    result = classify_entry_setup(signal)
    assert result.tag == "trend_continuation"


def test_classifies_mean_reversion():
    signal = SimpleNamespace(
        rsi_14=25,
        momentum_5d=-0.07,
        momentum_20d=-0.03,
        trend_strength=-0.02,
        volume_ratio=0.9,
    )
    result = classify_entry_setup(signal)
    assert result.tag == "mean_reversion"


def test_attach_preserves_reason_and_adds_stable_marker():
    signal = SimpleNamespace(
        reason="base engine rationale",
        rsi_14=25,
        momentum_5d=-0.07,
        momentum_20d=-0.03,
        trend_strength=-0.02,
        volume_ratio=0.9,
    )
    first = attach_setup_attribution(signal)
    second = attach_setup_attribution(signal)
    assert signal.setup_tag == first.tag == second.tag
    assert signal.reason.startswith("base engine rationale")
    assert signal.reason.count(f"setup={first.tag}") == 1


def test_council_action_is_independent_of_setup_annotation():
    base = dict(
        score=.72,
        action="BUY",
        rsi_14=42,
        momentum_5d=.03,
        momentum_20d=.11,
        volatility_20d=.25,
        trend_strength=.04,
        volume_ratio=1.4,
        news_sentiment=.2,
        regime="risk-on",
    )
    signal = dict(base)
    result = deliberate(signal, ["positive catalyst"])

    assert result["version"] == "V3"
    assert result["action"] in {"BUY", "HOLD", "SELL"}
    assert result["setup_tag"] == signal["setup_tag"]
    assert "setup=" in signal["reason"]
    assert len(result["votes"]) == 12
