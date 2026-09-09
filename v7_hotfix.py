"""V7 execution hotfix.

Fixes three production-research issues without changing the V6.1 baseline:
1) no signal generation outside the regular US session for any invocation type;
2) pending signals are revalidated on their own technical/catalyst conditions, not Top-3 membership;
3) final ranking score is calibrated to avoid 100/100 saturation.

Research/PAPER only. No broker orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from src.trading_bot.market import quote_intraday, recent_news


def _f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _calibrated_score(routed_score, mtf_status, catalyst_score):
    """Keep separation near the top instead of clipping many names at 100."""
    mtf_bonus = 4 if mtf_status == "PASS_STRONG" else 1 if mtf_status == "PASS" else -10
    score = round(0.90 * _f(routed_score) + mtf_bonus + _f(catalyst_score))
    return int(max(0, min(99, score)))


def install(engine):
    original_main = engine.main
    original_enrich = engine.enrich_candidate
    original_update = engine.update_paper_state
    original_add = engine.add_new_picks_to_paper

    def guarded_main():
        # Apply the market-hours guard to schedule, push, workflow_dispatch and local runs alike.
        if not engine._market_window_open():
            now_ny = datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
            print(f"V7 scan skipped outside regular US session: {now_ny}")
            return 0
        return original_main()

    def calibrated_enrich(row, regime_label):
        result = original_enrich(row, regime_label)
        calibrated = _calibrated_score(
            result.get("router", {}).get("routed_score", result.get("score", 0)),
            result.get("mtf", {}).get("status", "FAIL"),
            result.get("catalyst", {}).get("score", 0),
        )
        result["score_uncalibrated"] = result.get("score", 0)
        result["score"] = calibrated
        threshold = int(result.get("threshold", 76))
        eligible = (
            result.get("sharia_status") == "PRECHECK_PASS"
            and result.get("mtf", {}).get("status") != "FAIL"
            and not result.get("extended", False)
            and _f(result.get("catalyst", {}).get("score", 0)) > -6
        )
        result["status"] = (
            "PAPER_ENTRY" if eligible and calibrated >= threshold
            else "ARMED" if eligible and calibrated >= threshold - 8
            else "OBSERVE"
        )
        result["score_model"] = "V7_CALIBRATED_90PCT_ROUTE_PLUS_MTF_CATALYST"
        return result

    def update_open_only(state):
        """Let the proven engine manage only already-open trades; keep pending for explicit revalidation."""
        pending = list(state.get("pending", []))
        working = dict(state)
        working["pending"] = []
        updated, events = original_update(working)
        updated["pending"] = pending
        return updated, events

    def _current_regime():
        try:
            return engine.detect_regime().label
        except Exception:
            return "MIXED"

    def _pending_valid(pos, regime_label):
        """Revalidate the original setup itself; ranking against other names is irrelevant."""
        m = engine.build_metrics(pos["symbol"])
        setup = str(pos.get("setup"))
        raw = _f(m.get("strategy_scores", {}).get(setup, 0))
        weight = engine.router_weights(regime_label).get(setup, 1.0)
        routed = max(0, min(100, round(raw * weight)))
        mtf = engine.mtf_confirmation(
            setup,
            m.get("intraday_positive", False),
            m.get("hourly_positive", False),
            m.get("hourly_bull", False),
            m.get("daily_trend", False),
        )
        try:
            catalyst = engine.classify_catalyst(recent_news(pos["symbol"], 8))
        except Exception:
            catalyst = engine.classify_catalyst([])
        score = _calibrated_score(routed, mtf["status"], catalyst.get("score", 0))
        extended = _f(m.get("vwap_extension_atr")) > 2.5 or _f(m.get("day_change_pct")) > 15
        threshold = engine._threshold(setup, regime_label)
        valid = mtf["status"] != "FAIL" and catalyst.get("score", 0) > -6 and not extended and score >= threshold
        return valid, {
            "setup": setup,
            "raw_score": raw,
            "routed_score": routed,
            "score": score,
            "threshold": threshold,
            "mtf": mtf["status"],
            "catalyst": catalyst.get("sentiment", "NEUTRAL_OR_UNKNOWN"),
            "extended": bool(extended),
        }

    def revalidate_then_add(state, picks):
        rejected = list(state.get("rejected", []))
        activated = list(state.get("open", []))
        kept = []
        active_symbols = {x.get("symbol") for x in activated}
        regime_label = _current_regime()

        for pos in state.get("pending", []):
            try:
                valid, detail = _pending_valid(pos, regime_label)
                if not valid:
                    rejected.append({
                        **pos,
                        "status": "REJECTED",
                        "reason": "TECHNICAL_REVALIDATION_FAILED",
                        "revalidation": detail,
                        "rejected_at": datetime.now(timezone.utc).isoformat(),
                    })
                    continue

                intr = quote_intraday(pos["symbol"], "5d", "15m")
                signal_at = pd.Timestamp(pos["signal_at"])
                if signal_at.tzinfo is None:
                    signal_at = signal_at.tz_localize("UTC")
                bars = intr[intr.index > signal_at]
                if bars.empty:
                    kept.append({**pos, "revalidation": detail})
                    continue

                bar = bars.iloc[0]
                entry = _f(bar["Open"]) * (1 + engine.SLIPPAGE_BPS / 10000)
                if abs(entry - _f(pos["signal_entry"])) > 0.75 * max(_f(pos["atr_ref"]), 0.01):
                    rejected.append({
                        **pos,
                        "status": "REJECTED",
                        "reason": "ENTRY_GAP",
                        "revalidation": detail,
                        "rejected_at": str(bar.name),
                    })
                    continue

                risk = max(_f(pos["signal_entry"]) - _f(pos["signal_stop"]), 0.01)
                target_r = 1.8 if pos.get("setup") == "SWING_CONTINUATION" else 1.6
                active = {
                    **pos,
                    "status": "OPEN",
                    "entry": round(entry, 4),
                    "stop": round(entry - risk, 4),
                    "target1": round(entry + target_r * risk, 4),
                    "opened_at": str(bar.name),
                    "last_processed": str(bar.name),
                    "bars_held": 0,
                    "revalidation": detail,
                }
                if active["symbol"] not in active_symbols:
                    activated.append(active)
                    active_symbols.add(active["symbol"])
            except Exception as exc:
                kept.append({**pos, "revalidation_error": str(exc)})

        state["pending"] = kept
        state["open"] = activated
        state["rejected"] = rejected[-500:]
        return original_add(state, picks)

    engine.enrich_candidate = calibrated_enrich
    engine.update_paper_state = update_open_only
    engine.add_new_picks_to_paper = revalidate_then_add
    engine.main = guarded_main
