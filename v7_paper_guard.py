"""Pending-signal revalidation for V7 paper execution.

A signal may only activate on the next 15m bar if it is still present in the
current V7 paper-pick lineup. This prevents stale signals from opening after a
new catalyst/MTF/router decision invalidates them.
"""
from __future__ import annotations

import pandas as pd

from src.trading_bot.market import quote_intraday


def _f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


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
        current = {(x.get("symbol"), x.get("setup")) for x in picks}
        kept = []
        rejected = list(state.get("rejected", []))
        activated = list(state.get("open", []))
        active_symbols = {x.get("symbol") for x in activated}

        for pos in state.get("pending", []):
            key = (pos.get("symbol"), pos.get("setup"))
            if key not in current:
                rejected.append({**pos, "status": "REJECTED", "reason": "SIGNAL_INVALIDATED_BEFORE_ENTRY"})
                continue
            try:
                intr = quote_intraday(pos["symbol"], "5d", "15m")
                signal_at = pd.Timestamp(pos["signal_at"])
                if signal_at.tzinfo is None:
                    signal_at = signal_at.tz_localize("UTC")
                bars = intr[intr.index > signal_at]
                if bars.empty:
                    kept.append(pos)
                    continue
                bar = bars.iloc[0]
                entry = _f(bar["Open"]) * (1 + engine.SLIPPAGE_BPS / 10000)
                if abs(entry - _f(pos["signal_entry"])) > 0.75 * max(_f(pos["atr_ref"]), 0.01):
                    rejected.append({**pos, "status": "REJECTED", "reason": "ENTRY_GAP", "rejected_at": str(bar.name)})
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
