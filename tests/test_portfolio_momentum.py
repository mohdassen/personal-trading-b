from __future__ import annotations

import unittest

import backtest_portfolio_momentum as pm


def candidate(symbol, group, quality, spy_vol20=0.10, exit_date="2026-02-01"):
    return {
        "symbol": symbol,
        "group": group,
        "signal_date": "2026-01-02",
        "timestamp": "2026-01-02T00:00:00",
        "entry_date": "2026-01-05",
        "exit_date": exit_date,
        "quality": quality,
        "score": int(quality),
        "mom12_rank": 0.9,
        "spy_vol20": spy_vol20,
    }


class PortfolioMomentumTests(unittest.TestCase):
    def test_group_concentration_cap(self):
        rows = [
            candidate("A", "TECH", 95),
            candidate("B", "TECH", 94),
            candidate("C", "FIN", 93),
            candidate("D", "IND", 92),
        ]
        chosen = pm._portfolio_select(rows)
        self.assertEqual(len(chosen), 3)
        self.assertEqual(sum(x["group"] == "TECH" for x in chosen), 1)
        self.assertEqual(chosen[0]["symbol"], "A")

    def test_high_volatility_throttles_new_positions(self):
        rows = [
            candidate("A", "TECH", 95, 0.25),
            candidate("C", "FIN", 93, 0.25),
            candidate("D", "IND", 92, 0.25),
        ]
        chosen = pm._portfolio_select(rows)
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["symbol"], "A")


if __name__ == "__main__":
    unittest.main()
