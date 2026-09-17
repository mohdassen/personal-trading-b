from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from alpaca_integration import AlpacaBroker, AlpacaCredentials, AlpacaError, PAPER_ORDER_UNLOCK
from swing_full_fidelity_research import Prepared, _bin_key, simulate, swing_raw_score, swing_threshold, swing_weight


NY = ZoneInfo("America/New_York")


class BrokerSafetyTests(unittest.TestCase):
    def test_live_mode_is_hard_locked(self):
        creds = AlpacaCredentials("paper-key", "paper-secret")
        with self.assertRaises(AlpacaError):
            AlpacaBroker(mode="live", credentials=creds)

    def test_missing_credentials_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AlpacaError):
                AlpacaCredentials.from_env()

    def test_paper_order_needs_explicit_unlock(self):
        creds = AlpacaCredentials("paper-key", "paper-secret")
        broker = AlpacaBroker(mode="paper", credentials=creds)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AlpacaError):
                broker.submit_paper_bracket_buy(
                    symbol="AAPL", qty=1, limit_price=100,
                    stop_price=95, target_price=110, client_order_id="test",
                )

    def test_paper_order_payload_after_unlock(self):
        creds = AlpacaCredentials("paper-key", "paper-secret")
        broker = AlpacaBroker(mode="paper", credentials=creds)
        captured = {}

        def fake_post(path, payload):
            captured["path"] = path
            captured["payload"] = payload
            return {"id": "paper-order", "status": "accepted"}

        broker._post = fake_post
        with patch.dict(os.environ, {"ALPACA_PAPER_ORDER_UNLOCK": PAPER_ORDER_UNLOCK}, clear=False):
            out = broker.submit_paper_bracket_buy(
                symbol="aapl", qty=2, limit_price=100,
                stop_price=95, target_price=110, client_order_id="unit-test",
            )
        self.assertEqual(out["id"], "paper-order")
        self.assertEqual(captured["path"], "/v2/orders")
        self.assertEqual(captured["payload"]["order_class"], "bracket")
        self.assertEqual(captured["payload"]["side"], "buy")
        self.assertEqual(captured["payload"]["symbol"], "AAPL")


class SwingFidelityTests(unittest.TestCase):
    def test_session_bin_anchor(self):
        ts = pd.Timestamp("2026-09-17 09:35", tz=NY)
        self.assertEqual(_bin_key(ts, 15), (date(2026, 9, 17), 0))
        self.assertEqual(_bin_key(ts, 60), (date(2026, 9, 17), 0))
        ts2 = pd.Timestamp("2026-09-17 10:30", tz=NY)
        self.assertEqual(_bin_key(ts2, 60), (date(2026, 9, 17), 1))

    def test_swing_score_matches_v7_components(self):
        m = {
            "daily_trend": True,
            "price_above_ema20": True,
            "ret20d": 9.0,
            "ret5d": 4.0,
            "distance_20d_high_pct": -1.0,
            "rsi_d": 60.0,
            "daily_rvol": 1.3,
            "day_change_pct": 1.0,
        }
        self.assertEqual(swing_raw_score(m), 100)
        self.assertEqual(swing_weight("RISK_ON"), 1.02)
        self.assertEqual(swing_weight("MIXED"), 1.05)
        self.assertEqual(swing_weight("RISK_OFF"), 0.94)
        self.assertEqual(swing_threshold("RISK_OFF"), 79)
        self.assertEqual(swing_threshold("MIXED"), 74)

    def test_simulation_enters_next_full_15m_bar_not_signal_bar(self):
        d = date(2026, 9, 17)
        idx = pd.Index([(d, 0), (d, 1), (d, 2), (d, 3)], dtype=object)
        full15 = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0, 103.0],
                "High": [101.0, 103.0, 105.0, 104.0],
                "Low": [99.0, 100.0, 101.0, 102.0],
                "Close": [100.5, 102.0, 104.0, 103.5],
                "Volume": [1000, 1000, 1000, 1000],
                "ATR14": [2.0, 2.0, 2.0, 2.0],
            },
            index=idx,
        )
        prep = Prepared(
            five=pd.DataFrame(), daily=pd.DataFrame(), daily_pos={},
            full15=full15, full15_pos={k: i for i, k in enumerate(idx)},
            hourly=pd.DataFrame(), hourly_pos={},
        )
        candidate = {
            "symbol": "AAPL", "group": "MEGA_TECH", "setup": "SWING_CONTINUATION",
            "regime": "MIXED", "signal_at": "2026-09-17T09:35:00-04:00",
            "signal_date": "2026-09-17", "year": 2026, "score": 80,
            "raw_score": 75, "base_threshold": 74, "signal_price": 100.5,
            "risk": 2.0, "mtf": "PASS", "vwap_extension_atr": 0.2,
            "vix_previous_close": 18.0,
        }
        trade = simulate(candidate, prep, bps=0)
        self.assertIsNotNone(trade)
        # 09:35 signal -> next completed 15m bucket starts at 09:45, not 09:30.
        self.assertTrue(trade["entry_at"].startswith("2026-09-17T09:45:00"))
        self.assertEqual(trade["entry"], 101.0)

    def test_same_bar_stop_is_conservative_before_target(self):
        d = date(2026, 9, 17)
        idx = pd.Index([(d, 0), (d, 1), (d, 2)], dtype=object)
        full15 = pd.DataFrame(
            {
                "Open": [100.0, 100.0, 100.0],
                "High": [101.0, 105.0, 101.0],
                "Low": [99.0, 97.0, 99.0],
                "Close": [100.0, 101.0, 100.0],
                "Volume": [1000, 1000, 1000],
                "ATR14": [2.0, 2.0, 2.0],
            }, index=idx,
        )
        prep = Prepared(pd.DataFrame(), pd.DataFrame(), {}, full15,
                        {k: i for i, k in enumerate(idx)}, pd.DataFrame(), {})
        candidate = {
            "symbol": "AAPL", "group": "MEGA_TECH", "setup": "SWING_CONTINUATION",
            "regime": "MIXED", "signal_at": "2026-09-17T09:35:00-04:00",
            "signal_date": "2026-09-17", "year": 2026, "score": 80,
            "raw_score": 75, "base_threshold": 74, "signal_price": 100,
            "risk": 2.0, "mtf": "PASS", "vwap_extension_atr": 0.0,
            "vix_previous_close": 18.0,
        }
        trade = simulate(candidate, prep, bps=0)
        self.assertEqual(trade["outcome"], "STOP")
        self.assertAlmostEqual(trade["gross_r"], -1.0, places=6)


if __name__ == "__main__":
    unittest.main()
