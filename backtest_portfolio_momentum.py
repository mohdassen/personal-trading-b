from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml

import backtest_adaptive_momentum as am
import backtest_evidence_momentum as em

ROOT = Path(__file__).resolve().parent
ENGINE = "V4.9-Portfolio-Aware-Momentum"
SCORE_GRID = em.SCORE_GRID

MAX_OPEN_POSITIONS = 5
MAX_GROUP_POSITIONS = 1
MAX_NEW_PER_SIGNAL_DATE = 3
MAX_ENTRY_GAP_ATR = 0.75
MAX_TRADE_RISK_PCT = 8.5
MAX_SPY_VOL20_FOR_NEW_RISK = 0.30
DATA_DOWNLOAD_ATTEMPTS = 4
DATA_RETRY_SECONDS = (0, 2, 5, 10)
MIN_HISTORY_ROWS = 310


def _group_map():
    cfg = yaml.safe_load((ROOT / "config/groups.yml").read_text()) or {}
    out = {}
    for group, symbols in (cfg.get("groups") or {}).items():
        for symbol in symbols or []:
            out[str(symbol)] = str(group)
    return out


def _load_complete_prices(symbols):
    """Load a deterministic complete snapshot or fail closed.

    yfinance uses a local SQLite cache. Parallel ticker downloads can contend on
    that cache and intermittently return empty frames (observed as
    OperationalError: database is locked). A partial universe materially changes
    cross-sectional ranks, portfolio choices and validation dates, so V4.9 must
    never publish a result from incomplete inputs.
    """
    prices = {}
    failures = {}

    # Deliberately serial. Reliability/reproducibility is more important than a
    # few seconds of backtest runtime for a weekly validation job.
    for symbol in symbols:
        last_reason = "empty"
        df = pd.DataFrame()
        for attempt in range(DATA_DOWNLOAD_ATTEMPTS):
            delay = DATA_RETRY_SECONDS[min(attempt, len(DATA_RETRY_SECONDS) - 1)]
            if delay:
                time.sleep(delay)
            returned_symbol, candidate = em._download(symbol)
            if returned_symbol != symbol:
                last_reason = f"symbol_mismatch:{returned_symbol}"
                continue
            if candidate is None or candidate.empty:
                last_reason = "empty_download"
                continue
            if len(candidate) < MIN_HISTORY_ROWS:
                last_reason = f"short_history:{len(candidate)}"
                continue
            df = candidate
            break

        if df.empty:
            failures[symbol] = last_reason
        else:
            prices[symbol] = df

    if failures:
        missing = ", ".join(f"{s}({r})" for s, r in failures.items())
        raise RuntimeError(
            "Incomplete market-data snapshot; refusing to validate or publish. "
            f"Missing/invalid: {missing}"
        )

    if len(prices) != len(symbols):
        raise RuntimeError(
            f"Market-data completeness invariant failed: {len(prices)}/{len(symbols)}"
        )
    return prices


def _candidate_trades(rows, prices, cost_bps, threshold):
    groups = _group_map()
    out = []
    for r in rows:
        if not r.get("base_ok") or int(r["score"]) < threshold:
            continue
        symbol = r["symbol"]
        df = prices.get(symbol)
        if df is None or df.empty:
            continue
        sim = em._simulate(r, df, cost_bps)
        if sim is None:
            continue

        entry = float(sim["entry"])
        signal_close = max(float(r["close"]), 0.01)
        atr = max(float(r["atr"]), 0.01)
        gap_atr = abs(entry - signal_close) / atr
        risk_pct = float(sim["risk_pct"])
        spy_vol20 = float(r.get("spy_vol20", 9.0))

        # Fixed, non-optimized entry hygiene. Avoid chasing a large next-session
        # gap and avoid single-name tail risk from extremely wide ATR stops.
        if gap_atr > MAX_ENTRY_GAP_ATR:
            continue
        if risk_pct > MAX_TRADE_RISK_PCT:
            continue
        if spy_vol20 > MAX_SPY_VOL20_FOR_NEW_RISK:
            continue

        quality = (
            float(r["score"])
            + 6.0 * float(r.get("mom12_rank", 0.0))
            + 4.0 * float(r.get("mom6_rank", 0.0))
            + 2.0 * float(r.get("leadership_breadth50", 0.0))
            - 8.0 * max(0.0, float(r.get("vol20", 0.0)) - 0.35)
        )
        out.append({
            "symbol": symbol,
            "group": groups.get(symbol, f"UNGROUPED:{symbol}"),
            "strategy": "POSITION",
            "setup_type": "PORTFOLIO_MOMENTUM",
            "signal_date": r["date"],
            "timestamp": r["timestamp"],
            "score": int(r["score"]),
            "quality": round(quality, 4),
            "mom12_1": round(float(r["mom12_1"]), 4),
            "mom6_1": round(float(r["mom6_1"]), 4),
            "mom12_rank": round(float(r["mom12_rank"]), 3),
            "mom6_rank": round(float(r["mom6_rank"]), 3),
            "near52": round(float(r["near52"]), 4),
            "spy_vol20": round(spy_vol20, 4),
            "leadership_breadth50": round(float(r.get("leadership_breadth50", 0.0)), 3),
            "leadership_spread20": round(float(r.get("leadership_spread20", 0.0)), 4),
            "entry_gap_atr": round(gap_atr, 3),
            **{k: v for k, v in sim.items() if k != "exit_i"},
        })
    return out


