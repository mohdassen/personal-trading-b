"""V7 confidence scoring guard.

Keeps strategy thresholds intact while preventing multiple candidates from
collapsing to 100/100 after MTF/catalyst bonuses. Research/PAPER only.
"""
from __future__ import annotations


def confidence_score(routed_score, mtf_status, catalyst_score):
    routed = float(routed_score or 0)
    catalyst = float(catalyst_score or 0)
    mtf_bonus = 4 if mtf_status == "PASS_STRONG" else 1 if mtf_status == "PASS" else -10
    # Preserve the router as the dominant signal. MTF/catalyst may confirm or
    # penalize it, but 99 is a deliberate ceiling so ranking keeps resolution.
    return int(max(0, min(99, round(routed + mtf_bonus + catalyst))))


def install(engine):
    original = engine.enrich_candidate

    def enrich_with_resolved_confidence(row, regime_label):
        result = original(row, regime_label)
        score = confidence_score(
            result.get("router", {}).get("routed_score", 0),
            result.get("mtf", {}).get("status"),
            result.get("catalyst", {}).get("score", 0),
        )
        threshold = int(result.get("threshold", 76))
        eligible = (
            result.get("sharia_status") == "PRECHECK_PASS"
            and result.get("mtf", {}).get("status") != "FAIL"
            and not bool(result.get("extended"))
            and float(result.get("catalyst", {}).get("score", 0)) > -6
        )
        result["score"] = score
        result["status"] = (
            "PAPER_ENTRY" if eligible and score >= threshold
            else "ARMED" if eligible and score >= threshold - 8
            else "OBSERVE"
        )
        result.setdefault("decision_reason", []).append("Confidence score de-saturated (cap 99)")
        return result

    engine.enrich_candidate = enrich_with_resolved_confidence
