import unittest

from v7_validation import EPOCH, LEGACY_EPOCH, trade_stats, validation_report, normalize_epochs


class TestV7Phase3(unittest.TestCase):
    def test_clean_positive_sample_can_be_shadow_candidate(self):
        trades = [{"r": 0.5, "paper_epoch": EPOCH, "setup":"A", "signal_regime":"MIXED"} for _ in range(30)]
        report = validation_report({"closed": trades})
        self.assertEqual(report["status"], "SHADOW_CANDIDATE")
        self.assertFalse(report["live_execution_authorized"])

    def test_legacy_trades_are_excluded(self):
        trades = [{"r": 2.0, "paper_epoch": LEGACY_EPOCH} for _ in range(50)]
        report = validation_report({"closed": trades})
        self.assertEqual(report["clean_forward_stats"]["samples"], 0)
        self.assertEqual(report["status"], "COLLECTING_FORWARD_DATA")

    def test_missing_epoch_becomes_legacy(self):
        state = normalize_epochs({"pending":[],"open":[],"closed":[{"r":1}],"rejected":[]})
        self.assertEqual(state["closed"][0]["paper_epoch"], LEGACY_EPOCH)

    def test_trade_stats_profit_factor(self):
        stats = trade_stats([{"r":1.0},{"r":1.0},{"r":-1.0}])
        self.assertEqual(stats["samples"], 3)
        self.assertEqual(stats["profit_factor"], 2.0)


if __name__ == "__main__":
    unittest.main()