def _exposure_limits(spy_vol20):
    # Volatility-managed exposure: fewer simultaneous bets as market volatility
    # rises, instead of assuming every regime deserves the same gross risk.
    if spy_vol20 >= 0.24:
        return 2, 1
    if spy_vol20 >= 0.20:
        return 3, 2
    return MAX_OPEN_POSITIONS, MAX_NEW_PER_SIGNAL_DATE


def _portfolio_select(candidates):
    if not candidates:
        return []

    by_date = {}
    for x in candidates:
        by_date.setdefault(x["signal_date"], []).append(x)

    selected = []
    open_positions = []

    for signal_date in sorted(by_date):
        todays = sorted(
            by_date[signal_date],
            key=lambda x: (float(x["quality"]), int(x["score"]), float(x["mom12_rank"])),
            reverse=True,
        )
        if not todays:
            continue

        entry_date = min(pd.Timestamp(x["entry_date"]) for x in todays)
        open_positions = [
            p for p in open_positions if pd.Timestamp(p["exit_date"]) >= entry_date
        ]

        spy_vol20 = float(todays[0]["spy_vol20"])
        max_open, max_new = _exposure_limits(spy_vol20)
        new_count = 0

        for x in todays:
            if new_count >= max_new or len(open_positions) >= max_open:
                break
            x_entry = pd.Timestamp(x["entry_date"])
            active = [
                p for p in open_positions if pd.Timestamp(p["exit_date"]) >= x_entry
            ]
            open_positions = active
            if any(p["symbol"] == x["symbol"] for p in active):
                continue

            group_counts = Counter(p["group"] for p in active)
            if group_counts[x["group"]] >= MAX_GROUP_POSITIONS:
                continue

            selected.append(x)
            open_positions.append(x)
            new_count += 1

    return sorted(selected, key=lambda x: x["timestamp"])


def _trades(rows, prices, cost_bps, threshold):
    return _portfolio_select(_candidate_trades(rows, prices, cost_bps, threshold))


