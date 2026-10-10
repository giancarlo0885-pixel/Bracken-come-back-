import unittest
from paper_kelly_sizing_shadow import propose_kelly_size


class KellySizingTests(unittest.TestCase):
    def base(self, **overrides):
        params = dict(win_probability=.6, lower_bound_win_probability=.55,
                      average_net_win_pct=2.0, average_net_loss_pct=-1.0,
                      independent_episodes=30, completed_trades=100)
        params.update(overrides)
        return propose_kelly_size(**params)

    def test_fractional_conservative_kelly(self):
        r = self.base()
        self.assertTrue(r.eligible)
        self.assertAlmostEqual(r.full_fraction, .325)
        self.assertAlmostEqual(r.proposed_fraction, .08125)

    def test_no_probability_bound_abstains(self):
        self.assertEqual(self.base(lower_bound_win_probability=None).proposed_fraction, 0)

    def test_negative_edge_abstains(self):
        self.assertEqual(self.base(lower_bound_win_probability=.3).reason, "nonpositive_conservative_edge")

    def test_no_episode_independence_abstains(self):
        self.assertFalse(self.base(independent_episodes=2).eligible)

    def test_invalid_evidence_counts_abstain(self):
        for kwargs in (
            {"independent_episodes": -1},
            {"completed_trades": 10, "independent_episodes": 30},
            {"min_episodes": 0},
            {"completed_trades": 100.5},
            {"independent_episodes": True},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.base(**kwargs).reason, "invalid_sample_counts")

    def test_invalid_payoff_abstains(self):
        self.assertFalse(self.base(average_net_loss_pct=0).eligible)

    def test_cap(self):
        self.assertLessEqual(self.base(max_allocation=.02).proposed_fraction, .02)

    def test_nonfinite_abstains(self):
        self.assertFalse(self.base(win_probability=float("nan")).eligible)


if __name__ == "__main__":
    unittest.main()
