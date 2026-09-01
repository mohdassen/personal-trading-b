from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import v49_shadow as sh


def row(symbol, score=80, spy_vol20=0.15, group_fields=True):
    return {
        "symbol": symbol,
        "date": "2026-08-28",
        "timestamp": "2026-08-28T00:00:00",
        "base_ok": True,
        "score": score,
        "atr": 2.0,
        "close": 100.0,
        "spy_vol20": spy_vol20,
        "mom12_rank": 0.90,
        "mom6_rank": 0.80,
        "mom12_1": 0.25,
        "mom6_1": 0.12,
        "near52": 0.97,
        "leadership_breadth50": 0.75,
        "leadership_spread20": 0.03,
        "vol20": 0.25,
    }


class V49ShadowTests(unittest.TestCase):
    def test_conservative_sharia_precheck_excludes_financials(self):
        candidates = sh._candidate_rows([row("JPM"), row("AAPL")])
        symbols = {x["symbol"] for x in candidates}
        self.assertNotIn("JPM", symbols)
        self.assertIn("AAPL", symbols)

    def test_portfolio_group_cap_is_preserved(self):
        state = {"pending": [], "open": []}
        candidates = [
            {**row("AAPL"), "group": "MEGA_TECH", "quality": 95, "signal_close": 100, "atr": 2, "estimated_risk_pct": 5, "sharia_status": "PRECHECK_PASS"},
            {**row("MSFT"), "group": "MEGA_TECH", "quality": 94, "signal_close": 100, "atr": 2, "estimated_risk_pct": 5, "sharia_status": "PRECHECK_PASS"},
            {**row("CAT"), "group": "INDUSTRIALS", "quality": 93, "signal_close": 100, "atr": 2, "estimated_risk_pct": 5, "sharia_status": "PRECHECK_PASS"},
        ]
        chosen = sh._select_new(candidates, state)
        self.assertEqual(sum(x["group"] == "MEGA_TECH" for x in chosen), 1)
        self.assertIn("CAT", {x["symbol"] for x in chosen})

    def test_high_volatility_reduces_new_entries_to_one(self):
        state = {"pending": [], "open": []}
        candidates = [
            {**row("AAPL", spy_vol20=0.25), "group": "MEGA_TECH", "quality": 95},
            {**row("CAT", spy_vol20=0.25), "group": "INDUSTRIALS", "quality": 94},
        ]
        chosen = sh._select_new(candidates, state)
        self.assertEqual(len(chosen), 1)

    def test_partial_current_daily_bar_is_dropped(self):
        df = pd.DataFrame(
            {"Open": [10, 11], "High": [11, 12], "Low": [9, 10], "Close": [10.5, 11.5], "Volume": [100, 100]},
            index=pd.to_datetime(["2026-08-31", "2026-09-01"]),
        )
        clock = datetime(2026, 9, 1, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        completed = sh._completed(df, clock)
        self.assertEqual(str(completed.index[-1].date()), "2026-08-31")


if __name__ == "__main__":
    unittest.main()
