"""Lightweight V7 paper lifecycle monitor.

Runs between broad discovery scans. It does not discover new symbols; it only
revalidates pending signals, advances open trades, records MAE/MFE/P&L and
persists the portfolio ledger. Research/PAPER only.
"""
from __future__ import annotations

from datetime import datetime, timezone

import v7_runner  # installs the same V7 guards/risk/observability stack
import v7_decision_engine as engine
from v7_observability import enrich_state


def main():
    if not engine._market_window_open():
        print("V7 monitor outside US session; skipped.")
        return 0
    state = engine._load(engine.PAPER_STATE, {"engine": engine.ENGINE, "mode": engine.MODE, "pending": [], "open": [], "closed": [], "rejected": []})
    before_open = {x.get("symbol") for x in state.get("open", [])}
    before_closed = len(state.get("closed", []))

    state, events = engine.update_paper_state(state)
    # Empty picks means: revalidate/activate existing pending signals only;
    # never create a new candidate in this lightweight monitor.
    state = engine.add_new_picks_to_paper(state, [])
    state = enrich_state(state)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    engine._save(engine.PAPER_STATE, state)

    after_open = {x.get("symbol") for x in state.get("open", [])}
    newly_open = sorted(after_open - before_open)
    newly_closed = state.get("closed", [])[before_closed:]
    print({
        "monitor": "V7_LIGHTWEIGHT_15M",
        "newly_open": newly_open,
        "newly_closed": [{"symbol": x.get("symbol"), "r": x.get("r"), "reason": x.get("exit_reason")} for x in newly_closed],
        "pending": len(state.get("pending", [])),
        "open": len(state.get("open", [])),
        "metrics": state.get("metrics", {}),
        "ledger": state.get("portfolio_ledger", {}),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
