"""Technical revalidation for V7 paper execution.

A pending signal may activate on the next 15m bar only if its ORIGINAL setup
is still technically valid. It is no longer cancelled merely because another
symbol displaced it from the current top-3 ranking.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from src.trading_bot.market import quote_intraday
from v7_quality_rules import confidence_score

NY = ZoneInfo("America/New_York")


def _f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _signal_in_market_window(signal_at):
    try:
        ts = pd.Timestamp(signal_at)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        local = ts.tz_convert(NY)
        minutes = local.hour * 60 + local.minute
        return local.weekday() < 5 and 575 <= minutes <= 965
    except Exception:
        return False


def _current_regime(engine):
    try:
        return engine.detect_regime().label
    except Exception:
        return "MIXED"


def _technical_revalidation(engine, pos, regime_label):
    if not _signal_in_market_window(pos.get("signal_at")):
        return False, "OUT_OF_SESSION_SIGNAL"

    try:
        m = engine.build_metrics(pos["symbol"])
        setup = str(pos.get("setup") or "")
        mtf = engine.mtf_confirmation(
            setup,
            m["intraday_positive"],
            m["hourly_positive"],
            m["hourly_bull"],
            m["daily_trend"],
        )
        if mtf["status"] == "FAIL":
            return False, "MTF_INVALIDATED_BEFORE_ENTRY"

        try:
            catalyst = engine.classify_catalyst(engine.recent_news(pos["symbol"], 8))
        except Exception:
            catalyst = engine.classify_catalyst([])
        if _f(catalyst.get("score")) <= -6:
            return False, "NEGATIVE_CATALYST_BEFORE_ENTRY"

        if _f(m.get("vwap_extension_atr")) > 2.5 or _f(m.get("day_change_pct")) > 15:
            return False, "EXTENDED_BEFORE_ENTRY"

        raw = _f(m.get("strategy_scores", {}).get(setup))
        weight = _f(engine.router_weights(regime_label).get(setup), 1.0)
        routed = max(0, min(100, round(raw * weight)))
        current_score = confidence_score(routed, mtf["status"], catalyst.get("score", 0))
        threshold = engine._threshold(setup, regime_label)
        if current_score < threshold:
            return False, "SETUP_SCORE_INVALIDATED_BEFORE_ENTRY"

        return True, {
            "revalidated_score": current_score,
            "revalidated_mtf": mtf["status"],
            "revalidated_catalyst": catalyst.get("sentiment", "NEUTRAL_OR_UNKNOWN"),
            "revalidated_at": datetime.now(tz=NY).isoformat(),
        }
    except Exception as exc:
        # Fail closed for activation. Keep pending so a transient free-data
        # error does not fabricate a rejection or an entry.
        return None, f"REVALIDATION_DATA_ERROR:{type(exc).__name__}"


def install(engine):
    original_update = engine.update_paper_state
    original_add = engine.add_new_picks_to_paper

    def update_open_only(state):
        held_pending = list(state.get("pending", []))
        working = dict(state)
        working["pending"] = []
        updated, events = original_update(working)
        updated["pending"] = held_pending
        return updated, events

    def revalidate_then_add(state, picks):
        kept = []
        rejected = list(state.get("rejected", []))
        activated = list(state.get("open", []))
        active_symbols = {x.get("symbol") for x in activated}
        regime_label = _current_regime(engine)

        for pos in state.get("pending", []):
            valid, detail = _technical_revalidation(engine, pos, regime_label)
            if valid is False:
                rejected.append({**pos, "status": "REJECTED", "reason": detail})
                continue
            if valid is None:
                kept.append(pos)
                continue

            try:
                intr = quote_intraday(pos["symbol"], "5d", "15m")
                signal_at = pd.Timestamp(pos["signal_at"])
                if signal_at.tzinfo is None:
                    signal_at = signal_at.tz_localize("UTC")
                bars = intr[intr.index > signal_at]
                if bars.empty:
                    kept.append({**pos, **detail})
                    continue

                bar = bars.iloc[0]
                entry = _f(bar["Open"]) * (1 + engine.SLIPPAGE_BPS / 10000)
                if abs(entry - _f(pos["signal_entry"])) > 0.75 * max(_f(pos["atr_ref"]), 0.01):
                    rejected.append({**pos, **detail, "status": "REJECTED", "reason": "ENTRY_GAP", "rejected_at": str(bar.name)})
                    continue

                risk = max(_f(pos["signal_entry"]) - _f(pos["signal_stop"]), 0.01)
                target_r = 1.8 if pos.get("setup") == "SWING_CONTINUATION" else 1.6
                active = {
                    **pos,
                    **detail,
                    "status": "OPEN",
                    "entry": round(entry, 4),
                    "stop": round(entry - risk, 4),
                    "target1": round(entry + target_r * risk, 4),
                    "opened_at": str(bar.name),
                    "last_processed": str(bar.name),
                    "bars_held": 0,
                }
                if active["symbol"] not in active_symbols:
                    activated.append(active)
                    active_symbols.add(active["symbol"])
            except Exception:
                kept.append(pos)

        state["pending"] = kept
        state["open"] = activated
        state["rejected"] = rejected[-500:]
        state = original_add(state, picks)
        return state

    engine.update_paper_state = update_open_only
    engine.add_new_picks_to_paper = revalidate_then_add
