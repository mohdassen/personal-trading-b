from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import backtest_adaptive_momentum as am
import backtest_portfolio_momentum as pm
import v49_shadow as shadow

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data/v49_fast_research.json"


def _reason(row, sharia, groups):
    symbol = str(row["symbol"])
    group = groups.get(symbol, f"UNGROUPED:{symbol}")
    if group in sharia["excluded_groups"] or symbol in sharia["excluded_symbols"]:
        return "SHARIA_PRECHECK"
    if not row.get("base_ok"):
        return "BASE_SETUP"
    if int(row.get("score", 0)) < shadow.THRESHOLD:
        return "SCORE"
    atr = max(float(row.get("atr", 0.0)), 0.01)
    close = max(float(row.get("close", 0.0)), 0.01)
    risk_pct = 2.5 * atr / close * 100.0
    if risk_pct < 1.2:
        return "RISK_TOO_TIGHT"
    if risk_pct > pm.MAX_TRADE_RISK_PCT:
        return "RISK_TOO_WIDE"
    if float(row.get("spy_vol20", 9.0)) > pm.MAX_SPY_VOL20_FOR_NEW_RISK:
        return "MARKET_VOL"
    return "CANDIDATE"


def main():
    clock = datetime.now(timezone.utc)
    clock_ny = shadow.now_ny()
    universe = list(shadow.load_yaml(ROOT / "config/universe.yml").get("universe", []))
    prices = shadow._download_prices(["SPY"] + universe, clock_ny)
    spy = prices["SPY"]
    completed = spy.index[-1]
    for symbol in universe:
        if completed not in prices[symbol].index:
            raise RuntimeError(f"Fast research alignment failure: {symbol} missing {completed.date()}")

    spy_f = shadow.em._spy_regime(spy)
    raw = []
    for symbol in universe:
        row = shadow._row_at(symbol, prices[symbol], spy_f, completed)
        if row is None:
            raise RuntimeError(f"Fast research feature failure: {symbol} @ {completed.date()}")
        raw.append(row)
    rows = am._enrich_leadership(raw, prices)
    sharia = shadow._sharia_policy()
    groups = pm._group_map()
    reasons = Counter(_reason(r, sharia, groups) for r in rows)
    candidates = shadow._candidate_rows(rows)

    near = []
    for r in rows:
        reason = _reason(r, sharia, groups)
        if reason == "SHARIA_PRECHECK":
            continue
        near.append({
            "symbol": r["symbol"],
            "score": int(r.get("score", 0)),
            "reason": reason,
            "mom12_rank": round(float(r.get("mom12_rank", 0.0)), 3),
            "mom6_rank": round(float(r.get("mom6_rank", 0.0)), 3),
            "spy_vol20": round(float(r.get("spy_vol20", 0.0)), 4),
        })
    near.sort(key=lambda x: (x["reason"] == "CANDIDATE", x["score"], x["mom12_rank"]), reverse=True)

    history = shadow.load_json(OUT, {}).get("sessions", [])
    history = [x for x in history if x.get("session") != str(completed.date())]
    session = {
        "session": str(completed.date()),
        "captured_at": clock.isoformat(),
        "universe": len(universe),
        "candidate_count": len(candidates),
        "rejection_counts": dict(sorted(reasons.items())),
        "top_near_candidates": near[:12],
    }
    history.append(session)
    history = history[-30:]

    last5 = history[-5:]
    candidate_sessions = sum(1 for x in last5 if int(x.get("candidate_count", 0)) > 0)
    total_candidates = sum(int(x.get("candidate_count", 0)) for x in last5)
    combined = Counter()
    for x in last5:
        combined.update(x.get("rejection_counts", {}))
    bottleneck = None
    non_candidate = [(k, v) for k, v in combined.items() if k not in ("CANDIDATE", "SHARIA_PRECHECK")]
    if non_candidate:
        bottleneck = max(non_candidate, key=lambda kv: kv[1])[0]

    if len(last5) < 5:
        status = "LEARNING"
    elif total_candidates == 0:
        status = "TOO_INACTIVE_RESEARCH_REQUIRED"
    elif candidate_sessions <= 1:
        status = "LOW_ACTIVITY_WATCH"
    else:
        status = "HEALTHY_ACTIVITY"

    out = {
        "engine": "V4.9-Fast-Research-Diagnostics",
        "mode": "RESEARCH_ONLY_DO_NOT_TRADE",
        "updated_at": clock.isoformat(),
        "status": status,
        "sessions_observed": len(history),
        "last5": {
            "sessions": len(last5),
            "candidate_sessions": candidate_sessions,
            "total_candidates": total_candidates,
            "dominant_bottleneck": bottleneck,
        },
        "decision_rule": {
            "after_5_sessions_zero_candidates": "research a separate higher-activity challenger; never loosen frozen V4.9 in place",
            "low_activity": "diagnose repeated rejection gate before designing challenger",
            "healthy_activity": "keep V4.9 frozen and continue forward collection",
        },
        "sessions": history,
    }
    shadow.save_json(OUT, out)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
