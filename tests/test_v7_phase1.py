import unittest

from v7_observability import max_drawdown_r, _backfill_legacy_sizing
from v7_portfolio_risk import size_trade, risk_dollars


class TestV7Phase1(unittest.TestCase):
    def test_max_drawdown_r(self):
        closed = [{"r": 1.0}, {"r": -0.5}, {"r": -1.0}, {"r": 2.0}]
        self.assertEqual(max_drawdown_r(closed), 1.5)

    def test_position_sizing_respects_half_percent_risk(self):
        x = size_trade(100, 95, 100000, [], "TECH")
        self.assertTrue(x["allowed"])
        self.assertEqual(x["shares"], 100)
        self.assertLessEqual(x["risk_dollars"], 500)

    def test_group_risk_limit_blocks_third_half_percent_trade(self):
        p = {"entry": 100, "stop": 95, "shares": 100, "group": "TECH"}
        second = size_trade(100, 95, 100000, [p], "TECH")
        self.assertTrue(second["allowed"])
        p2 = {"entry": 100, "stop": 95, "shares": second["shares"], "group": "TECH"}
        third = size_trade(100, 95, 100000, [p, p2], "TECH")
        self.assertFalse(third["allowed"])

    def test_legacy_backfill_does_not_exceed_total_risk_cap(self):
        state = {"open": [
            {"symbol": "A", "entry": 100, "stop": 95},
            {"symbol": "B", "entry": 50, "stop": 48},
            {"symbol": "C", "entry": 200, "stop": 190},
        ]}
        out = _backfill_legacy_sizing(state)
        self.assertTrue(all(int(x.get("shares", 0)) > 0 for x in out["open"]))
        self.assertLessEqual(sum(risk_dollars(x) for x in out["open"]), 1500.0)
        self.assertTrue(all(x.get("risk_backfill") for x in out["open"]))


if __name__ == "__main__":
    unittest.main()
