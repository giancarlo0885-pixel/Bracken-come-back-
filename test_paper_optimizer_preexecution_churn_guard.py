from types import SimpleNamespace

import oracle_bot
import paper_crypto_churn_guard as churn_guard
import paper_optimizer_size_handoff as handoff


def _paper_env(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("PAPER_UNBOUNDED_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def _install_with_fake_downstream(monkeypatch):
    downstream_calls = []

    def fake_buy(*args, **kwargs):
        downstream_calls.append((args, kwargs))
        return True

    monkeypatch.setattr(handoff, "_INSTALLED", False)
    monkeypatch.setattr(oracle_bot, "_buy", fake_buy)
    monkeypatch.setattr(
        oracle_bot,
        "adaptive_capital_allocation",
        lambda **kwargs: SimpleNamespace(
            calculated_notional=0.0,
            calculated_quantity=0.0,
            approved=True,
            reason="test",
        ),
    )
    assert handoff.install_paper_optimizer_size_handoff() is True
    return downstream_calls


def test_final_optimizer_buy_blocks_downstream_core_reentry(monkeypatch):
    _paper_env(monkeypatch)
    monkeypatch.setattr(
        churn_guard,
        "_allow_generic_buy",
        lambda signal: (False, "loss_reentry_cooldown:streak=6:12.87/105.00m"),
    )
    downstream_calls = _install_with_fake_downstream(monkeypatch)

    signal = {
        "symbol": "SOL-USD",
        "action": "BUY",
        "core_rebalance_intent": "CORE_REBALANCE_BUY",
        "core_rebalance_source": "configured_core_allocation_gap",
    }
    result = oracle_bot._buy("crypto", "SOL-USD", 150.0, signal)

    assert result is False
    assert downstream_calls == []


def test_final_optimizer_accumulate_blocks_downstream_core_reentry(monkeypatch):
    _paper_env(monkeypatch)
    monkeypatch.setattr(
        churn_guard,
        "_allow_generic_buy",
        lambda signal: (False, "loss_reentry_cooldown:streak=6:31.13/105.00m"),
    )
    downstream_calls = _install_with_fake_downstream(monkeypatch)

    signal = {
        "symbol": "SOL-USD",
        "action": "ACCUMULATE",
        "core_rebalance_intent": "CORE_REBALANCE_BUY",
        "core_rebalance_source": "configured_core_allocation_gap",
    }
    result = oracle_bot._buy("crypto", "SOL-USD", 150.0, signal)

    assert result is False
    assert downstream_calls == []


def test_final_optimizer_buy_allows_nonblocked_paper_buy(monkeypatch):
    _paper_env(monkeypatch)
    monkeypatch.setattr(churn_guard, "_allow_generic_buy", lambda signal: (True, "no_prior_sell"))
    downstream_calls = _install_with_fake_downstream(monkeypatch)

    result = oracle_bot._buy("crypto", "BNB-USD", 700.0, {"symbol": "BNB-USD", "action": "BUY"})

    assert result is True
    assert len(downstream_calls) == 1
