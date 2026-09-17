from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

OUT = Path("data/published_edge_round2.json")
START = "2014-01-01"
BASELINE_BPS = 30.0
STRESS_BPS = 60.0

SYMBOLS = [
    "AAPL","MSFT","NVDA","AMD","AMZN","GOOGL","META","TSLA","AVGO","ORCL",
    "CRM","ADBE","NFLX","MU","QCOM","AMAT","LRCX","KLAC","CRWD","PANW","NOW",
    "PLTR","XOM","CVX","COP","SLB","ABBV","JNJ","LLY","UNH","CAT","DE","GE",
    "WMT","COST","HD","DIS","ROKU","TMUS","GILD","INTC","SNOW","DDOG","NET",
    "MDB","SHOP","UBER","ABNB","BKNG","TGT","LOW","NKE","SBUX","MCD","ISRG",
    "VRTX","REGN","PFE","BA","HON","RTX","CMCSA","SPOT","RBLX","MELI"
]
STRATEGIES = ["LOW_VOL_1Y", "LOW_VOL_3Y_WEEKLY", "RESIDUAL_MOMENTUM_MKT", "SHORT_REVERSAL_1M"]


def norm_one(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            if symbol in raw.columns.get_level_values(0):
                x = raw[symbol].copy()
            elif symbol in raw.columns.get_level_values(-1):
                x = raw.xs(symbol, axis=1, level=-1).copy()
            else:
                return pd.DataFrame()
        else:
            x = raw.copy()
        need = ["Open","High","Low","Close","Volume"]
        if not all(c in x.columns for c in need):
            return pd.DataFrame()
        x = x[need].dropna(subset=["Open","Close"]).copy()
        idx = pd.DatetimeIndex(x.index)
        if idx.tz is not None:
            idx = idx.tz_convert(None)
        x.index = idx
        return x.sort_index()
    except Exception:
        return pd.DataFrame()


def download_all() -> dict[str, pd.DataFrame]:
    tickers = SYMBOLS + ["SPY"]
    raw = yf.download(
        tickers, start=START, interval="1d", auto_adjust=True,
        progress=False, threads=True, group_by="ticker", timeout=30,
    )
    out = {}
    for s in tickers:
        x = norm_one(raw, s)
        if len(x) >= 500:
            out[s] = x
        print(f"{s}: rows={len(x)}")
    return out


def monthly_table(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["month"] = x.index.to_period("M")
    grouped = list(x.groupby("month", sort=True))
    rows = []
    for i,(m,g) in enumerate(grouped):
        g = g.sort_index()
        row = {
            "month": m,
            "close": float(g.Close.iloc[-1]),
            "open_first": float(g.Open.iloc[0]),
            "next_open": np.nan,
            "dollar_vol20": float((g.Close * g.Volume).tail(20).mean()),
        }
        if i + 1 < len(grouped):
            ng = grouped[i+1][1].sort_index()
            if not ng.empty:
                row["next_open"] = float(ng.Open.iloc[0])
        rows.append(row)
    m = pd.DataFrame(rows).set_index("month").sort_index()
    m["ret1m"] = m["close"].pct_change()
    m["mom12_1"] = m["close"].shift(1) / m["close"].shift(12) - 1.0
    return m


def monthly_realized_vol(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    r = d.Close.pct_change()
    one_y = r.rolling(252, min_periods=200).std() * math.sqrt(252)
    weekly = d.Close.resample("W-FRI").last().pct_change()
    three_y = weekly.rolling(156, min_periods=120).std() * math.sqrt(52)
    out = {}
    for m,g in d.groupby(d.index.to_period("M")):
        last = g.index[-1]
        v1 = one_y.loc[:last].dropna()
        w3 = three_y.loc[:last].dropna()
        out[m] = {
            "vol1y": float(v1.iloc[-1]) if not v1.empty else np.nan,
            "vol3y": float(w3.iloc[-1]) if not w3.empty else np.nan,
        }
    return pd.DataFrame.from_dict(out, orient="index").sort_index()


def rolling_market_residual_scores(stock_m: pd.DataFrame, spy_m: pd.DataFrame) -> pd.DataFrame:
    idx = stock_m.index.intersection(spy_m.index)
    sret = stock_m.loc[idx, "ret1m"].astype(float)
    mret = spy_m.loc[idx, "ret1m"].astype(float)
    residual = pd.Series(index=idx, dtype=float)

    # Each monthly residual is estimated using only the previous 36 completed months.
    for i in range(36, len(idx)):
        hist = pd.DataFrame({"y": sret.iloc[i-36:i], "x": mret.iloc[i-36:i]}).dropna()
        if len(hist) < 24:
            continue
        X = np.column_stack([np.ones(len(hist)), hist.x.values])
        beta, *_ = np.linalg.lstsq(X, hist.y.values, rcond=None)
        if pd.isna(sret.iloc[i]) or pd.isna(mret.iloc[i]):
            continue
        residual.iloc[i] = float(sret.iloc[i] - (beta[0] + beta[1]*mret.iloc[i]))

    score = pd.Series(index=idx, dtype=float)
    reversal = pd.Series(index=idx, dtype=float)
    for i in range(13, len(idx)):
        # 12 months ending one month before signal month (skip most recent month).
        formation = residual.iloc[i-12:i-1].dropna()
        if len(formation) >= 9 and formation.std(ddof=1) > 1e-12:
            score.iloc[i] = float(formation.sum() / formation.std(ddof=1))
        hist = residual.iloc[max(0,i-12):i].dropna()
        if len(hist) >= 9 and hist.std(ddof=1) > 1e-12 and pd.notna(residual.iloc[i-1]):
            reversal.iloc[i] = float(residual.iloc[i-1] / hist.std(ddof=1))
    return pd.DataFrame({"resmom": score, "resrev": reversal})


def build_features(raw: dict[str,pd.DataFrame]) -> tuple[dict[str,pd.DataFrame], pd.DataFrame]:
    spy_m = monthly_table(raw["SPY"])
    tables = {}
    rows = []
    for s in SYMBOLS:
        if s not in raw:
            continue
        mt = monthly_table(raw[s])
        vol = monthly_realized_vol(raw[s])
        res = rolling_market_residual_scores(mt, spy_m)
        mt = mt.join(vol, how="left").join(res, how="left")
        tables[s] = mt
        for month,r in mt.iterrows():
            rows.append({
                "symbol": s, "month": month, "close": r.close,
                "next_open": r.next_open, "dollar_vol20": r.dollar_vol20,
                "ret1m": r.ret1m, "mom12_1": r.mom12_1,
                "vol1y": r.vol1y, "vol3y": r.vol3y,
                "resmom": r.resmom, "resrev": r.resrev,
            })
    return tables, pd.DataFrame(rows)


def select(strategy: str, g: pd.DataFrame) -> list[str]:
    g = g.dropna(subset=["next_open"]).copy()
    if len(g) < 10:
        return []
    if strategy == "LOW_VOL_1Y":
        q = g.dropna(subset=["vol1y"])
        n = max(3, math.ceil(len(q)*0.20))
        return q.nsmallest(n, "vol1y").symbol.tolist()
    if strategy == "LOW_VOL_3Y_WEEKLY":
        q = g.dropna(subset=["vol3y"])
        n = max(3, math.ceil(len(q)*0.20))
        return q.nsmallest(n, "vol3y").symbol.tolist()
    if strategy == "RESIDUAL_MOMENTUM_MKT":
        q = g.dropna(subset=["resmom"])
        n = max(3, math.ceil(len(q)*0.20))
        return q.nlargest(n, "resmom").symbol.tolist()
    if strategy == "SHORT_REVERSAL_1M":
        # Approximate the published large/liquid-universe implementation:
        # take the 20 most liquid names point-in-time, then buy the 10 worst prior-month performers.
        q = g.dropna(subset=["ret1m","dollar_vol20"]).nlargest(min(20, len(g)), "dollar_vol20")
        return q.nsmallest(min(10, len(q)), "ret1m").symbol.tolist()
    return []


def next_month_return(mt: pd.DataFrame, month: pd.Period, cost_bps: float) -> float | None:
    if month not in mt.index:
        return None
    i = mt.index.get_loc(month)
    if isinstance(i, slice) or i + 1 >= len(mt):
        return None
    entry = float(mt.iloc[i].next_open)
    if not math.isfinite(entry) or entry <= 0:
        return None
    exit_close = float(mt.iloc[i+1].close)
    return exit_close / entry - 1.0 - cost_bps/10000.0


def portfolio(tables: dict[str,pd.DataFrame], panel: pd.DataFrame, strategy: str,
              symbols: set[str], cost_bps: float) -> pd.Series:
    returns = {}
    q = panel[panel.symbol.isin(symbols)].copy()
    for month,g in q.groupby("month", sort=True):
        picked = select(strategy, g)
        rs = []
        for s in picked:
            r = next_month_return(tables[s], month, cost_bps)
            if r is not None:
                rs.append(r)
        if rs:
            returns[month.to_timestamp(how="end")] = float(np.mean(rs))
    return pd.Series(returns, dtype=float).sort_index()


def benchmark(tables: dict[str,pd.DataFrame], panel: pd.DataFrame,
              symbols: set[str], cost_bps: float) -> pd.Series:
    returns = {}
    q = panel[panel.symbol.isin(symbols)].copy()
    for month,g in q.groupby("month", sort=True):
        rs = []
        for s in g.symbol.tolist():
            r = next_month_return(tables[s], month, cost_bps)
            if r is not None:
                rs.append(r)
        if rs:
            returns[month.to_timestamp(how="end")] = float(np.mean(rs))
    return pd.Series(returns, dtype=float).sort_index()


def relative_metrics(strategy: pd.Series, bench: pd.Series, start: str, end: str | None=None) -> dict:
    x = pd.concat([strategy.rename("s"), bench.rename("b")], axis=1).dropna()
    x = x[x.index >= pd.Timestamp(start)]
    if end:
        x = x[x.index <= pd.Timestamp(end)]
    if x.empty:
        return {"months":0,"relative_wealth":None,"excess_sharpe":None,"mean_excess_monthly":None}
    ex = x.s - x.b
    rel = float(((1+x.s)/(1+x.b)).prod() - 1.0)
    sd = float(ex.std(ddof=1))
    sh = float(ex.mean()/sd*math.sqrt(12)) if sd > 1e-12 else None
    return {
        "months": int(len(x)),
        "relative_wealth": round(rel,4),
        "excess_sharpe": None if sh is None else round(sh,3),
        "mean_excess_monthly": round(float(ex.mean()),5),
    }


def make_folds(symbols: list[str], k: int=5) -> list[set[str]]:
    ordered = sorted(symbols)
    return [set(ordered[i::k]) for i in range(k)]


def evaluate_strategy(strategy: str, tables: dict[str,pd.DataFrame], panel: pd.DataFrame,
                      available: list[str]) -> dict:
    cells = []
    stress_latest = []
    folds = make_folds(available, 5)
    periods = [("2016_2020","2016-01-01","2020-12-31"),
               ("2021_2023","2021-01-01","2023-12-31"),
               ("2024_PLUS","2024-01-01",None)]
    for fi,syms in enumerate(folds):
        s30 = portfolio(tables, panel, strategy, syms, BASELINE_BPS)
        b30 = benchmark(tables, panel, syms, BASELINE_BPS)
        s60 = portfolio(tables, panel, strategy, syms, STRESS_BPS)
        b60 = benchmark(tables, panel, syms, STRESS_BPS)
        for name,start,end in periods:
            m = relative_metrics(s30,b30,start,end)
            cells.append({"fold":fi,"period":name,**m})
        stress_latest.append({"fold":fi,**relative_metrics(s60,b60,"2024-01-01",None)})

    positive_cells = sum(1 for c in cells if (c["relative_wealth"] or -99) > 0)
    latest = [c for c in cells if c["period"]=="2024_PLUS"]
    pos_latest = sum(1 for c in latest if (c["relative_wealth"] or -99) > 0)
    pos_stress = sum(1 for c in stress_latest if (c["relative_wealth"] or -99) > 0)
    med = {}
    for p,_,_ in periods:
        vals = [c["excess_sharpe"] for c in cells if c["period"]==p and c["excess_sharpe"] is not None]
        med[p] = None if not vals else round(float(np.median(vals)),3)

    # Locked before results: same spirit as Stage 3, with no post-result relaxation.
    passed = (
        positive_cells >= 11
        and pos_latest >= 4
        and pos_stress >= 4
        and all(med[p] is not None and med[p] > 0 for p,_,_ in periods)
        and med["2024_PLUS"] >= 0.25
    )
    return {
        "strategy": strategy,
        "status": "ROUND2_ROBUST_CANDIDATE" if passed else "REJECT",
        "positive_cells": positive_cells,
        "positive_2024_folds": pos_latest,
        "positive_stress_2024_folds": pos_stress,
        "median_excess_sharpe_by_period": med,
        "cells": cells,
        "stress_2024_plus": stress_latest,
    }


def main():
    raw = download_all()
    if "SPY" not in raw:
        raise RuntimeError("SPY missing")
    available = [s for s in SYMBOLS if s in raw]
    tables,panel = build_features(raw)
    results = [evaluate_strategy(s,tables,panel,available) for s in STRATEGIES]
    candidates = [r["strategy"] for r in results if r["status"]=="ROUND2_ROBUST_CANDIDATE"]
    out = {
        "method": "predeclared published-edge round 2; monthly next-open fills; cross-fold benchmark-relative tests",
        "baseline_roundtrip_bps": BASELINE_BPS,
        "stress_roundtrip_bps": STRESS_BPS,
        "downloaded_symbols": len(available),
        "strategies": STRATEGIES,
        "results": results,
        "candidates": candidates,
        "overall": "ROUND2_CANDIDATE_FOUND" if candidates else "NO_ROUND2_CANDIDATE",
        "limitations": [
            "current-survivor universe remains selection/survivorship biased",
            "Yahoo is not institutional point-in-time constituent data",
            "residual momentum uses market-only residuals rather than exact Fama-French three-factor residuals",
            "a candidate authorizes only higher-fidelity validation, never paper/live trading"
        ]
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({
        "overall": out["overall"],
        "candidates": candidates,
        "summary": [{
            "strategy": r["strategy"],
            "status": r["status"],
            "positive_cells": r["positive_cells"],
            "positive_2024_folds": r["positive_2024_folds"],
            "positive_stress_2024_folds": r["positive_stress_2024_folds"],
            "median_excess_sharpe_by_period": r["median_excess_sharpe_by_period"],
        } for r in results]
    }, indent=2))


if __name__ == "__main__":
    main()
