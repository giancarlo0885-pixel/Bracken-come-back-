import unittest
from datetime import datetime, timedelta, timezone
from paper_kelly_evidence_shadow import CompletedPaperOutcome, evaluate_prior_generation_evidence


class KellyEvidenceTests(unittest.TestCase):
    def test_generation_and_episode_isolation(self):
        now = datetime(2026, 10, 9, tzinfo=timezone.utc)
        rows = []
        for i in range(60):
            rows.append(CompletedPaperOutcome(str(i), "g1", f"episode-{i}",
                now-timedelta(days=3), now-timedelta(days=2),
                2.0 if i % 3 else -1.0))
        rows.append(CompletedPaperOutcome("other", "g2", "other",
            now-timedelta(days=3), now-timedelta(days=2), -100))
        rows.append(CompletedPaperOutcome("same", "g1", "candidate",
            now-timedelta(days=3), now-timedelta(days=2), -100))
        result = evaluate_prior_generation_evidence(outcomes=rows,
            generation_id="g1", candidate_entry_time=now,
            candidate_episode_id="candidate", lower_bound_win_probability=.55)
        self.assertTrue(result.eligible)
        self.assertGreater(result.proposed_fraction, 0)

    def test_future_close_rejected(self):
        now = datetime(2026, 10, 9, tzinfo=timezone.utc)
        row = CompletedPaperOutcome("x", "g1", "e1", now-timedelta(hours=1),
                                    now+timedelta(hours=1), 1.0)
        result = evaluate_prior_generation_evidence(outcomes=[row],
            generation_id="g1", candidate_entry_time=now,
            candidate_episode_id="e2", lower_bound_win_probability=.6)
        self.assertEqual(result.reason, "invalid_or_future_outcome")


if __name__ == "__main__":
    unittest.main()
