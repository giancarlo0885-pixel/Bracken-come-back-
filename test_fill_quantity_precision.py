"""Read-only quantity comparisons share the atomic writer's existing tolerance."""
from copy import deepcopy
import math

import pytest

from paper_fill_provenance import PROVENANCE_VERSION, attach_exact_fill_lot_evidence
from test_paper_regime_economics_shadow import _linked_close


def verified(row, allocation):
    return attach_exact_fill_lot_evidence([row], [allocation])[0].get(
        '_fill_accounting_provenance') == PROVENANCE_VERSION


def test_one_ulp_allocation_above_fill_quantity_remains_exact_without_rewriting_input():
    allocated = math.nextafter(2.0, math.inf)
    row, allocation = _linked_close(quantity=allocated, buy_quantity=3, exit_fee=.2, buy_fee=.3)
    for key in ('fill', 'paper_order', 'execution'):
        quantity_field = 'filled_quantity' if key == 'paper_order' else 'quantity'
        allocation[key][quantity_field] = 2.0
    allocation['fill']['notional'] = allocation['execution']['value'] = 204.0
    allocation['paper_order']['filled_notional'] = 204.0
    allocation['allocated_fee'] = .2 * allocated / 2.0
    original = deepcopy((row, allocation))
    assert verified(row, allocation)
    assert (row, allocation) == original


def test_one_ulp_close_above_opened_quantity_uses_existing_relative_tolerance():
    row, allocation = _linked_close(quantity=math.nextafter(3.0, math.inf),
                                     buy_quantity=3, exit_fee=.2, buy_fee=.3)
    original = deepcopy((row, allocation))
    assert verified(row, allocation)
    assert (row, allocation) == original


@pytest.mark.parametrize('opened,closed', [(3.0, 3.000001), (1e-24, 2e-24)])
def test_material_or_tiny_lot_overallocation_is_never_rescued(opened, closed):
    row, allocation = _linked_close(quantity=closed, buy_quantity=opened,
                                     exit_fee=0, buy_fee=0)
    assert not verified(row, allocation)


def test_material_fill_overallocation_remains_rejected():
    row, allocation = _linked_close(quantity=2.001, buy_quantity=3, exit_fee=.2, buy_fee=.3)
    allocation['fill']['quantity'] = 2.0
    assert not verified(row, allocation)
