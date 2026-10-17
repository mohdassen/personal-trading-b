"""Executable risk guard tests. No broker access or network calls."""
import unittest
from manual_trade_risk_guard import size_trade

class RiskGuardTests(unittest.TestCase):
    def test_normal_sizing(self):
        x=size_trade(100,98)
        self.assertTrue(x["allowed"])
        self.assertEqual(x["shares"],2)
        self.assertLessEqual(x["planned_stop_risk_usd"],5)
    def test_position_cap_scales_with_equity(self):
        x=size_trade(20,19,equity=200)
        self.assertTrue(x['allowed'])
        self.assertLessEqual(x['notional_usd'],50)
    def test_3000_account_caps(self):
        from manual_trade_risk_guard import CAPITAL_USD, MAX_POSITION_USD, MAX_TOTAL_EXPOSURE_USD
        self.assertEqual(CAPITAL_USD,3000)
        self.assertEqual(MAX_POSITION_USD,750)
        self.assertEqual(MAX_TOTAL_EXPOSURE_USD,1500)
    def test_no_cash(self):
        self.assertFalse(size_trade(100,98,open_exposure=1500)["allowed"])
    def test_daily_limit(self):
        self.assertFalse(size_trade(100,98,realized_daily_pnl=-15)["allowed"])
    def test_remaining_daily_risk_budget(self):
        x=size_trade(20,19,realized_daily_pnl=-14)
        self.assertTrue(x['allowed'])
        self.assertEqual(x['shares'],1)
        self.assertLessEqual(x['planned_stop_risk_usd'],1)
    def test_daily_risk_exhausted(self):
        self.assertFalse(size_trade(20,19,realized_daily_pnl=-14.5)['allowed'])
    def test_invalid_stop(self):
        for stop in (100,101,-1):
            with self.subTest(stop=stop),self.assertRaises(ValueError):
                size_trade(100,stop)
    def test_expensive_share(self):
        self.assertFalse(size_trade(800,790)["allowed"])
    def test_nonfinite(self):
        with self.assertRaises(ValueError):
            size_trade(float("nan"),98)
    def test_exposure_cap(self):
        x=size_trade(20,19,open_exposure=1480)
        self.assertTrue(x["allowed"])
        self.assertEqual(x["shares"],1)
    def test_no_guarantee_of_stop_fill(self):
        self.assertIn("not guaranteed",size_trade(100,98)["warning"])

if __name__=="__main__":
    unittest.main()
