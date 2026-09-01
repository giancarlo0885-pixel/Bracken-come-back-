from types import SimpleNamespace

import crypto_v39_risk_bridge as bridge
import runtime_integrity_patch as patch


class _Log:
    def info(self, *args, **kwargs):
        pass


def _worker():
    worker = SimpleNamespace()
    worker.log = _Log()
    worker._v39_position_rows = lambda market: ({"cash": 2000.0, "equity": 2000.0}, [])

    def opportunity(market, signal, prices, ranked_by_symbol, scan_type):
        risk = getattr(signal, "risk_score", None)
        return {"symbol": signal.symbol, "risk_score": risk, "qualified_for_capital": risk is not None}

    worker._v39_signal_opportunity = opportunity
    return worker


def _candidate(**overrides):
    values = dict(
        symbol="BTC-USD",
        action="HOLD",
        score=0.60,
        confidence=0.70,
        volatility_20d=0.55,
        atr_pct=0.018,
        momentum_5d=0.01,
        momentum_20d=0.03,
        trend_strength=0.02,
        volume_ratio=1.2,
        news_sentiment=0.0,
        regime="neutral",
        rebalance_intent=patch.CORE_REBALANCE_CANDIDATE_INTENT,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_canonical_quant_risk_is_attached_for_measured_core_candidate():
    worker = _worker()
    signal = _candidate()

    bridge.install_crypto_v39_risk_bridge(worker)
    result = worker._v39_signal_opportunity("crypto", signal, {}, {}, "fast")

    assert isinstance(signal.risk_score, float)
    assert 0.0 <= signal.risk_score <= 100.0
    assert signal.v39_risk_source == "quant_trade_standard"
    assert result["qualified_for_capital"] is True


def test_missing_measured_risk_inputs_remains_fail_closed():
    worker = _worker()
    signal = _candidate(volatility_20d=None, atr_pct=None)

    bridge.install_crypto_v39_risk_bridge(worker)
    result = worker._v39_signal_opportunity("crypto", signal, {}, {}, "fast")

    assert not hasattr(signal, "risk_score")
    assert result["qualified_for_capital"] is False


def test_non_rebalance_hold_does_not_gain_risk_authorization():
    worker = _worker()
    signal = _candidate(rebalance_intent="")

    bridge.install_crypto_v39_risk_bridge(worker)
    result = worker._v39_signal_opportunity("crypto", signal, {}, {}, "fast")

    assert not hasattr(signal, "risk_score")
    assert result["qualified_for_capital"] is False


def test_existing_risk_evidence_is_preserved():
    worker = _worker()
    signal = _candidate(risk_score=42.0)

    bridge.install_crypto_v39_risk_bridge(worker)
    worker._v39_signal_opportunity("crypto", signal, {}, {}, "fast")

    assert signal.risk_score == 42.0
    assert not hasattr(signal, "v39_risk_source")
