"""Only original exact CLOSED evidence can remove a terminal numeric residual."""
from copy import deepcopy
from datetime import timedelta
import math

import pytest

from paper_exit_research import attach_fill_fee_evidence, reconcile_fifo
from test_paper_exact_fill_research import exact_allocations
from test_paper_exit_research import START, buy, sell


def original_history(*, terminal_status='CLOSED', final_quantity=None, later_exit=102):
    final_quantity = .3 - .2 if final_quantity is None else final_quantity
    rows = [buy('entry-a', .3, 100, .03),
            buy('entry-b', 1, 101, .01, minute=1),
            sell('close-a1', .2, 100, 110, .002, 2, minute=20),
            sell('close-a2', final_quantity, 100, 111, .001, final_quantity*11, minute=21),
            sell('close-b', 1, 101, later_exit, .01, later_exit-101, minute=30, entry_minute=1)]
    for row in rows:
        entry = 'a' if row['trade_id'] in {'entry-a', 'close-a1', 'close-a2'} else 'b'
        row.update(entry_signal_id='signal:'+entry, entry_forecast_id='forecast:'+entry,
                   entry_decision_id='decision:'+entry, entry_quote_id='quote:'+entry)
    rows[2]['status'] = 'PARTIAL'
    rows[3]['status'] = terminal_status
    rows[4]['status'] = 'CLOSED'
    return rows


def allocations(rows):
    return exact_allocations(rows, {'close-a1':'entry-a', 'close-a2':'entry-a', 'close-b':'entry-b'})


def reconcile(rows, proof=None):
    return reconcile_fifo(attach_fill_fee_evidence(rows, [], allocations(rows) if proof is None else proof),
                          require_fill_fee_evidence=True, as_of=START+timedelta(days=1))


@pytest.mark.parametrize('later_exit,later_net', [(102, .98), (100, -1.02), (101.02, 0)])
def test_exact_original_terminal_close_cannot_poison_later_lot_with_float_residual(later_exit, later_net):
    rows = original_history(later_exit=later_exit)
    before = deepcopy(rows)
    proof = allocations(rows)
    before_proof = deepcopy(proof)
    backed = attach_fill_fee_evidence(rows, [], proof)
    assert all(row.get('_fill_accounting_provenance') == 'exact_fill_lot_v1' for row in backed)
    report = reconcile_fifo(backed, require_fill_fee_evidence=True, as_of=START+timedelta(days=1))
    assert report['qualified_fifo_closes'] == 3
    assert report['net_pnl'] == pytest.approx(3.067+later_net)
    assert report['diagnostics'].get('normalized_terminal_lot_residual') == 1
    later = report['closes'][-1]
    assert later['fifo_complete'] is True
    assert later['entry_fees'] == pytest.approx(.01)
    assert later['exit_fees'] == pytest.approx(.01)
    assert later['net_pnl'] == pytest.approx(later_net, abs=1e-12)
    assert len(later['members']) == 1
    assert later['members'][0]['buy_trade_id'] == 'entry-b'
    assert later['members'][0]['quantity'] == 1
    assert rows == before and proof == before_proof


def test_terminal_annotation_comes_from_original_stored_status_and_cannot_be_supplied():
    rows = original_history(terminal_status='PARTIAL')
    proof = allocations(rows)
    supplied = deepcopy(rows)
    supplied[3]['status'] = 'CLOSED'
    supplied[3]['_canonical_lot_terminal_close'] = True
    backed = attach_fill_fee_evidence(supplied, [], proof)
    assert backed[3].get('_canonical_lot_terminal_close') is not True
    assert reconcile_fifo(backed, require_fill_fee_evidence=True)['closes'][-1]['fifo_complete'] is False
    # A marker from a previous reader must not survive missing original proof.
    clean = attach_fill_fee_evidence(backed, [], [])
    assert all('_canonical_lot_terminal_close' not in row for row in clean)


@pytest.mark.parametrize('status', [None, 'PARTIAL', 'OPEN'])
def test_nonterminal_or_missing_original_status_does_not_clear_residual(status):
    report = reconcile(original_history(terminal_status=status))
    assert report['closes'][-1]['fifo_complete'] is False
    assert report['diagnostics'].get('normalized_terminal_lot_residual', 0) == 0


@pytest.mark.parametrize('missing', ['entry-a', 'close-a1', 'close-a2'])
def test_missing_exact_entry_or_prior_exit_proof_cannot_normalize_inventory(missing):
    rows = original_history()
    proof = [p for p in allocations(rows) if p['trade_id'] != missing]
    report = reconcile(rows, proof)
    assert report['closes'][-1]['fifo_complete'] is False
    assert report['diagnostics'].get('normalized_terminal_lot_residual', 0) == 0


