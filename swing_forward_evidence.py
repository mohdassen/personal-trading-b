"""Extract clean forward evidence for SWING_CONTINUATION only.

Research/reporting only. Does not alter V7.2 rules or authorize live trading.
Reads the current V7.2 paper state copied from the research branch and reports
closed-trade evidence plus currently open SWING positions.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STATE = Path("data/v7_paper_state.json")
OUT = Path("data/swing_forward_evidence.json")
EPOCH = "V7.2_FORWARD_2026-09-09"
SETUP = "SWING_CONTINUATION"


def f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def metrics(rows):
    rs = [f(x.get("r")) for x in rows if x.get("r") is not None]
    if not rs:
        return {
            "samples": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "expectancy_r": 0.0, "profit_factor": 0.0, "total_r": 0.0,
            "max_drawdown_r": 0.0, "losing_streak": 0,
        }
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r <= 0]
    pf = sum(wins) / sum(losses) if losses and sum(losses) > 0 else (99.0 if wins else 0.0)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = best_streak = 0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if r <= 0:
            streak += 1
            best_streak = max(best_streak, streak)
        else:
            streak = 0
    return {
        "samples": len(rs), "wins": len(wins), "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(rs), 1),
        "expectancy_r": round(sum(rs) / len(rs), 3),
        "profit_factor": round(pf, 2), "total_r": round(sum(rs), 2),
        "max_drawdown_r": round(max_dd, 2), "losing_streak": best_streak,
    }


def compact_trade(x):
    keys = (
        "symbol", "signal_day", "signal_regime", "score", "mtf", "entry",
        "stop", "target1", "exit", "exit_reason", "r", "opened_at", "closed_at",
        "mae_r", "mfe_r",
    )
    return {k: x.get(k) for k in keys if k in x}


def compact_open(x):
    return {
        "symbol": x.get("symbol"), "signal_day": x.get("signal_day"),
        "regime": x.get("signal_regime"), "score": x.get("score"),
        "mtf": x.get("mtf"), "entry": x.get("entry"), "stop": x.get("stop"),
        "target1": x.get("target1"), "mark": x.get("mark"),
        "unrealized_r": x.get("unrealized_r"), "mae_r": x.get("mae_r"),
        "mfe_r": x.get("mfe_r"), "bars_held": x.get("bars_held"),
    }


def main():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    closed = [
        x for x in state.get("closed", [])
        if x.get("paper_epoch") == EPOCH and x.get("setup") == SETUP
    ]
    opened = [
        x for x in state.get("open", [])
        if x.get("paper_epoch") == EPOCH and x.get("setup") == SETUP
    ]
    pending = [
        x for x in state.get("pending", [])
        if x.get("paper_epoch") == EPOCH and x.get("setup") == SETUP
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "V7.2 SWING Clean Forward Evidence",
        "mode": "RESEARCH_PAPER_ONLY",
        "paper_epoch": EPOCH,
        "setup": SETUP,
        "production_v7_changed": False,
        "live_execution_authorized": False,
        "closed_metrics": metrics(closed),
        "closed_trades": [compact_trade(x) for x in closed],
        "open_count": len(opened),
        "open_positions": [compact_open(x) for x in opened],
        "pending_count": len(pending),
        "interpretation": "Closed trades are the only realized forward evidence. Open P/L is shown separately and is not counted in expectancy or profit factor.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "closed_metrics": report["closed_metrics"],
        "open_count": report["open_count"],
        "open_positions": report["open_positions"],
        "pending_count": report["pending_count"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
