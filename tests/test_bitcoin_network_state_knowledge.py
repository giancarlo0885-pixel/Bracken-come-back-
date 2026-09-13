import math

from bitcoin_network_state_knowledge import (
    assess_bitcoin_network_state,
    attacker_catchup_probability,
    block_subsidy_btc,
    halving_context,
)


def test_attacker_probability_declines_with_confirmations_below_half_hash_power():
    p1 = attacker_catchup_probability(q=0.10, z=1)
    p6 = attacker_catchup_probability(q=0.10, z=6)
    assert p1 is not None and p6 is not None
    assert 0.0 <= p6 < p1 <= 1.0


def test_majority_attacker_probability_is_one():
    assert attacker_catchup_probability(q=0.50, z=6) == 1.0
    assert attacker_catchup_probability(q=0.60, z=6) == 1.0


def test_subsidy_uses_consensus_satoshi_rounding():
    assert block_subsidy_btc(0) == 50.0
    assert block_subsidy_btc(210_000) == 25.0
    assert block_subsidy_btc(840_000) == 3.125


def test_halving_context_is_observed_height_only():
    blocks, progress = halving_context(840_001)
    assert blocks == 209_999
    assert math.isclose(progress, 1 / 210_000)


def test_network_state_is_measurement_only_and_does_not_invent_missing_fields():
    state = assess_bitcoin_network_state({"block_height": 840_001, "hash_rate_change": 0.10})
    assert state.available
    assert state.subsidy_btc == 3.125
    assert state.blocks_to_halving == 209_999
    assert state.fee_pressure is None
    assert state.attacker_catchup_probability is None


def test_observed_metrics_produce_bounded_context_scores():
    state = assess_bitcoin_network_state({
        "block_height": 900_000,
        "confirmations": 6,
        "attacker_hash_share": 0.10,
        "hash_rate_change": 0.08,
        "difficulty_change": 0.04,
        "block_interval_seconds": 610,
        "fee_pressure": 0.7,
        "mempool_pressure": 0.5,
        "miner_revenue_change": -0.12,
    })
    assert -1.0 <= state.network_security_score <= 1.0
    assert -1.0 <= state.network_activity_score <= 1.0
    assert -1.0 <= state.miner_stress_score <= 1.0
    assert state.miner_stress_score > 0
