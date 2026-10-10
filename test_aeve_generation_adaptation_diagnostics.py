"""AEVE generation report must distinguish a complete window from adaptation."""
from paper_aeve_generation_controller import BATCH_SIZE, generation_research_report


class FakeConn:
    def __init__(self, window, accepted):
        self.window, self.accepted = window, accepted
        self.calls = 0

    def execute(self, sql, params):
        self.calls += 1
        if self.calls == 1:
            return FakeCursor({})
        return FakeCursor({
            "window_trades": self.window,
            "accepted_trades": self.accepted,
            "outcome_ids": list(range(self.window)),
        })


class FakeCursor:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value


def test_complete_generation_is_not_automatically_adaptation_eligible():
    result = generation_research_report(FakeConn(BATCH_SIZE, 50), 1, "hash")
    assert result["generation_complete"] is True
    assert result["adaptation_eligible"] is False
    assert result["adaptation_block_reason"] == "accepted_sample_requirement_equals_full_generation_window"


def test_incomplete_generation_is_not_complete():
    result = generation_research_report(FakeConn(BATCH_SIZE - 1, 0), 1, "hash")
    assert result["generation_complete"] is False
    assert result["adaptation_eligible"] is False


def test_full_acceptance_is_distinct_from_completion():
    result = generation_research_report(FakeConn(BATCH_SIZE, BATCH_SIZE), 1, "hash")
    assert result["generation_complete"] is True
    assert result["adaptation_eligible"] is True
    assert result["adaptation_block_reason"] is None
