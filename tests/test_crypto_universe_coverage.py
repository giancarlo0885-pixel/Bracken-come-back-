import unittest

from crypto_universe_coverage import summarize_crypto_coverage


class CryptoUniverseCoverageTests(unittest.TestCase):
    def test_deduplicates_and_accounts_for_unprocessed_assets(self):
        x = summarize_crypto_coverage(["BTC-USD", "btc-usd", "ETH-USD", "SOL-USD"], ["BTC-USD", "ETH-USD"], ["BTC-USD"], rejected=["ETH-USD"])
        self.assertEqual((x.discovered, x.analyzed, x.rejected, x.deferred, x.missing_from_seed), (3, 1, 1, 1, 1))

    def test_overlaps_do_not_double_count(self):
        x = summarize_crypto_coverage(["BTC-USD", "ETH-USD"], ["BTC-USD"], ["BTC-USD"], rejected=["BTC-USD", "ETH-USD"], deferred=["BTC-USD", "ETH-USD"])
        self.assertEqual((x.analyzed, x.rejected, x.deferred), (1, 1, 0))

    def test_unknown_symbols_are_reported_not_fabricated(self):
        x = summarize_crypto_coverage(["BTC-USD"], ["BTC-USD"], ["SOL-USD"], rejected=["ETH-USD"])
        self.assertEqual((x.analyzed, x.deferred, x.unknown_analyzed, x.unknown_rejected), (0, 1, 1, 1))

    def test_empty_universe(self):
        self.assertEqual(summarize_crypto_coverage([], [], []).as_dict()["discovered"], 0)

    def test_missing_or_nonstring_symbols_are_not_invented(self):
        x = summarize_crypto_coverage([None, 0, "", "BTC-USD"], [None], [None])
        self.assertEqual((x.discovered, x.active_seed, x.analyzed, x.deferred), (1, 0, 0, 1))
        self.assertEqual(x.unknown_analyzed, 0)


if __name__ == "__main__":
    unittest.main()
