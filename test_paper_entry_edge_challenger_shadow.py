from pathlib import Path

import paper_entry_edge_challenger_shadow as challenger


def test_entry_edge_score_penalizes_adverse_excursion_cost_and_bad_regime():
    score = challenger.entry_edge_score(
        mfe_pct=0.18,
        mae_pct=-0.51,
        round_trip_cost_pct=0.20,
        loss_streak=4,
        bad_regime=True,
    )
    assert score < 0.0


def test_entry_edge_score_can_clear_gate_for_strong_clean_edge():
    score = challenger.entry_edge_score(
        mfe_pct=1.50,
        mae_pct=-0.20,
        round_trip_cost_pct=0.10,
        loss_streak=0,
        bad_regime=False,
    )
    assert score > 0.20


def test_entry_edge_score_caps_loss_streak_penalty():
    six = challenger.entry_edge_score(
        mfe_pct=1.0, mae_pct=-0.1, round_trip_cost_pct=0.05,
        loss_streak=6, bad_regime=False,
    )
    twenty = challenger.entry_edge_score(
        mfe_pct=1.0, mae_pct=-0.1, round_trip_cost_pct=0.05,
        loss_streak=20, bad_regime=False,
    )
    assert six == twenty


def test_active_requires_paper_and_disarmed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    assert challenger.active() is True
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    assert challenger.active() is False


def test_challenger_v2_requires_exact_round_trip_cost_evidence():
    source = Path("paper_entry_edge_challenger_shadow.py").read_text(encoding="utf-8")
    assert 'entry-edge-challenger-v2-round-trip-cost' in source
    assert "m.cost_provenance='exact_lot'" in source
    assert "m.round_trip_net_pnl" in source
    assert "m.round_trip_fees" in source
    assert "l.fees/(l.quantity*l.entry_price)" not in source
    assert "gate=0.20pct" in source