def _validate(rows, prices, cost_bps):
    all_by = {s: _trades(rows, prices, cost_bps, s) for s in SCORE_GRID}
    ref = all_by[min(SCORE_GRID)]
    dates = sorted(set(x["signal_date"] for x in ref))
    if len(dates) < 30:
        return {
            "status": "INSUFFICIENT_DATA",
            "safe_for_shadow": False,
            "robust_candidate_count": 0,
        }

    cut = dates[max(1, int(len(dates) * 0.85)) - 1]
    folds = ((0.00, 0.35), (0.20, 0.55), (0.40, 0.75), (0.60, 1.00))
    candidates = []
    fold_report = {}

    for threshold, trs in all_by.items():
        selection = [x for x in trs if x["signal_date"] <= cut]
        selection_stats = em._stats(selection)
        fs = [em._stats(em._date_slice(selection, a, b)) for a, b in folds]
        fold_report[str(threshold)] = fs
        useful = [x for x in fs if int(x["samples"]) >= 10]
        if len(useful) < 3:
            continue

        exps = [float(x["expectancy_r"]) for x in useful]
        pfs = [float(x["profit_factor_r"]) for x in useful]
        dds = [float(x["max_drawdown_r"]) for x in useful]
        pos = sum(e > 0 for e in exps) / len(exps)
        med = float(pd.Series(exps).median())
        worst = min(exps)
        medpf = float(pd.Series(pfs).median())
        worst_dd = max(dds)

        # Preserve V4.8's pre-existing worst-fold tolerance (-0.10R) so the
        # experiment isolates the actual fix: drawdown stability. The new gate
        # rejects parameter regions that hide an extreme loss cluster in one fold.
        if (
            int(selection_stats["samples"]) >= 60
            and pos >= 0.75
            and med > 0.05
            and worst > -0.10
            and medpf >= 1.15
            and worst_dd <= 12.0
        ):
            candidates.append({
                "score": threshold,
                "selection": selection_stats,
                "positive_fold_ratio": round(pos, 3),
                "median_fold_expectancy_r": round(med, 3),
                "worst_fold_expectancy_r": round(worst, 3),
                "median_fold_profit_factor": round(medpf, 2),
                "worst_fold_drawdown_r": round(worst_dd, 3),
                "fold_samples": [int(x["samples"]) for x in fs],
                "folds": fs,
            })

    passing = {x["score"] for x in candidates}
    robust = [
        x for x in candidates
        if x["score"] - 5 in passing or x["score"] + 5 in passing
    ]
    robust.sort(
        key=lambda x: (
            x["median_fold_expectancy_r"],
            -x["worst_fold_drawdown_r"],
            x["selection"]["samples"],
        ),
        reverse=True,
    )
    selected = robust[0] if robust else None

    if selected:
        holdout = [
            x for x in all_by[selected["score"]]
            if x["signal_date"] > cut
        ]
        hs = em._stats(holdout)
        halves = [
            em._stats(em._date_slice(holdout, 0, 0.5)),
            em._stats(em._date_slice(holdout, 0.5, 1.0)),
        ]
        halves_ok = all(
            int(x["samples"]) >= 6
            and float(x["expectancy_r"]) > -0.05
            and float(x["max_drawdown_r"]) <= 6.0
            for x in halves
        )
    else:
        holdout = []
        hs = em._stats([])
        halves = [em._stats([]), em._stats([])]
        halves_ok = False

    safe = bool(
        selected
        and hs["samples"] >= 15
        and hs["expectancy_r"] > 0.05
        and hs["profit_factor_r"] >= 1.15
        and hs["max_drawdown_r"] <= 8.0
        and halves_ok
    )

    return {
        "status": "PASS" if safe else ("HOLDOUT_FAIL" if selected else "NO_ROBUST_REGION"),
        "selection_end_date": cut,
        "robust_candidate_count": len(robust),
        "selected": selected,
        "final_holdout": hs,
        "holdout_halves": halves,
        "safe_for_shadow": safe,
        "acceptance_gate": {
            "holdout_min_samples": 15,
            "holdout_min_expectancy_r": 0.05,
            "holdout_min_profit_factor": 1.15,
            "holdout_max_drawdown_r": 8.0,
            "each_half_min_samples": 6,
            "each_half_min_expectancy_r": -0.05,
            "each_half_max_drawdown_r": 6.0,
        },
        "portfolio_constraints": {
            "max_open_positions_normal": MAX_OPEN_POSITIONS,
            "max_group_positions": MAX_GROUP_POSITIONS,
            "max_new_per_signal_date_normal": MAX_NEW_PER_SIGNAL_DATE,
            "max_entry_gap_atr": MAX_ENTRY_GAP_ATR,
            "max_trade_risk_pct": MAX_TRADE_RISK_PCT,
            "max_spy_vol20_for_new_risk": MAX_SPY_VOL20_FOR_NEW_RISK,
            "volatility_managed_exposure": True,
        },
        "threshold_overall": {str(s): em._stats(v) for s, v in all_by.items()},
        "fold_report": fold_report,
        "selected_holdout_samples": holdout[-500:],
        "holdout_note": (
            "Chronologically held out inside this V4.9 run. Because earlier strategy "
            "generations have already examined overlapping market history, PASS can "
            "authorize Shadow/Paper only, never direct live activation."
        ),
    }


def main():
    cfg = yaml.safe_load((ROOT / "config/settings.yml").read_text())["settings"]
    universe = yaml.safe_load((ROOT / "config/universe.yml").read_text())["universe"]
    cost = float(cfg.get("backtest_transaction_cost_bps", 10))

    symbols = list(dict.fromkeys(["SPY"] + list(universe)))
    prices = _load_complete_prices(symbols)

    spy_f = em._spy_regime(prices["SPY"])
    raw = []
    for symbol in universe:
        raw.extend(em._signal_rows(symbol, prices[symbol], spy_f))

    rows = am._enrich_leadership(raw, prices)
    validation = _validate(rows, prices, cost)

    out = {
        "engine": ENGINE,
        "method": (
            "Evidence-based cross-sectional momentum with V4.8 leadership health, "
            "plus portfolio-aware top-ranked selection, group concentration caps, "
            "entry-gap/tail-risk hygiene, volatility-managed exposure, and "
            "drawdown-aware multi-window validation."
        ),
        "years_requested": em.YEARS,
        "holding_days_max": em.HOLD_DAYS,
        "transaction_cost_bps_per_side": cost,
        "universe_size": len(universe),
        "symbols_with_data": len(universe),
        "data_integrity": {
            "required_symbols_including_spy": len(symbols),
            "loaded_symbols_including_spy": len(prices),
            "universe_complete": True,
            "download_mode": "serial_with_retries",
            "download_attempts_per_symbol": DATA_DOWNLOAD_ATTEMPTS,
            "min_history_rows": MIN_HISTORY_ROWS,
            "fail_closed_on_missing_symbol": True,
        },
        "weekly_feature_rows": len(rows),
        "base_qualified_signals": sum(bool(x.get("base_ok")) for x in rows),
        "validation": validation,
    }

    (ROOT / "data/backtest.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    printable = {
        **out,
        "validation": {
            k: v
            for k, v in validation.items()
            if k not in ("selected_holdout_samples", "fold_report")
        },
    }
    print(json.dumps(printable, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
