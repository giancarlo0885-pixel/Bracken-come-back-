from datetime import timedelta
from paper_exit_research import reconcile_fifo
from paper_winner_memory import winner_entry_memory
from test_paper_exit_research import buy, sell, START


def history():
    opening = buy('b', 2, 100, .2)
    opening.update(decision_timestamp=START.isoformat(), feature_snapshot={'momentum_5d': .03},
                   _canonical_fill_fee=.2, _canonical_fill_id='bf')
    closing = sell('s', 2, 100, 102, .1, 4)
    closing.update(_canonical_fill_fee=.1, _canonical_fill_id='sf',
                   feature_snapshot={'momentum_5d': -99})
    return [opening, closing]


def memory(rows):
    return winner_entry_memory(reconcile_fifo(rows, require_fill_fee_evidence=True)['closes'])


def test_winner_uses_buy_features_and_full_costs():
    result = memory(history())
    assert result['winners'] == 1
    row = result['records'][0]
    assert row['net_pnl'] == 3.7
    assert row['feature_snapshot'] == {'momentum_5d': .03}


def test_split_exits_count_one_entry_and_wait_for_full_close():
    opening, closing = history()
    first = {**closing, 'quantity': 1, 'gross_pnl': 2, 'net_pnl': 1.9}
    assert memory([opening, first])['exclusions'] == {'open_or_partial_lot': 1}
    second = {**first, 'trade_id': 's2', 'id': 40, 'exit_time': START+timedelta(minutes=30)}
    result = memory([opening, first, second])
    assert result['winners'] == 1
    assert len(result['records'][0]['sell_trade_ids']) == 2


def test_missing_fees_late_features_and_fee_reversed_win_are_not_winners():
    rows = history()
    rows[0].pop('_canonical_fill_fee')
    assert memory(rows)['records'] == []
    rows = history()
    rows[0]['decision_timestamp'] = (START+timedelta(minutes=1)).isoformat()
    assert memory(rows)['exclusions'] == {'missing_or_late_entry_features': 1}
    rows = history()
    rows[1].update(exit_price=100.1, gross_pnl=.2, net_pnl=.1)
    result = memory(rows)
    assert result['winners'] == 0
    assert result['losers'] == 1


def test_formula_recomputes_current_features_and_excludes_future_outcomes(monkeypatch):
    import database
    import oracle_brain
    import oracle_brain_feedback as feedback
    from paper_strategy_economics import strategy_identity
    signal = {'symbol': 'TEST-USD', 'strategy': 'Oracle Council V3', 'regime': 'range-bound', 'volatility_20d': .2,
              'momentum_5d': .03, 'decision_timestamp': START.isoformat()}
    strategy = strategy_identity(signal)
    records = [dict(symbol='TEST-USD', strategy=strategy, regime='range__low_vol',
                    exit_time=(START-timedelta(hours=1)).isoformat(), net_pnl=pnl,
                    feature_snapshot={'momentum_5d': momentum / .30})
               for pnl, momentum in [(1, .03)]*20+[(-1, -.03)]*20]
    records.append({**records[0], 'exit_time': (START+timedelta(hours=1)).isoformat()})
    monkeypatch.setattr(oracle_brain, 'runtime_safety_state', lambda: {'safe_research_boundary': True})
    monkeypatch.setattr(database, 'rows', lambda sql, params: [{'report': {'winner_entry_memory': {'records': records}}}]
                        if 'paper_exit_research_reports' in sql else [])
    monkeypatch.setattr(feedback, '_counterfactual_memory_for_signal', lambda *a, **k: {'adjustment': 0})
    winner = feedback.outcome_memory_for_signal(signal, market='crypto')['green_core']
    loser = feedback.outcome_memory_for_signal({**signal, 'momentum_5d': -.03}, market='crypto')['green_core']
    assert winner['samples'] == 40
    assert winner['adjustment'] > 0
    assert loser['adjustment'] < 0


def test_current_features_match_persisted_fingerprint_units_without_missing_defaults():
    from paper_winner_memory import current_entry_features
    assert current_entry_features({}) == {}
    result = current_entry_features({'momentum_5d': -.003, 'momentum_20d': .03,
                                     'rsi_14': 60, 'atr_pct': .02, 'trend_strength': .01})
    import pytest
    assert result['momentum_5d'] == pytest.approx(-.01)
    assert result['momentum_20d'] == pytest.approx(.05)
    assert result['rsi_14'] == pytest.approx(.2)
    assert result['atr_pct'] == pytest.approx(.1)
    assert result['trend_strength'] == .01
    assert 'volatility_20d' not in result
