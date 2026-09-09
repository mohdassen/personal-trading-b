import unittest

from v7_signal_quality import entry_quality
from v7_sharia_research import _business_status
from v7_catalyst_rules import classify_catalyst


class TestV7Phase2(unittest.TestCase):
    def test_intraday_entry_quality_requires_vwap(self):
        x = entry_quality({"setup":"SESSION_LEADER","mtf":{"status":"PASS_STRONG"},"above_vwap":False,"relative_volume":2.0,"ret1h_pct":1.0,"daily_trend":True,"extended":False})
        self.assertFalse(x["pass"])
        self.assertIn("BELOW_VWAP", x["reasons"])

    def test_swing_can_pass_without_intraday_vwap(self):
        x = entry_quality({"setup":"SWING_CONTINUATION","mtf":{"status":"PASS_STRONG"},"above_vwap":False,"relative_volume":1.0,"ret5d_pct":3,"ret20d_pct":8,"daily_trend":True,"extended":False})
        self.assertTrue(x["pass"])

    def test_business_screen_rejects_conventional_banking(self):
        status, hits = _business_status({"industry":"Conventional Banking"}, "")
        self.assertEqual(status, "FAIL")
        self.assertTrue(hits)

    def test_business_screen_allows_plain_software(self):
        status, hits = _business_status({"sector":"Technology", "industry":"Software Infrastructure"}, "")
        self.assertEqual(status, "PASS")
        self.assertFalse(hits)

    def test_ai_agent_headline_is_positive_catalyst(self):
        result = classify_catalyst([{"title":"Company unveils AI agent for enterprise customers"}])
        self.assertEqual(result["sentiment"], "POSITIVE")


if __name__ == "__main__":
    unittest.main()
