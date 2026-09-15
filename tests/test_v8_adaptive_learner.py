import unittest
import v8_adaptive_learner as v8


def trade(symbol, setup, regime, r):
    return {"symbol":symbol,"setup":setup,"signal_regime":regime,"r":r,
            "paper_epoch":v8.EPOCH,"closed_at":"2026-09-15T00:00:00Z"}


class V8LearnerTests(unittest.TestCase):
    def test_legacy_excluded_and_guardrails_locked(self):
        state={"closed":[trade("A","MOMENTUM_LEADER","RISK_ON",1.0),
                         {"symbol":"OLD","r":5.0,"paper_epoch":"LEGACY_PRE_V7_2"}]}
        rep=v8.build_report(state,{})
        self.assertEqual(rep["champion"]["samples"],1)
        self.assertFalse(rep["guardrails"]["live_execution_authorized"])
        self.assertFalse(rep["guardrails"]["may_modify_v7"])
        self.assertFalse(rep["guardrails"]["may_modify_risk_limits"])
        self.assertFalse(rep["guardrails"]["may_override_sharia_screen"])
        self.assertFalse(rep["promotion"]["automatic_promotion"])

    def test_small_segment_observe_only(self):
        rows=[trade(str(i),"MOMENTUM_LEADER","MIXED",1.0) for i in range(3)]
        rep=v8.build_report({"closed":rows},{})
        seg=rep["learner"]["setup_model"]["MOMENTUM_LEADER"]
        self.assertEqual(seg["recommendation_status"],"OBSERVE_ONLY")
        self.assertEqual(seg["suggested_score_adjustment"],0.0)
        self.assertEqual(rep["status"],"LEARNING")

    def test_actionable_only_after_min_segment_samples(self):
        rows=[trade(str(i),"MOMENTUM_LEADER","RISK_ON",1.0) for i in range(v8.MIN_SEGMENT_SAMPLES)]
        rep=v8.build_report({"closed":rows},{})
        seg=rep["learner"]["setup_model"]["MOMENTUM_LEADER"]
        self.assertEqual(seg["recommendation_status"],"ACTIONABLE_SHADOW")
        self.assertGreater(seg["suggested_score_adjustment"],0)
        self.assertLessEqual(seg["suggested_score_adjustment"],v8.MAX_SCORE_ADJUSTMENT)

    def test_never_auto_promotes_even_with_good_30(self):
        rows=[trade(str(i),"MOMENTUM_LEADER","RISK_ON",1.0) for i in range(30)]
        rep=v8.build_report({"closed":rows},{"market_regime":"RISK_ON","paper_picks":[]})
        self.assertEqual(rep["status"],"READY_FOR_SHADOW_CHALLENGER")
        self.assertFalse(rep["promotion"]["automatic_promotion"])
        self.assertFalse(rep["promotion"]["live_execution_authorized"])

    def test_shadow_ranking_uses_learned_adjustment(self):
        rows=[trade(str(i),"MOMENTUM_LEADER","RISK_ON",1.0) for i in range(6)]
        snap={"market_regime":"RISK_ON","paper_picks":[
            {"symbol":"AAA","setup":"MOMENTUM_LEADER","score":80,"status":"PAPER_ENTRY","sharia_status":"PRECHECK_PASS"},
            {"symbol":"BBB","setup":"BREAKOUT","score":80,"status":"PAPER_ENTRY","sharia_status":"PRECHECK_PASS"}]}
        rep=v8.build_report({"closed":rows},snap)
        self.assertEqual(rep["shadow_rankings"][0]["symbol"],"AAA")
        self.assertGreater(rep["shadow_rankings"][0]["v8_shadow_score"],80)


if __name__=="__main__":
    unittest.main()
