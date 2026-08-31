from __future__ import annotations

import pytest

from bitcoin_settlement_integrity import (
    SettlementState,
    assess_settlement,
    confirmation_depth,
    credit_allowed,
    normalize_txid,
)


TXID = "ab" * 32
CONFLICT = "cd" * 32


def test_txid_is_canonicalized_and_strictly_validated():
    assert normalize_txid(TXID.upper()) == TXID
    with pytest.raises(ValueError):
        normalize_txid("abc")
    with pytest.raises(ValueError):
        normalize_txid("zz" * 32)


def test_confirmation_depth_counts_inclusion_block():
    assert confirmation_depth(tip_height=100, block_height=100) == 1
    assert confirmation_depth(tip_height=105, block_height=100) == 6
    assert confirmation_depth(tip_height=100, block_height=None) == 0
    with pytest.raises(ValueError):
        confirmation_depth(tip_height=99, block_height=100)


def test_zero_confirmation_transaction_is_never_final():
    assessment = assess_settlement(
        txid=TXID,
        tip_height=100,
        block_height=None,
        in_best_chain=False,
        seen_in_mempool=True,
        required_confirmations=6,
    )
    assert assessment.state is SettlementState.MEMPOOL
    assert credit_allowed(assessment) is False


def test_replaceable_and_conflicting_transactions_fail_closed():
    replaceable = assess_settlement(
        txid=TXID,
        tip_height=100,
        block_height=None,
        in_best_chain=False,
        seen_in_mempool=True,
        replaceable=True,
    )
    conflicted = assess_settlement(
        txid=TXID,
        tip_height=100,
        block_height=None,
        in_best_chain=False,
        conflicting_txids=[CONFLICT],
    )
    assert replaceable.state is SettlementState.REPLACEABLE
    assert conflicted.state is SettlementState.CONFLICTED
    assert not credit_allowed(replaceable)
    assert not credit_allowed(conflicted)


def test_reorged_block_is_not_counted_as_confirmation():
    assessment = assess_settlement(
        txid=TXID,
        tip_height=105,
        block_height=100,
        in_best_chain=False,
        required_confirmations=6,
    )
    assert assessment.state is SettlementState.REORG_RISK
    assert assessment.confirmations == 0
    assert not credit_allowed(assessment)


def test_confirmation_policy_controls_finality():
    confirming = assess_settlement(
        txid=TXID,
        tip_height=104,
        block_height=100,
        in_best_chain=True,
        required_confirmations=6,
    )
    final = assess_settlement(
        txid=TXID,
        tip_height=105,
        block_height=100,
        in_best_chain=True,
        required_confirmations=6,
    )
    assert confirming.state is SettlementState.CONFIRMING
    assert confirming.confirmations == 5
    assert not credit_allowed(confirming)
    assert final.state is SettlementState.FINAL
    assert final.confirmations == 6
    assert credit_allowed(final)


def test_invalid_observations_fail_closed_without_throwing():
    bad_txid = assess_settlement(
        txid="not-a-txid",
        tip_height=100,
        block_height=None,
        in_best_chain=False,
    )
    bad_height = assess_settlement(
        txid=TXID,
        tip_height=99,
        block_height=100,
        in_best_chain=True,
    )
    assert bad_txid.state is SettlementState.INVALID
    assert bad_height.state is SettlementState.INVALID
    assert not credit_allowed(bad_txid)
    assert not credit_allowed(bad_height)
