"""Lightweight V7 paper lifecycle monitor.

Interleaves broad scans. No discovery and no broker execution.
"""
from __future__ import annotations

from datetime import datetime, timezone

import v7_runner  # installs the same V7 guards/risk/quality/validation stack
import v7_decision_engine as engine
from v7_observability import enrich_state
from v7_validation import apply_state_validation


def _notify(state, newly_open, newly_closed):
    if not newly_open and not newly_closed: return
    if not engine.telegram_enabled(): return
    lines = ["🧪 <b>V7 Paper Monitor</b>"]
    open_map = {x.get("symbol"): x for x in state.get("open", [])}
    for symbol in newly_open:
        p = open_map.get(symbol, {})
        lines.append(f"🟡 OPEN {symbol} @ ${float(p.get('entry',0)):.2f} | {int(p.get('shares',0) or 0)} shares | Paper")
    for p in newly_closed:
        r = float(p.get("r", 0) or 0); icon = "✅" if r > 0 else "❌"
        pnl = p.get("pnl_dollars")
        dollar = f" | ${float(pnl):+.2f}" if pnl is not None else ""
        lines.append(f"{icon} CLOSE {p.get('symbol')} {r:+.2f}R{dollar} | {p.get('exit_reason')}")
    v = state.get("validation", {})
    s = v.get("clean_forward_stats", {})
    lines.append(f"Forward: {s.get('samples',0)}/30 | Exp {s.get('expectancy_r',0):+.3f}R | PF {s.get('profit_factor',0)} | DD {s.get('max_drawdown_r',0)}R")
    lines.append(f"Status: {v.get('status','COLLECTING_FORWARD_DATA')} | Live execution OFF")
    try: engine.send("\n".join(lines))
    except Exception as exc: print("V7 monitor Telegram warning:", exc)


def main():
    if not engine._market_window_open():
        print("V7 monitor outside US session; skipped.")
        return 0
    state = engine._load(engine.PAPER_STATE, {"engine":engine.ENGINE,"mode":engine.MODE,"pending":[],"open":[],"closed":[],"rejected":[]})
    before_open = {x.get("symbol") for x in state.get("open", [])}
    before_closed = len(state.get("closed", []))
    state, events = engine.update_paper_state(state)
    state = engine.add_new_picks_to_paper(state, [])
    state = enrich_state(state)
    state = apply_state_validation(state)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()

    after_open = {x.get("symbol") for x in state.get("open", [])}
    newly_open = sorted(after_open - before_open)
    newly_closed = state.get("closed", [])[before_closed:]
    _notify(state, newly_open, newly_closed)
    engine._save(engine.PAPER_STATE, state)
    print({"monitor":"V7_LIGHTWEIGHT_15M","newly_open":newly_open,"newly_closed":[{"symbol":x.get("symbol"),"r":x.get("r"),"reason":x.get("exit_reason")} for x in newly_closed],"pending":len(state.get("pending",[])),"open":len(state.get("open",[])),"metrics":state.get("metrics",{}),"ledger":state.get("portfolio_ledger",{}),"validation":state.get("validation",{})})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
