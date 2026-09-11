import math

import paper_optimizer_size_handoff as handoff


def test_risk_limited_spendable_clips_to_remaining_gross_room():
    spendable, gross_room = handoff._risk_limited_spendable(
        cash=35.30,
        buying_power=35.30,
        equity=181.42,
        current_exposure=178.74,
        leverage_limit=1.0,
        margin_utilization_pct=1.0,
    )

    assert math.isclose(gross_room, 2.68, abs_tol=1e-9)
    assert math.isclose(spendable, 2.68, abs_tol=1e-9)


def test_risk_limited_spendable_keeps_full_target_capacity_when_room_exists():
    spendable, gross_room = handoff._risk_limited_spendable(
        cash=35.30,
        buying_power=35.30,
        equity=181.42,
        current_exposure=140.0,
        leverage_limit=1.0,
        margin_utilization_pct=1.0,
    )

    assert gross_room > 35.30
    assert math.isclose(spendable, 35.30, abs_tol=1e-9)


def test_risk_limited_spendable_fails_closed_when_no_gross_room_remains():
    spendable, gross_room = handoff._risk_limited_spendable(
        cash=20.0,
        buying_power=20.0,
        equity=100.0,
        current_exposure=100.0,
        leverage_limit=1.0,
        margin_utilization_pct=1.0,
    )

    assert gross_room == 0.0
    assert spendable == 0.0
