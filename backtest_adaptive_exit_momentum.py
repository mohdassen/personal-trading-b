from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yaml

import backtest_adaptive_momentum as am
import backtest_evidence_momentum as em
import backtest_portfolio_momentum as pm

ROOT = Path(__file__).resolve().parent
ENGINE = "V5.0-Adaptive-Exit-Momentum"

# This is intentionally one fixed exit rule, not another optimization grid.
# The entry/portfolio logic stays identical to V4.9 so we isolate whether
# faster recognition of failed momentum improves payoff stability.
FAILURE_MIN_BARS = 5
FAILURE_SMA = 20
FAILURE_SLOPE_LOOKBACK = 5


def _simulate_adaptive(row, df, cost_bps):
    i = int(row["i"])
    if i + 2 >= len(df):
        return None

    entry_i = i + 1
    entry = float(df["Open"].iloc[entry_i])
    atr = max(float(row["atr"]), 0.01)
    risk = 2.5 * atr
    risk_pct = risk / max(entry, 0.01)
    if risk_pct < 0.012 or risk_pct > 0.12:
        return None

    stop = entry - risk
    target1 = entry + 2.0 * risk
    target2 = entry + 4.0 * risk
    trail = stop
    armed = False

    last = min(entry_i + em.HOLD_DAYS, len(df) - 1)
    exit_price = float(df["Close"].iloc[last])
    exit_i = last
    outcome = "TIME_EXIT"

    sma20 = df["Close"].rolling(FAILURE_SMA).mean()

    for j in range(entry_i, last + 1):
        bar = df.iloc[j]
        op = float(bar["Open"])
        lo = float(bar["Low"])
        hi = float(bar["High"])
        close = float(bar["Close"])
        active_stop = trail

        # Preserve V4.7/V4.9 conservative ordering: adverse stop first.
        if op <= active_stop:
            exit_price = op
            exit_i = j
            outcome = "STOP_GAP"
            break
        if lo <= active_stop:
            exit_price = active_stop
            exit_i = j
            outcome = "STOP"
            break
        if op >= target2:
            exit_price = target2
            exit_i = j
            outcome = "TARGET2"
            break
        if hi >= target2:
            exit_price = target2
            exit_i = j
            outcome = "TARGET2"
            break

        if hi >= target1:
            armed = True

        # Once a winner has reached +2R, keep the existing prior-low trailing
        # logic so V5.0 does not cap the right tail.
        if armed and j > entry_i:
            a = max(entry_i, j - 10)
            prior_low = float(df["Low"].iloc[a:j].min())
            new_trail = prior_low - 0.25 * atr
            trail = max(trail, entry, new_trail)

        # Failed-momentum exit: only after five completed sessions, only while
        # the trade is still below entry, and only when price is below a falling
        # 20-day average. This uses information available at that close only.
        # It is a structural trend-failure rule rather than a tuned loss target.
        bars_held = j - entry_i + 1
        if not armed and bars_held >= FAILURE_MIN_BARS:
            slope_ref = j - FAILURE_SLOPE_LOOKBACK
            if slope_ref >= 0 and pd.notna(sma20.iloc[j]) and pd.notna(sma20.iloc[slope_ref]):
                falling_sma = float(sma20.iloc[j]) < float(sma20.iloc[slope_ref])
                below_sma = close < float(sma20.iloc[j])
                below_entry = close < entry
                if falling_sma and below_sma and below_entry:
                    exit_price = close
                    exit_i = j
                    outcome = "MOMENTUM_FAIL"
                    break

    cost_pct = em._cost_pct(cost_bps)
    cost = entry * (cost_pct / 100.0)
    ret_pct = (exit_price - entry) / entry * 100.0 - cost_pct
    r_mult = (exit_price - entry - cost) / risk

    return {
        "entry_date": str(pd.Timestamp(df.index[entry_i]).date()),
        "exit_date": str(pd.Timestamp(df.index[exit_i]).date()),
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "target1": round(target1, 4),
        "target2": round(target2, 4),
        "risk_pct": round(risk_pct * 100.0, 3),
        "outcome": outcome,
        "return_pct": round(ret_pct, 3),
        "r_multiple": round(r_mult, 3),
        "exit_i": int(exit_i),
    }


def main():
    cfg = yaml.safe_load((ROOT / "config/settings.yml").read_text())["settings"]
    universe = yaml.safe_load((ROOT / "config/universe.yml").read_text())["universe"]
    cost = float(cfg.get("backtest_transaction_cost_bps", 10))

    symbols = list(dict.fromkeys(["SPY"] + list(universe)))
    prices = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(em._download, s): s for s in symbols}
        for f in as_completed(futs):
            s, df = f.result()
            if not df.empty:
                prices[s] = df

    if "SPY" not in prices:
        raise RuntimeError("SPY market data unavailable")

    spy_f = em._spy_regime(prices["SPY"])
    raw = []
    for symbol in universe:
        if symbol in prices:
            raw.extend(em._signal_rows(symbol, prices[symbol], spy_f))
    rows = am._enrich_leadership(raw, prices)

    # Reuse V4.9 entry selection, concentration limits, volatility throttling,
    # and robust validation. Only the execution/exit model is changed.
    original_simulate = em._simulate
    em._simulate = _simulate_adaptive
    try:
        validation = pm._validate(rows, prices, cost)
    finally:
        em._simulate = original_simulate

    historical_pass = bool(validation.get("safe_for_shadow"))
    validation["historical_gate_pass"] = historical_pass
    # Important statistical hygiene: V4.9's recent historical challenge set was
    # inspected before designing this exit rule. Therefore it cannot honestly be
    # called untouched for V5.0. Even a historical pass must NOT auto-authorize
    # shadow/live. Fresh forward observations are required.
    validation["safe_for_shadow"] = False
    validation["research_only"] = True
    validation["fresh_forward_validation_required"] = True
    validation["validation_integrity_note"] = (
        "V5.0 was designed after inspection of V4.9 holdout behavior. Historical "
        "results are a research challenge test, not a new untouched holdout. "
        "No shadow/live authorization can be inferred from them."
    )

    out = {
        "engine": ENGINE,
        "method": (
            "V4.9 portfolio-aware momentum unchanged at entry, with one fixed "
            "momentum-failure exit: after at least five sessions, an unarmed losing "
            "trade exits at the close when price is below a falling SMA20. Winners "
            "that reach +2R retain the existing trailing logic."
        ),
        "years_requested": em.YEARS,
        "holding_days_max": em.HOLD_DAYS,
        "transaction_cost_bps_per_side": cost,
        "universe_size": len(universe),
        "symbols_with_data": len([s for s in universe if s in prices]),
        "weekly_feature_rows": len(rows),
        "base_qualified_signals": sum(bool(x.get("base_ok")) for x in rows),
        "exit_rule": {
            "failure_min_bars": FAILURE_MIN_BARS,
            "failure_sma": FAILURE_SMA,
            "failure_slope_lookback": FAILURE_SLOPE_LOOKBACK,
            "requires_below_entry": True,
            "requires_unarmed_trade": True,
        },
        "validation": validation,
    }

    (ROOT / "data/backtest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    printable = {
        **out,
        "validation": {
            k: v for k, v in validation.items()
            if k not in ("selected_holdout_samples", "fold_report")
        },
    }
    print(json.dumps(printable, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
