"""Independent edge-discovery tournament.

Research-only. No orders are placed.
Safeguards: point-in-time signals, next-open entry, stop-first ambiguity,
pre-declared rules, time holdout, independent-symbol holdout, cost stress,
bootstrap CI, and no optimizer.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

OUT = Path("data/edge_discovery_tournament.json")
START = "2016-01-01"
BASELINE_BPS = 30.0
STRESS_BPS = (15.0, 30.0, 60.0, 100.0)
SEED = 20260917

PRIMARY = [
    "AAPL","MSFT","NVDA","AMD","AMZN","GOOGL","META","TSLA","AVGO","ORCL",
    "CRM","ADBE","NFLX","MU","QCOM","AMAT","LRCX","KLAC","CRWD","PANW","NOW",
    "PLTR","XOM","CVX","COP","SLB","ABBV","JNJ","LLY","UNH","CAT","DE","GE",
    "WMT","COST","HD","DIS","ROKU","TMUS","GILD",
]
INDEPENDENT = [
    "INTC","SNOW","DDOG","NET","MDB","SHOP","UBER","ABNB","BKNG","TGT","LOW",
    "NKE","SBUX","MCD","ISRG","VRTX","REGN","PFE","BA","HON","RTX","CMCSA",
    "SPOT","RBLX","MELI",
]


@dataclass(frozen=True)
class Spec:
    name: str
    stop_atr: float
    target_r: float
    max_hold: int
    kind: str


# Fixed before results. No parameter search.
SPECS = [
    Spec("TREND_6M",       2.0, 2.5, 20, "trend"),
    Spec("BREAKOUT_55",    2.0, 3.0, 20, "breakout"),
    Spec("TREND_PULLBACK", 1.5, 2.0, 10, "pullback"),
    Spec("MEAN_REVERSION", 1.5, 1.5,  7, "mean_reversion"),
]


def norm(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy()
    if isinstance(x.columns, pd.MultiIndex):
        if symbol in x.columns.get_level_values(-1):
            x = x.xs(symbol, axis=1, level=-1)
        else:
            x.columns = x.columns.get_level_values(0)
    need = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in x.columns for c in need):
        return pd.DataFrame()
    x = x[need].dropna(subset=["Open", "High", "Low", "Close"]).copy()
    idx = pd.DatetimeIndex(x.index)
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    x.index = idx
    return x.sort_index()


def download(symbol: str) -> pd.DataFrame:
    try:
        x = yf.download(symbol, start=START, interval="1d", auto_adjust=True,
                        progress=False, threads=False, timeout=30)
        return norm(x, symbol)
    except Exception as exc:
        print("DOWNLOAD_FAIL", symbol, repr(exc))
        return pd.DataFrame()


def rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    au = up.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    ad = dn.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    rs = au / ad.replace(0, np.nan)
    return (100 - 100/(1 + rs)).fillna(50.0)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - pc).abs(),
        (df["Low"] - pc).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, min_periods=n, adjust=False).mean()


def features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    c = x["Close"]
    x["SMA5"] = c.rolling(5).mean()
    x["SMA20"] = c.rolling(20).mean()
    x["SMA50"] = c.rolling(50).mean()
    x["SMA200"] = c.rolling(200).mean()
    x["ATR14"] = atr(x)
    x["RSI2"] = rsi(c, 2)
    x["RSI14"] = rsi(c, 14)
    x["RET3"] = c.pct_change(3)
    x["RET20"] = c.pct_change(20)
    x["RET63"] = c.pct_change(63)
    x["RET126"] = c.pct_change(126)
    x["HIGH55_PRIOR"] = x["High"].shift(1).rolling(55).max()
    x["VOL20"] = x["Volume"].rolling(20).mean()
    x["RVOL"] = x["Volume"] / x["VOL20"].replace(0, np.nan)
    return x


def signal(spec: Spec, row: pd.Series) -> bool:
    needed = ["Close","SMA5","SMA20","SMA50","SMA200","ATR14","RSI2","RSI14",
              "RET3","RET20","RET63","RET126","HIGH55_PRIOR","RVOL"]
    if any(pd.isna(row.get(k, np.nan)) for k in needed):
        return False
    c = float(row.Close)
    if spec.kind == "trend":
        return (c > row.SMA200 and row.SMA50 > row.SMA200 and row.RET126 >= 0.15
                and row.RET63 > 0 and row.RET20 > 0 and row.RSI14 < 75)
    if spec.kind == "breakout":
        return (c > row.HIGH55_PRIOR and c > row.SMA200 and row.SMA50 > row.SMA200
                and row.RVOL >= 1.0)
    if spec.kind == "pullback":
        return (c > row.SMA200 and row.SMA50 > row.SMA200 and c < row.SMA20
                and c > row.SMA50 and row.RSI2 <= 15 and row.RET63 > 0)
    if spec.kind == "mean_reversion":
        return (c > row.SMA200 and row.SMA50 > row.SMA200 and row.RSI2 <= 5
                and row.RET3 <= -0.04)
    return False


def exit_signal(spec: Spec, row: pd.Series) -> bool:
    if spec.kind == "mean_reversion":
        return bool(pd.notna(row.SMA5) and row.Close > row.SMA5)
    if spec.kind == "pullback":
        return bool(pd.notna(row.SMA20) and row.Close > row.SMA20)
    return False


def simulate_symbol(symbol: str, universe: str, raw: pd.DataFrame, spec: Spec) -> list[dict]:
    x = features(raw)
    trades: list[dict] = []
    i = 210
    n = len(x)
    while i < n - 2:
        row = x.iloc[i]
        if not signal(spec, row):
            i += 1
            continue
        entry_i = i + 1
        entry = float(x.iloc[entry_i].Open)
        atr0 = float(row.ATR14)
        risk = max(spec.stop_atr * atr0, 0.015 * entry)
        if not math.isfinite(entry) or entry <= 0 or not math.isfinite(risk) or risk <= 0:
            i += 1
            continue
        stop = entry - risk
        target = entry + spec.target_r * risk
        exit_i = min(entry_i + spec.max_hold - 1, n - 1)
        exit_px = float(x.iloc[exit_i].Close)
        reason = "TIME"

        for j in range(entry_i, min(entry_i + spec.max_hold, n)):
            b = x.iloc[j]
            lo, hi = float(b.Low), float(b.High)
            if lo <= stop and hi >= target:
                exit_i, exit_px, reason = j, stop, "STOP_FIRST"
                break
            if lo <= stop:
                exit_i, exit_px, reason = j, stop, "STOP"
                break
            if hi >= target:
                exit_i, exit_px, reason = j, target, "TARGET"
                break
            # Close-based exit is known after bar close; fill next open.
            if exit_signal(spec, b) and j + 1 < n:
                exit_i = j + 1
                exit_px = float(x.iloc[exit_i].Open)
                reason = "RULE_EXIT"
                break

        gross_r = (exit_px - entry) / risk
        trades.append({
            "strategy": spec.name,
            "symbol": symbol,
            "universe": universe,
            "signal_date": str(x.index[i].date()),
            "entry_date": str(x.index[entry_i].date()),
            "exit_date": str(x.index[exit_i].date()),
            "entry": round(entry, 6),
            "exit": round(exit_px, 6),
            "risk_per_share": round(risk, 6),
            "gross_r": round(gross_r, 6),
            "reason": reason,
        })
        # No overlapping position in the same symbol/strategy.
        i = exit_i + 1
    return trades


def cost_adjusted_r(trade: dict, bps: float) -> float:
    # bps is a total round-trip notional charge.
    notional_cost = float(trade["entry"]) * (bps / 10000.0)
    return float(trade["gross_r"]) - notional_cost / float(trade["risk_per_share"])


def max_drawdown(rs: list[float]) -> float:
    eq = peak = dd = 0.0
    for value in rs:
        eq += value
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dd


def bootstrap_ci(rs: list[float]):
    if len(rs) < 20:
        return None, None
    a = np.array(rs, dtype=float)
    rng = np.random.default_rng(SEED + len(a))
    means = np.empty(2000, dtype=float)
    for i in range(len(means)):
        means[i] = rng.choice(a, size=len(a), replace=True).mean()
    lo, hi = np.quantile(means, [0.025, 0.975])
    return float(lo), float(hi)


def metrics(trades: list[dict], bps: float = BASELINE_BPS) -> dict:
    if not trades:
        return {"samples":0,"win_rate":None,"expectancy_r":None,"profit_factor":None,
                "total_r":0.0,"max_drawdown_r":0.0,"bootstrap95":[None,None]}
    ordered = sorted(trades, key=lambda t: (t["exit_date"], t["symbol"]))
    rs = [cost_adjusted_r(t, bps) for t in ordered]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    pf = sum(wins)/abs(sum(losses)) if losses else (999.0 if wins else 0.0)
    lo, hi = bootstrap_ci(rs)
    return {
        "samples": len(rs),
        "win_rate": round(100*len(wins)/len(rs), 1),
        "expectancy_r": round(float(np.mean(rs)), 4),
        "profit_factor": round(float(pf), 3),
        "total_r": round(float(sum(rs)), 2),
        "max_drawdown_r": round(float(max_drawdown(rs)), 2),
        "bootstrap95": [None if lo is None else round(lo,4), None if hi is None else round(hi,4)],
    }


def period_filter(trades: list[dict], start: str | None = None, end: str | None = None) -> list[dict]:
    out = []
    for t in trades:
        d = pd.Timestamp(t["signal_date"])
        if start and d < pd.Timestamp(start):
            continue
        if end and d > pd.Timestamp(end):
            continue
        out.append(t)
    return out


def evaluate(spec: Spec, trades: list[dict]) -> dict:
    primary = [t for t in trades if t["universe"] == "primary"]
    independent = [t for t in trades if t["universe"] == "independent"]
    train = period_filter(primary, end="2022-12-31")
    validation = period_filter(primary, start="2023-01-01", end="2024-12-31")
    holdout = period_filter(primary, start="2025-01-01")
    indep_holdout = period_filter(independent, start="2025-01-01")

    mval = metrics(validation)
    mhold = metrics(holdout)
    mind = metrics(indep_holdout)
    stress = {str(int(b)): metrics(holdout, b) for b in STRESS_BPS}

    # Strict pre-declared research-candidate gate. This is not live approval.
    passes = (
        mval["samples"] >= 50 and mhold["samples"] >= 50 and mind["samples"] >= 30
        and (mval["expectancy_r"] or -99) > 0.05 and (mval["profit_factor"] or 0) >= 1.15
        and (mhold["expectancy_r"] or -99) > 0.05 and (mhold["profit_factor"] or 0) >= 1.15
        and mhold["max_drawdown_r"] <= 15.0
        and (mind["expectancy_r"] or -99) > 0.0 and (mind["profit_factor"] or 0) >= 1.05
        and (stress["60"]["expectancy_r"] or -99) >= 0.0
    )
    return {
        "strategy": spec.name,
        "status": "RESEARCH_CANDIDATE" if passes else "REJECT",
        "train": metrics(train),
        "validation": mval,
        "holdout": mhold,
        "independent_holdout": mind,
        "holdout_cost_stress": stress,
    }


def main() -> None:
    all_symbols = [(s, "primary") for s in PRIMARY] + [(s, "independent") for s in INDEPENDENT]
    raw: dict[str, tuple[str, pd.DataFrame]] = {}
    for idx, (symbol, universe) in enumerate(all_symbols, 1):
        df = download(symbol)
        if len(df) >= 260:
            raw[symbol] = (universe, df)
        print(f"[{idx}/{len(all_symbols)}] {symbol}: rows={len(df)}")

    results = []
    counts = {}
    for spec in SPECS:
        trades = []
        for symbol, (universe, df) in raw.items():
            trades.extend(simulate_symbol(symbol, universe, df, spec))
        counts[spec.name] = len(trades)
        result = evaluate(spec, trades)
        results.append(result)
        print(json.dumps({
            "strategy": spec.name,
            "status": result["status"],
            "validation": result["validation"],
            "holdout": result["holdout"],
            "independent_holdout": result["independent_holdout"],
            "stress60": result["holdout_cost_stress"]["60"],
        }, indent=2))

    winners = [r["strategy"] for r in results if r["status"] == "RESEARCH_CANDIDATE"]
    payload = {
        "method": {
            "start": START,
            "train": "2016-01-01..2022-12-31",
            "validation": "2023-01-01..2024-12-31",
            "holdout": "2025-01-01..latest",
            "baseline_round_trip_bps": BASELINE_BPS,
            "stress_bps": list(STRESS_BPS),
            "optimizer": "NONE",
            "signal_timing": "close t -> next open t+1",
            "same_bar_ambiguity": "STOP_FIRST",
            "note": "Research candidates require full-fidelity and forward paper validation; no live authorization.",
        },
        "downloaded_symbols": len(raw),
        "trade_counts": counts,
        "results": results,
        "research_candidates": winners,
        "overall": "CANDIDATE_FOUND" if winners else "NO_EDGE_FOUND_IN_THIS_TOURNAMENT",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\nFINAL")
    print(json.dumps({
        "overall": payload["overall"],
        "research_candidates": winners,
        "downloaded_symbols": len(raw),
    }, indent=2))


if __name__ == "__main__":
    main()
