from types import SimpleNamespace

import oracle_bot
import paper_crypto_churn_guard as churn_guard
import paper_optimizer_size_handoff as handoff
import runtime_integrity_patch as patch


def _paper_env(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("PAPER_UNBOUNDED_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def test_low_priced_crypto_compensation_restores_normal_trade_ceiling():
    fake = SimpleNamespace(
        PENNY_STOCK_MIN_PRICE=0.50,
        PENNY_STOCK_MAX_PRICE=5.00,
        PENNY_STOCK_MAX_TRADE_VALUE_PCT=0.01,
        MAX_TRADE_VALUE_PCT=0.10,
    )

    assert handoff._crypto_penny_compensation(fake, 2.50) == 10.0
    assert handoff._crypto_penny_compensation(fake, 10.00) == 1.0


def test_configured_core_low_priced_crypto_keeps_optimizer_target_and_stock_policy_isolated(monkeypatch):
    _paper_env(monkeypatch)
    monkeypatch.setattr(churn_guard, "_allow_generic_buy", lambda signal: (True, "no_prior_sell"))
    monkeypatch.setattr(handoff, "_INSTALLED", False)
    monkeypatch.setattr(oracle_bot, "PENNY_STOCK_MIN_PRICE", 0.50)
    monkeypatch.setattr(oracle_bot, "PENNY_STOCK_MAX_PRICE", 5.00)
    monkeypatch.setattr(oracle_bot, "PENNY_STOCK_MAX_TRADE_VALUE_PCT", 0.01)
    monkeypatch.setattr(oracle_bot, "MAX_TRADE_VALUE_PCT", 0.10)
    monkeypatch.setattr(oracle_bot, "normalized_confidence", lambda signal: 0.80)
    monkeypatch.setattr(oracle_bot, "normalized_score", lambda signal: 80.0)

    exposure_calls = []

    def original_penny_exposure(*args, **kwargs):
        exposure_calls.append("original")
        return 25.0, 0.50

    observed = {}

    def fake_buy(*args, **kwargs):
        observed["target_trade_value"] = kwargs.get("target_trade_value")
        observed["position_multiplier"] = kwargs["quant_assessment"].position_multiplier
        observed["crypto_context"] = handoff._OPTIMIZER_CRYPTO_PENNY_ISOLATION.get()
        observed["penny_exposure"] = oracle_bot._penny_portfolio_exposure_after([], 100.0, 12.89)
        return True

    monkeypatch.setattr(oracle_bot, "_penny_portfolio_exposure_after", original_penny_exposure)
    monkeypatch.setattr(oracle_bot, "_buy", fake_buy)
    monkeypatch.setattr(
        oracle_bot,
        "adaptive_capital_allocation",
        lambda **kwargs: SimpleNamespace(
            calculated_notional=1.61,
            calculated_quantity=0.0,
            approved=True,
            reason="stock-penny-sized baseline",
        ),
    )

    assert handoff.install_paper_optimizer_size_handoff() is True

    signal = {
        "symbol": "NEAR-USD",
        "action": "BUY",
        "core_rebalance_intent": patch.CORE_REBALANCE_BUY_INTENT,
        "core_rebalance_source": "configured_core_allocation_gap",
        "v39_optimizer_approved_amount": 12.89,
        "v39_optimizer_allocation": {
            "symbol": "NEAR-USD",
            "amount": 12.89,
            "liquidity": {"average_dollar_volume": 100_000_000.0},
        },
    }

    assert oracle_bot._buy("crypto", "NEAR-USD", 2.50, signal) is True
    assert observed["target_trade_value"] == 12.89
    assert observed["position_multiplier"] == 12.5
    assert observed["crypto_context"] is True
    assert observed["penny_exposure"] == (0.0, 0.0)
    assert exposure_calls == []

    # The ContextVar is reset after the crypto call, so stock policy remains the
    # original implementation even in the same Python process.
    assert handoff._OPTIMIZER_CRYPTO_PENNY_ISOLATION.get() is False
    assert oracle_bot._penny_portfolio_exposure_after([], 100.0, 1.0) == (25.0, 0.50)
    assert exposure_calls == ["original"]