def test_conflicting_original_entry_identity_remains_excluded():
    rows = original_history()
    proof = allocations(rows)
    # Conflicting original provenance is not a terminal rounding condition.
    rows[3]['entry_signal_id'] = 'contradictory-entry'
    report = reconcile(rows, proof)
    assert report['closes'][-1]['fifo_complete'] is False
    assert report['diagnostics'].get('normalized_terminal_lot_residual', 0) == 0


def test_material_unclosed_inventory_is_never_discarded_even_with_closed_status():
    report = reconcile(original_history(final_quantity=.099))
    assert report['closes'][-1]['fifo_complete'] is False
    assert report['diagnostics'].get('normalized_terminal_lot_residual', 0) == 0


def test_small_relative_quantity_with_material_currency_value_is_not_normalized():
    rows = original_history()
    rows[0]['entry_price'] = 1e12
    for index, move in ((2,10), (3,11)):
        rows[index]['entry_price'] = 1e12
        rows[index]['exit_price'] = 1e12+move
    proof = allocations(rows)
    backed = attach_fill_fee_evidence(rows, [], proof)
    assert all(row.get('_fill_accounting_provenance') == 'exact_fill_lot_v1' for row in backed)
    report = reconcile_fifo(backed, require_fill_fee_evidence=True)
    assert report['closes'][-1]['fifo_complete'] is False
    assert report['diagnostics'].get('normalized_terminal_lot_residual', 0) == 0


@pytest.mark.parametrize('price,entry_fee', [(1e12,.1),(100,1e12)])
def test_exact_validator_rejects_material_notional_or_entry_fee_overallocation(price,entry_fee):
    quantity = 1.0000000001
    rows = [buy('entry',1,price,entry_fee),
            sell('exit',quantity,price,price+10,.1,quantity*10)]
    rows[-1]['status'] = 'CLOSED'
    backed = attach_fill_fee_evidence(rows, [], exact_allocations(rows, {'exit':'entry'}))
    # Relative quantity proximity alone cannot certify ~$100 of inventory or
    # entry-fee excess for direct verifier consumers that do not run FIFO.
    assert backed[-1].get('_fill_accounting_provenance') is None
    assert backed[-1].get('_canonical_lot_terminal_close') is not True
    report = reconcile_fifo(backed,require_fill_fee_evidence=True)
    assert report['qualified_fifo_closes'] == 0
    assert report['net_pnl'] is None


def test_exact_validator_rejects_material_allocation_above_original_fill_quantity():
    quantity,price = 1.0000000001,1e12
    rows = [buy('entry',quantity,price,.1),
            sell('exit',quantity,price,price+10,.1,quantity*10)]
    rows[-1]['status'] = 'CLOSED'
    proof = exact_allocations(rows, {'exit':'entry'})
    exit_proof = proof[-1]
    exit_proof['fill'].update(quantity=1,notional=price+10)
    exit_proof['paper_order'].update(filled_quantity=1,filled_notional=price+10)
    exit_proof['execution'].update(quantity=1,value=price+10)
    backed = attach_fill_fee_evidence(rows, [], proof)
    assert backed[-1].get('_fill_accounting_provenance') is None
    assert backed[-1].get('_canonical_lot_terminal_close') is not True


def test_tiny_genuine_inventory_without_original_entry_never_qualifies():
    close = sell('tiny-unmatched', 2e-17, 100, 101, 0, 2e-17)
    close.update(status='CLOSED', _canonical_lot_terminal_close=True)
    report = reconcile_fifo([close], require_fill_fee_evidence=True)
    assert report['qualified_fifo_closes'] == 0
    assert report['net_pnl'] is None
    assert report['diagnostics'].get('normalized_terminal_lot_residual', 0) == 0


@pytest.mark.parametrize('later_exit,later_net', [(102, .98), (100, -1.02)])
def test_exact_terminal_sell_roundoff_cannot_spill_into_the_next_lot(later_exit, later_net):
    rows = original_history(final_quantity=math.nextafter(.1, math.inf), later_exit=later_exit)
    original = deepcopy(rows)
    report = reconcile(rows)
    assert report['qualified_fifo_closes'] == 3
    assert report['diagnostics'].get('normalized_terminal_sell_residual') == 1
    assert report['closes'][1]['members'][0]['normalized_sell_quantity_residual'] == '2E-17'
    assert report['closes'][-1]['net_pnl'] == pytest.approx(later_net)
    assert rows == original


def test_nonterminal_sell_roundoff_is_not_silently_discarded():
    rows = original_history(terminal_status='PARTIAL', final_quantity=math.nextafter(.1, math.inf))
    report = reconcile(rows)
    assert report['diagnostics'].get('normalized_terminal_sell_residual', 0) == 0
    assert report['closes'][1]['fifo_complete'] is False
