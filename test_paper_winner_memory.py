from datetime import timedelta
from copy import deepcopy
import pytest
from paper_exit_research import reconcile_fifo
from paper_winner_memory import winner_entry_memory
from test_paper_exit_research import buy, sell, START


def history():
    opening = buy('b', 2, 100, .2)
    opening.update(decision_timestamp=START.isoformat(), feature_snapshot={'momentum_5d': .03},
                   _canonical_fill_fee=.2, _canonical_fill_id='bf',
                   _canonical_lot_id='lot-b', _canonical_entry_trade_id='b',
                   _canonical_entry_fill_id='bf', _fill_accounting_provenance='exact_fill_lot_v1')
    closing = sell('s', 2, 100, 102, .1, 4)
    closing.update(_canonical_fill_fee=.1, _canonical_fill_id='sf',
                   _canonical_lot_id='lot-b', _canonical_entry_trade_id='b',
                   _canonical_entry_fill_id='bf',
                   _fill_accounting_provenance='exact_fill_lot_v1',
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
    second = {**first, 'trade_id': 's2', 'id': 40, '_canonical_fill_id': 'sf2',
              'exit_time': START+timedelta(minutes=30)}
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


def exact_close(*, opened=2, quantity=2, sell_id='s', sell_fill_id='sf',
                buy_id='b', buy_fill_id='bf', lot_id='lot-b'):
    return dict(trade_id=sell_id, market='crypto', symbol='TEST-USD',
                fifo_complete=True, fill_fee_evidence_verified=True,
                exact_fill_lot_linkage_verified=True, exit_fill_id=sell_fill_id,
                exit_time=(START+timedelta(minutes=20)).isoformat(), net_pnl=.1,
                members=[dict(lot_id=lot_id, buy_trade_id=buy_id, buy_fill_id=buy_fill_id,
                              sell_trade_id=sell_id, sell_fill_id=sell_fill_id,
                              opened_quantity=opened, quantity=quantity,
                              entry_time=START.isoformat(), decision_timestamp=START.isoformat(),
                              feature_snapshot={'momentum_5d': .03},
                              entry_strategy='council', entry_regime='range')])


@pytest.mark.parametrize('opened', [1e-7, 1e-13, 1e-20])
def test_tiny_partial_lot_cannot_become_a_fully_closed_winner(opened):
    result = winner_entry_memory([exact_close(opened=opened, quantity=opened/2)])
    assert result['records'] == []
    assert result['winners'] == 0
    assert result['exclusions'] == {'open_or_partial_lot': 1}


@pytest.mark.parametrize('opened', [1e-7, 1e-13, 1e-20])
def test_tiny_exact_partial_closes_count_one_completed_entry(opened):
    parts = [exact_close(opened=opened, quantity=opened/2),
             exact_close(opened=opened, quantity=opened/2, sell_id='s2', sell_fill_id='sf2')]
    result = winner_entry_memory(parts)
    assert len(result['records']) == result['winners'] == 1
    assert result['records'][0]['sell_trade_ids'] == ['s', 's2']
    assert result['records'][0]['buy_fill_id'] == 'bf'
    assert result['records'][0]['lot_id'] == 'lot-b'
    assert result['records'][0]['net_pnl'] == .2


@pytest.mark.parametrize('missing', ['exact_fill_lot_linkage_verified', 'lot_id',
                                    'buy_fill_id', 'sell_fill_id'])
def test_verified_fees_alone_cannot_establish_fill_to_lot_provenance(missing):
    close = exact_close()
    if missing == 'exact_fill_lot_linkage_verified':
        close.pop(missing)
    else:
        close['members'][0].pop(missing)
    result = winner_entry_memory([close])
    assert result['records'] == []
    assert result['exclusions'] == {'unverified_fill_lot_linkage': 1}


@pytest.mark.parametrize('field', ['lot_id', 'buy_fill_id'])
def test_partial_closes_cannot_combine_different_original_entry_identities(field):
    first = exact_close(quantity=1)
    second = exact_close(quantity=1, sell_id='s2', sell_fill_id='sf2')
    second['members'][0][field] = 'unrelated-entry'
    result = winner_entry_memory([first, second])
    assert result['records'] == []
    assert result['exclusions'] == {'unverified_fill_lot_linkage': 1}


def test_one_sell_fill_can_close_distinct_canonical_lots_without_merging_entries():
    first = exact_close(sell_id='s1', sell_fill_id='shared-sell-fill')
    second = exact_close(sell_id='s2', sell_fill_id='shared-sell-fill',
                         buy_id='b2', buy_fill_id='bf2', lot_id='lot-b2')
    result = winner_entry_memory([first, second])
    assert len(result['records']) == result['winners'] == 2
    assert {r['buy_trade_id'] for r in result['records']} == {'b', 'b2'}
    assert result['exclusions'] == {}


def test_direct_mixed_entry_close_does_not_assign_combined_pnl_to_each_lot():
    close = exact_close()
    unrelated = deepcopy(close['members'][0])
    unrelated.update(buy_trade_id='b2', buy_fill_id='bf2', lot_id='lot-b2')
    close['members'].append(unrelated)
    result = winner_entry_memory([close])
    assert result['records'] == []
    assert result['exclusions'] == {'mixed_entry_close': 2}


def test_duplicate_partial_sell_cannot_manufacture_a_fully_closed_winner():
    close = exact_close(opened=2, quantity=1)
    result = winner_entry_memory([close, deepcopy(close)])
    assert result['records'] == []
    assert result['exclusions'] == {'unverified_fill_lot_linkage': 1}
