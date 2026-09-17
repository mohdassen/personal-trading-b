"""Follow-up research: test each V7 strategy as its own portfolio under V7 risk limits.
Research only; does not change production V7.2 or authorize live trading.
"""
from __future__ import annotations

import json
from pathlib import Path

import v7_edge_research as h


def main():
    frames, errors = {}, {}
    for sym in sorted(set(h.PRIMARY_SYMBOLS + ["SPY", "QQQ", "^VIX"])):
        df = h.download(sym)
        if len(df) < 260:
            errors[sym] = f"insufficient rows: {len(df)}"
        else:
            frames[sym] = df
    if not all(s in frames for s in ("SPY", "QQQ", "^VIX")):
        raise RuntimeError("missing market-regime data")

    regime_df = h.build_regime(frames["SPY"], frames["QQQ"], frames["^VIX"])
    routed, separate = [], []
    for sym in h.PRIMARY_SYMBOLS:
        if sym not in frames:
            continue
        r, s = h.generate_candidates(sym, frames[sym], regime_df)
        routed += r
        separate += s

    combined, _ = h.portfolio_select(routed, frames, threshold_offset=0)
    regime_split = {}
    for regime in ("RISK_ON", "MIXED", "RISK_OFF", "HIGH_VOLATILITY"):
        regime_rows = [t for t in combined if t["regime"] == regime]
        regime_split[regime] = h.split_stats(regime_rows)

    strategy_portfolios = {}
    for setup in h.STRATEGIES:
        candidates = [t for t in separate if t["setup"] == setup]
        selected, rejects = h.portfolio_select(candidates, frames, threshold_offset=0)
        split = h.split_stats(selected)
        holdout = [t for t in selected if h.split_name(t["signal_date"]) == "holdout"]
        strategy_portfolios[setup] = {
            "split": split,
            "holdout_expectancy_bootstrap_95pct": h.bootstrap_ci(holdout),
            "holdout_cost_stress_round_trip_bps": {
                str(int(b)): h.stats(holdout, bps=b) for b in h.STRESS_BPS
            },
            "rejections": rejects,
        }

    report_path = Path("data/v7_edge_research.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["combined_portfolio"]["regime_split"] = regime_split
    report["strategy_portfolios_same_risk_limits"] = strategy_portfolios
    report["strategy_portfolio_followup_errors"] = errors
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps({
        "combined_verdict": report.get("verdict"),
        "strategy_holdouts": {k: v["split"]["holdout"] for k, v in strategy_portfolios.items()},
        "regime_holdouts": {k: v["holdout"] for k, v in regime_split.items()},
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
