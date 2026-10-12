import unittest
from crypto_winner_cohort_research import analyze_completed_cohorts


def trade(tid, pnl, episode="e1", label="breakout", valid=True):
    return {"trade_id":tid,"net_pnl":pnl,"market_episode_id":episode,"entry_phenotype":label,"entry_provenance_valid":valid}


class CohortResearchTests(unittest.TestCase):
    def test_unique_trades_and_net_economics(self):
        r=analyze_completed_cohorts([trade("1",2),trade("1",2),trade("2",-3)],min_trades=2,min_episodes=2)[0]
        self.assertEqual((r.trades,r.wins,r.net_pnl,r.episodes),(2,1,-1,1))
        self.assertAlmostEqual(r.profit_factor,2/3)
        self.assertFalse(r.qualified_for_research)

    def test_missing_provenance_and_nonfinite_excluded(self):
        r=analyze_completed_cohorts([trade("1",1,valid=False),trade("2",float("nan")),trade("3",1)])[0]
        self.assertEqual(r.trades,1)

    def test_positive_qualification_is_not_promotion(self):
        r=analyze_completed_cohorts([trade("1",2,"a"),trade("2",3,"b")],min_trades=2,min_episodes=2)[0]
        self.assertTrue(r.qualified_for_research)
        self.assertIsNone(r.profit_factor)

    def test_no_data(self):
        self.assertEqual(analyze_completed_cohorts([]),[])


if __name__ == "__main__":
    unittest.main()
