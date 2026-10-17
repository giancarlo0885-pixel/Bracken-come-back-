"""Exact identity and arithmetic boundaries for the read-only fill validator."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest

from paper_fill_provenance import ANNOTATIONS, PROVENANCE_VERSION, attach_exact_fill_lot_evidence, close, number
from test_paper_regime_economics_shadow import _linked_close


def _verified(row):
    return row.get("_fill_accounting_provenance") == PROVENANCE_VERSION


def test_exact_chain_is_validated_without_a_fabricated_fee_contract():
    row, allocation = _linked_close()
    result = attach_exact_fill_lot_evidence([row], [allocation])[0]
    assert _verified(result)
    assert Decimal(result["_canonical_fill_fee"]) == 1
    assert Decimal(result["_canonical_entry_fill_fee"]) == 2
    assert Decimal(result["_canonical_entry_quantity"]) == 10
    assert result["_canonical_lot_id"] == "original-lot"
    assert not any(key in allocation["fill"] for key in ("fee_contract", "execution_trade_id"))


def test_identical_raw_rows_dedupe_but_ambiguous_entry_identity_fails_closed():
    row, allocation = _linked_close()
    assert _verified(attach_exact_fill_lot_evidence([row], [allocation, deepcopy(allocation)])[0])
    second = deepcopy(allocation)
    second["entry_allocation"]["trade_id"] = "another-buy"
    second["entry_ledger"]["trade_id"] = "another-buy"
    assert not _verified(attach_exact_fill_lot_evidence([row], [allocation, second])[0])


@pytest.mark.parametrize("field", ["quantity", "allocated_fee"])
def test_nan_in_one_candidate_cannot_abort_other_trade_ids(field):
    row, good = _linked_close()
    bad_row, bad = _linked_close(trade_id="bad")
    if field == "quantity":
        bad["fill"][field] = float("nan")
    else:
        bad[field] = float("nan")
    result = attach_exact_fill_lot_evidence([row, bad_row], [bad, good])
    assert _verified(result[0])
    assert not _verified(result[1])


@pytest.mark.parametrize("value", [True, False, float("inf"), Decimal("NaN"), Decimal("Infinity")])
def test_malformed_accounting_values_are_not_numbers(value):
    assert number(value) is None
    row, allocation = _linked_close()
    allocation["entry_fill"]["fee_amount"] = value
    assert not _verified(attach_exact_fill_lot_evidence([row], [allocation])[0])


def test_tiny_lot_identity_uses_relative_quantity_tolerance():
    assert not close(Decimal("1e-24"), Decimal("2e-24"), quantity=True)
    assert close(Decimal("1e-24"), Decimal("1.0000000001e-24"), quantity=True)
    row, allocation = _linked_close(quantity=1e-24, buy_quantity=1e-23, buy_fee=0, exit_fee=0)
    assert _verified(attach_exact_fill_lot_evidence([row], [allocation])[0])
    allocation["allocated_quantity"] = 2e-24
    assert not _verified(attach_exact_fill_lot_evidence([row], [allocation])[0])


@pytest.mark.parametrize("object_name,field,value", [
    ("fill", "canonical_trade_id", True),
    ("execution", "id", True),
    ("fill", "canonical_trade_id", None),
    ("paper_order", "filled_notional", 500),
    ("execution", "realized_pnl", 20),
    ("execution", "gross_realized_pnl", 20),
    ("entry_ledger", "net_pnl", 0),
    ("entry_execution", "realized_pnl", -2),
    ("lot", "broker_mode", "LIVE"),
])
def test_wrong_actual_execution_or_ledger_accounting_is_not_exact(object_name, field, value):
    row, allocation = _linked_close()
    allocation[object_name][field] = value
    assert not _verified(attach_exact_fill_lot_evidence([row], [allocation])[0])


def test_fee_allocation_is_recomputed_from_actual_fill_and_link_quantity():
    row, allocation = _linked_close()
    allocation["allocated_fee"] = 2
    row["fees"] = allocation["ledger"]["fees"] = 2
    row["net_pnl"] = allocation["ledger"]["net_pnl"] = 8
    assert not _verified(attach_exact_fill_lot_evidence([row], [allocation])[0])


def test_source_cannot_change_canonical_accounting_or_timestamps():
    row, allocation = _linked_close()
    row["exit_time"] += timedelta(seconds=1)
    assert not _verified(attach_exact_fill_lot_evidence([row], [allocation])[0])


def test_as_of_rejects_future_observation_and_clears_stale_annotations():
    row, allocation = _linked_close()
    row.update({name: "stale" for name in ANNOTATIONS})
    assert _verified(attach_exact_fill_lot_evidence([row], [allocation], as_of=allocation["fill"]["created_at"])[0])
    result = attach_exact_fill_lot_evidence([row], [allocation], as_of=row["exit_time"])[0]
    assert not any(name in result for name in ANNOTATIONS)
    assert not _verified(attach_exact_fill_lot_evidence([row], [allocation], as_of="not-a-time")[0])


def test_legacy_rows_never_infer_identity_from_same_prices_or_timestamps():
    row, allocation = _linked_close()
    row.update({name: "stale" for name in ANNOTATIONS})
    assert not any(name in attach_exact_fill_lot_evidence([row], [])[0] for name in ANNOTATIONS)
    allocation["entry_allocation"] = None
    assert not _verified(attach_exact_fill_lot_evidence([row], [allocation])[0])


def test_cumulative_closes_cannot_allocate_more_than_the_original_lot():
    first, first_allocation = _linked_close(trade_id="sell:first", fill_id="fill:first", quantity=6)
    second, second_allocation = _linked_close(trade_id="sell:second", fill_id="fill:second", quantity=6)
    for allocation in (first_allocation, second_allocation):
        allocation["lot_closed_quantity"] = 12
    result = attach_exact_fill_lot_evidence([first, second], [first_allocation, second_allocation])
    assert not any(_verified(row) for row in result)


@pytest.mark.parametrize("source", [{"entry": {"edge": True}}, {"entry": {"edge": 2}},
                                     {"entry": {"edge": float("inf")}}])
def test_supplied_features_cannot_replace_persisted_entry_evidence(source):
    row, allocation = _linked_close()
    allocation["ledger"]["feature_snapshot"] = {"entry": {"edge": 1}}
    row["feature_snapshot"] = source
    assert not _verified(attach_exact_fill_lot_evidence([row], [allocation])[0])
