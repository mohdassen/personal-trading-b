"""Setup-specific entry quality gate for V7 PAPER research."""
from __future__ import annotations


def _f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def entry_quality(candidate):
    setup = str(candidate.get("setup") or "")
    mtf = str((candidate.get("mtf") or {}).get("status") or "FAIL")
    above_vwap = bool(candidate.get("above_vwap"))
    rvol = _f(candidate.get("relative_volume"), 1.0)
    ret1h = _f(candidate.get("ret1h_pct"))
    daily = bool(candidate.get("daily_trend"))
    extended = bool(candidate.get("extended"))
    score = 0
    reasons = []

    if mtf == "PASS_STRONG": score += 30
    elif mtf == "PASS": score += 18
    else: reasons.append("MTF_FAIL")

    if setup == "SWING_CONTINUATION":
        if daily: score += 30
        else: reasons.append("NO_DAILY_TREND")
        if _f(candidate.get("ret5d_pct")) > 0: score += 15
        if _f(candidate.get("ret20d_pct")) >= 4: score += 15
        if rvol >= 0.8: score += 10
        hard_pass = daily and mtf != "FAIL"
    else:
        if above_vwap: score += 25
        else: reasons.append("BELOW_VWAP")
        if rvol >= 1.5: score += 20
        elif rvol >= 1.1: score += 12
        else: reasons.append("LOW_SESSION_RVOL")
        if ret1h >= 0: score += 15
        elif ret1h >= -0.5: score += 6
        else: reasons.append("WEAK_1H_MOMENTUM")
        if daily: score += 10
        hard_pass = above_vwap and mtf != "FAIL" and ret1h >= -0.5

    if extended:
        score -= 25
        reasons.append("EXTENDED")
        hard_pass = False

    score = max(0, min(100, int(round(score))))
    return {"score": score, "pass": bool(hard_pass and score >= 65), "reasons": reasons}


def install(engine):
    original = engine.enrich_candidate
    def enrich_with_entry_quality(row, regime_label):
        result = original(row, regime_label)
        quality = entry_quality(result)
        result["entry_quality"] = quality
        if result.get("status") == "PAPER_ENTRY" and not quality["pass"]:
            result["status"] = "ARMED" if quality["score"] >= 55 else "OBSERVE"
            result.setdefault("decision_reason", []).append("Entry quality gate blocked immediate Paper entry")
        return result
    engine.enrich_candidate = enrich_with_entry_quality
