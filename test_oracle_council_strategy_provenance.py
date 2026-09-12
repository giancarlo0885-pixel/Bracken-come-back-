from types import SimpleNamespace

import market_worker


def test_oracle_council_v3_attaches_stable_strategy_identity():
    signal = SimpleNamespace(strategy="", reason="momentum context")

    result = market_worker._apply_oracle_council_identity(
        signal,
        {"version": "V3", "explanation": "Oracle Council V3 consensus BUY"},
    )

    assert result is signal
    assert signal.strategy == "oracle_council_v3"


def test_non_v3_council_does_not_overwrite_existing_strategy():
    signal = SimpleNamespace(strategy="momentum_v2", reason="context")

    result = market_worker._apply_oracle_council_identity(
        signal,
        {"version": "V2", "explanation": "legacy council"},
    )

    assert result is signal
    assert signal.strategy == "momentum_v2"
