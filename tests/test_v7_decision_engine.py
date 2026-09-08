import unittest

import v7_decision_engine as v7
from v7_catalyst_rules import classify_catalyst


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


if __name__ == "__main__":
    unittest.main()
