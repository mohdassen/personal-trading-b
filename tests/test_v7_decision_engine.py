import unittest

import v7_decision_engine as v7
from v7_catalyst_rules import classify_catalyst
from v7_paper_guard import _signal_in_market_window
from v7_quality_rules import confidence_score


class TestV7Logic(unittest.TestCase):
    def test_router_prefers_momentum_in_risk_on(self):
        scores = {
            "SESSION_LEADER": 80,
            "MOMENTUM_LEADER": 81,
            "BREAKOUT": 80,
            "VWAP_PULLBACK": 80,
            "SWING_CONTINUATION": 80,
        }
        routed = v7.route_strategy(scores, "RISK_ON")
        self.assertEqual(routed["setup"], "MOMENTUM_LEADER")

    def test_router_prefers_vwap_in_mixed(self):
        scores = {
            "SESSION_LEADER": 75,
            "MOMENTUM_LEADER": 76,
            "BREAKOUT": 76,
            "VWAP_PULLBACK": 76,
            "SWING_CONTINUATION": 75,
        }
        routed = v7.route_strategy(scores, "MIXED")
        self.assertEqual(routed["setup"], "VWAP_PULLBACK")

    def test_mtf_session_leader_can_pass_without_daily_trend(self):
        result = v7.mtf_confirmation("SESSION_LEADER", True, True, True, False)
        self.assertEqual(result["status"], "PASS_STRONG")

    def test_mtf_swing_requires_hourly_and_daily(self):
        result = v7.mtf_confirmation("SWING_CONTINUATION", True, True, False, False)
        self.assertEqual(result["status"], "FAIL")

    def test_positive_catalyst_is_capped(self):
        items = [
            {"title": "Company beats estimates and raises guidance"},
            {"title": "Company wins major contract"},
        ]
        result = classify_catalyst(items)
        self.assertEqual(result["sentiment"], "POSITIVE")
        self.assertLessEqual(result["score"], 5)

    def test_guidance_cuts_is_strong_negative(self):
        result = classify_catalyst([{"title": "Company faces a growth reset after Guidance Cuts"}])
        self.assertEqual(result["sentiment"], "NEGATIVE")
        self.assertLessEqual(result["score"], -6)

    def test_negative_catalyst_can_block(self):
        items = [
            {"title": "Company cuts guidance after earnings miss"},
            {"title": "Company faces investigation and lawsuit"},
        ]
        result = classify_catalyst(items)
        self.assertEqual(result["sentiment"], "NEGATIVE")
        self.assertLessEqual(result["score"], -6)

    def test_confidence_score_never_saturates_at_100(self):
        self.assertEqual(confidence_score(100, "PASS_STRONG", 5), 99)
        self.assertLess(confidence_score(94, "PASS_STRONG", 0), confidence_score(95, "PASS_STRONG", 0))

    def test_signal_market_window_accepts_regular_session(self):
        # 13:59 UTC = 09:59 New York on 2026-09-09 (EDT).
        self.assertTrue(_signal_in_market_window("2026-09-09T13:59:00+00:00"))

    def test_signal_market_window_rejects_after_close(self):
        # 20:55 UTC = 16:55 New York on 2026-09-08 (EDT).
        self.assertFalse(_signal_in_market_window("2026-09-08T20:55:00+00:00"))


if __name__ == "__main__":
    unittest.main()
