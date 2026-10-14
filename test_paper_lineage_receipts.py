"""Fail-closed provenance receipt regressions."""
from datetime import datetime, timedelta, timezone

from paper_lineage_receipts import classify_paper_lineage

T = datetime(2026, 10, 14, tzinfo=timezone.utc)


def evidence():
    trade = {"trade_id": "t1", "broker_mode": "PAPER", "account_environment": "PAPER",
             "entry_time": T, "exit_time": T + timedelta(minutes=1)}
    fill = {"trade_id": "t1", "fill_id": "f1", "entry_fill_id": "f0", "exit_fill_id": "f1",
            "cost_provenance": "exact_canonical_fill", "fee_verified": True,
            "recorded_at": T + timedelta(minutes=2)}
    brain = {"trade_id": "t1", "episode_key": "trade:t1", "provenance_status": "exact",
             "cost_provenance": "exact_lot", "committed_at": T + timedelta(minutes=3)}
    decision = {"decision_id": "d2", "evidence_immutable": True,
                "decided_at": T + timedelta(minutes=4), "consumed_episode_keys": ["trade:t1"]}
    return trade, fill, brain, decision


def check(items):
    return classify_paper_lineage(trade=items[0], fill=items[1], brain=items[2], decision=items[3])


def test_exact_later_decision_requires_explicit_immutable_consumption():
    assert check(evidence()) == {"accounting": "EXACT_LINKED", "brain": "COMMITTED_LINKED",
                                  "decision_use": "VERIFIED_CONSUMED"}


def test_missing_fill_and_unverified_fee_are_isolated():
    items = list(evidence())
    items[1] = None
    assert check(items)["accounting"] == "ISOLATED"
    items = list(evidence())
    items[1]["fee_verified"] = False
    assert check(items)["accounting"] == "ISOLATED"


def test_brain_ingestion_alone_never_proves_decision_use():
    items = list(evidence())
    items[3] = None
    assert check(items)["decision_use"] == "UNVERIFIED"


def test_future_brain_episode_cannot_be_used_by_past_decision():
    items = list(evidence())
    items[3]["decided_at"] = T + timedelta(minutes=2)
    assert check(items)["decision_use"] == "UNVERIFIED"


def test_reconstructed_context_and_missing_consumption_fail_closed():
    items = list(evidence())
    items[3]["evidence_immutable"] = False
    assert check(items)["decision_use"] == "UNVERIFIED"
    items = list(evidence())
    items[3]["consumed_episode_keys"] = []
    assert check(items)["decision_use"] == "UNVERIFIED"


def test_naive_timestamps_fail_closed():
    items = list(evidence())
    items[1]["recorded_at"] = datetime(2026, 10, 14)
    assert check(items)["accounting"] == "ISOLATED"
