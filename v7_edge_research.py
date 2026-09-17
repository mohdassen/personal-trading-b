"""V7 historical edge falsification harness.

Research-only. The purpose is to decide whether the current V7.2 hypotheses deserve
continued shadow validation. It does not alter production rules or authorize live
trading.

Important fidelity limitation: free Yahoo history does not provide multi-year 15m
bars or point-in-time news/Sharia fundamentals. Therefore SESSION_LEADER,
MOMENTUM_LEADER and VWAP_PULLBACK are conservative DAILY proxies. BREAKOUT and
SWING_CONTINUATION are much closer to their daily components. Catalyst and MTF
bonuses are deliberately set to zero, not backfilled from future information.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

OUT = Path("data/v7_edge_research.json")
START = "2018-01-01"
TRAIN_END = "2022-12-31"
VALIDATION_END = "2024-12-31"
HOLDOUT_START = "2025-01-01"
BASELINE_ROUND_TRIP_BPS = 30.0
STRESS_BPS = (30.0, 60.0, 100.0)
HOLD_BARS = 10
MAX_NEW_SIGNALS_PER_DAY = 3
MAX_OPEN_POSITIONS = 3
MAX_GROUP_POSITIONS = 2
MAX_PAIR_CORRELATION = 0.85

PRIMARY_SYMBOLS = [
    "AAPL","MSFT","NVDA","AMD","AMZN","GOOGL","META","TSLA","AVGO","ORCL",
    "CRM","ADBE","NFLX","MU","QCOM","AMAT","LRCX","KLAC","CRWD","PANW","NOW",
    "PLTR","XOM","CVX","COP","SLB","ABBV","JNJ","LLY","UNH","CAT","DE","GE",
    "WMT","COST","HD","DIS","ROKU","TMUS","GILD",
]
INDEPENDENT_SYMBOLS = [
    "INTC","SNOW","DDOG","NET","MDB","SHOP","UBER","ABNB","BKNG","TGT","LOW",
    "NKE","SBUX","MCD","ISRG","VRTX","REGN","PFE","BA","HON","RTX","CMCSA",
    "SPOT","RBLX","MELI",
]

GROUPS = {
    "MEGA_TECH": {"AAPL","MSFT","AMZN","GOOGL","META","ORCL","ADBE","CRM"},
    "SEMICONDUCTORS": {"NVDA","AVGO","AMD","INTC","QCOM","MU","AMAT","LRCX","KLAC","ARM"},
    "CLOUD_SECURITY": {"PLTR","PANW","CRWD","NOW","SNOW","DDOG","NET","MDB","SHOP"},
    "TRAVEL_MOBILITY": {"UBER","ABNB","BKNG"},
    "CONSUMER_RETAIL": {"COST","WMT","TGT","HD","LOW","NKE","SBUX","MCD","TSLA"},
    "HEALTHCARE": {"LLY","UNH","JNJ","MRK","ABBV","PFE","ISRG","VRTX","REGN","GILD"},
    "ENERGY": {"XOM","CVX","COP","SLB"},
    "INDUSTRIALS": {"CAT","GE","BA","DE","HON","RTX"},
    "MEDIA_TELECOM": {"DIS","CMCSA","TMUS","T","VZ","SPOT","NFLX","RBLX","ROKU","MELI"},
}
STRATEGIES = ["SESSION_LEADER","MOMENTUM_LEADER","BREAKOUT","VWAP_PULLBACK","SWING_CONTINUATION"]
BASE_THRESHOLDS = {"SESSION_LEADER":78,"MOMENTUM_LEADER":78,"BREAKOUT":76,"VWAP_PULLBACK":74,"SWING_CONTINUATION":74}


def group_for(symbol: str) -> str:
    for group, symbols in GROUPS.items():
        if symbol in symbols:
            return group
    return "OTHER"


def norm(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        if symbol in df.columns.get_level_values(-1):
            df = df.xs(symbol, axis=1, level=-1)
        else:
            df.columns = df.columns.get_level_values(0)
    required = ["Open","High","Low","Close","Volume"]
    if not all(c in df.columns for c in required):
        return pd.DataFrame()
    return df[required].dropna(subset=["Open","High","Low","Close"]).copy()


def download(symbol: str) -> pd.DataFrame:
    try:
        df = yf.download(symbol, start=START, auto_adjust=True, progress=False,
                         threads=False, timeout=30)
        return norm(df, symbol)
    except Exception as exc:
        print("DOWNLOAD", symbol, exc)
        return pd.DataFrame()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff(); gain = d.clip(lower=0); loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    al = loss.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    return (100 - 100/(1 + ag/al.replace(0, np.nan))).fillna(50)


def atr(df, n=14):
    pc = df["Close"].shift(1)
    tr = pd.concat([df["High"]-df["Low"], (df["High"]-pc).abs(), (df["Low"]-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, min_periods=n, adjust=False).mean()


def features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    c, h, l, v = x["Close"], x["High"], x["Low"], x["Volume"]
    x["EMA9"] = ema(c, 9); x["EMA20"] = ema(c, 20); x["EMA21"] = ema(c, 21)
    x["EMA50"] = ema(c, 50); x["EMA200"] = ema(c, 200)
    x["RSI14"] = rsi(c); x["ATR14"] = atr(x)
    x["VOL_RATIO"] = v / v.rolling(20).mean().replace(0, np.nan)
    x["RET5"] = c.pct_change(5) * 100; x["RET20"] = c.pct_change(20) * 100
    x["HIGH20"] = h.shift(1).rolling(20).max()
    x["TYPICAL"] = (h + l + c) / 3.0
    x["DAY_CHANGE"] = c.pct_change() * 100
    x["CLOSE_LOC"] = (c - l) / (h - l).replace(0, np.nan)
    return x


def build_regime(spy: pd.DataFrame, qqq: pd.DataFrame, vix: pd.DataFrame) -> pd.DataFrame:
    s = features(spy); q = features(qqq)
    idx = s.index.union(q.index).sort_values()
    out = pd.DataFrame(index=idx)
    for p, frame in (("S", s), ("Q", q)):
        out[p+"C"] = frame["Close"].reindex(idx).ffill()
        out[p+"20"] = frame["EMA20"].reindex(idx).ffill()
        out[p+"50"] = frame["EMA50"].reindex(idx).ffill()
    out["VIX"] = vix["Close"].reindex(idx).ffill() if not vix.empty else 20.0
    labels = []
    for _, r in out.iterrows():
        sb = bool(r.SC > r.S20 > r.S50); qb = bool(r.QC > r.Q20 > r.Q50)
        sr = bool(r.SC < r.S20 < r.S50); qr = bool(r.QC < r.Q20 < r.Q50)
        vv = float(r.VIX) if pd.notna(r.VIX) else 20.0
        if sb and qb and vv < 25: labels.append("RISK_ON")
        elif sr and qr: labels.append("RISK_OFF")
        elif vv >= 30: labels.append("HIGH_VOLATILITY")
        else: labels.append("MIXED")
    out["REGIME"] = labels
    return out[["REGIME","VIX"]]


def router_weights(regime: str):
    if regime == "RISK_ON":
        return {"SESSION_LEADER":1.07,"MOMENTUM_LEADER":1.06,"BREAKOUT":1.05,"VWAP_PULLBACK":1.02,"SWING_CONTINUATION":1.02}
    if regime in ("RISK_OFF","HIGH_VOLATILITY"):
        return {"SESSION_LEADER":0.90,"MOMENTUM_LEADER":0.88,"BREAKOUT":0.88,"VWAP_PULLBACK":0.96,"SWING_CONTINUATION":0.94}
    return {"SESSION_LEADER":1.00,"MOMENTUM_LEADER":0.98,"BREAKOUT":0.97,"VWAP_PULLBACK":1.07,"SWING_CONTINUATION":1.05}


def threshold(setup: str, regime: str) -> int:
    t = BASE_THRESHOLDS[setup]
    return t + 5 if regime in ("RISK_OFF","HIGH_VOLATILITY") else t


def daily_proxy_metrics(row) -> dict:
    price = float(row.Close); atrd = max(float(row.ATR14), price*0.005)
    typical = float(row.TYPICAL)
    high20 = float(row.HIGH20)
    return {
        "price": price,
        "day_change_pct": float(row.DAY_CHANGE),
        "rvol": float(row.VOL_RATIO),
        "daily_rvol": float(row.VOL_RATIO),
        "rsi_i": float(row.RSI14),
        "rsi_d": float(row.RSI14),
        "ret1h": 0.0,
        "ret5d": float(row.RET5),
        "ret20d": float(row.RET20),
        "above_vwap": price >= typical,
        "vwap_extension_atr": (price - typical) / atrd,
        "ema_stack": float(row.EMA9) > float(row.EMA21) > float(row.EMA50),
        "ema_fast": float(row.EMA9) >= float(row.EMA21),
        "daily_trend": price > float(row.EMA20) > float(row.EMA50) and float(row.EMA50) > float(row.EMA200),
        "price_above_ema20": price > float(row.EMA20),
        "above_20d_high": price > high20,
        "distance_20d_high_pct": (price/high20 - 1)*100 if high20 else 0.0,
        "close_location": float(row.CLOSE_LOC) if pd.notna(row.CLOSE_LOC) else 0.5,
        "atr_d": atrd,
    }


def strategy_scores(m: dict) -> dict:
    scores = {}
    sc = 0
    sc += 20 if m["day_change_pct"] >= 2 else 10 if m["day_change_pct"] >= 1 else 0
    sc += 20 if m["rvol"] >= 2 else 12 if m["rvol"] >= 1.3 else 0
    sc += 15 if m["above_vwap"] else 0
    sc += 15 if m["ema_stack"] else 8 if m["ema_fast"] else 0
    sc += 10 if m["ret1h"] >= .5 else 5 if m["ret1h"] > 0 else 0
    sc += 10 if 52 <= m["rsi_i"] <= 72 else 4 if 47 <= m["rsi_i"] < 52 else 0
    sc += 10 if m["daily_trend"] else 0
    if m["vwap_extension_atr"] > 2.0: sc -= 15
    if m["day_change_pct"] > 12: sc -= 10
    scores["MOMENTUM_LEADER"] = max(0, min(100, sc))

    sc = 0
    sc += 22 if m["above_20d_high"] else 12 if m["distance_20d_high_pct"] >= -1 else 0
    sc += 20 if m["rvol"] >= 1.8 else 12 if m["rvol"] >= 1.2 else 0
    sc += 15 if m["daily_trend"] else 0
    sc += 10 if m["ret1h"] > 0 else 0
    sc += 10 if m["close_location"] >= .70 else 5 if m["close_location"] >= .55 else 0
    sc += 10 if m["day_change_pct"] >= 1 else 0
    sc += 10 if 50 <= m["rsi_i"] <= 72 else 0
    scores["BREAKOUT"] = max(0, min(100, sc))

    sc = 0
    sc += 22 if m["daily_trend"] else 0
    sc += 15 if .5 <= m["day_change_pct"] <= 6 else 5 if m["day_change_pct"] > 0 else 0
    sc += 20 if m["above_vwap"] and 0 <= m["vwap_extension_atr"] <= .55 else 8 if m["above_vwap"] else 0
    sc += 12 if m["ema_fast"] else 0
    sc += 12 if 45 <= m["rsi_i"] <= 65 else 0
    sc += 10 if m["rvol"] >= 1.1 else 0
    sc += 9 if -.5 <= m["ret1h"] <= 1.0 else 0
    scores["VWAP_PULLBACK"] = max(0, min(100, sc))

    sc = 0
    sc += 25 if m["daily_trend"] else 8 if m["price_above_ema20"] else 0
    sc += 20 if m["ret20d"] >= 8 else 12 if m["ret20d"] >= 4 else 0
    sc += 15 if 0 < m["ret5d"] <= 8 else 6 if m["ret5d"] > 0 else 0
    sc += 15 if m["distance_20d_high_pct"] >= -3 else 7 if m["distance_20d_high_pct"] >= -6 else 0
    sc += 10 if 50 <= m["rsi_d"] <= 68 else 0
    sc += 10 if m["daily_rvol"] >= 1.2 else 4 if m["daily_rvol"] >= 1.0 else 0
    sc += 5 if m["day_change_pct"] >= 0 else 0
    scores["SWING_CONTINUATION"] = max(0, min(100, sc))

    sc = 0
    change, rvol = m["day_change_pct"], m["rvol"]
    sc += 30 if 4 <= change <= 12 else 22 if 2 <= change < 4 else 12 if 1 <= change < 2 else 0
    sc += 25 if rvol >= 2.0 else 18 if rvol >= 1.4 else 10 if rvol >= 1.1 else 0
    sc += 15 if m["above_vwap"] else 0
    sc += 10 if m["close_location"] >= .65 else 5 if m["close_location"] >= .50 else 0
    sc += 10 if m["ret1h"] >= .50 else 6 if m["ret1h"] > 0 else 0
    sc += 5 if m["ema_fast"] else 0
    sc += 5 if 50 <= m["rsi_i"] <= 75 else 0
    if m["vwap_extension_atr"] > 2.5: sc -= 12
    if change > 15: sc -= 15
    scores["SESSION_LEADER"] = max(0, min(100, sc))
    return scores


def simulate_trade(symbol, setup, score, base_threshold, regime, frame, i, round_trip_bps=BASELINE_ROUND_TRIP_BPS):
    if i + 1 >= len(frame): return None
    entry_i = i + 1
    entry = float(frame["Open"].iloc[entry_i])
    signal = frame.iloc[i]
    atrd = max(float(signal.ATR14), entry*0.005)
    swing = setup == "SWING_CONTINUATION"
    risk = max((1.50*atrd if swing else 1.25*atrd), entry*(0.025 if swing else 0.012))
    risk = min(risk, entry*(0.08 if swing else 0.05))
    if entry <= 0 or risk <= 0: return None
    stop = entry - risk
    target = entry + (1.8 if swing else 1.6)*risk
    last = min(entry_i + HOLD_BARS - 1, len(frame)-1)
    exit_px = float(frame["Close"].iloc[last]); exit_i = last; reason = "TIME"
    for j in range(entry_i, last+1):
        bar = frame.iloc[j]; op = float(bar.Open); lo = float(bar.Low); hi = float(bar.High)
        if op <= stop:
            exit_px = op; exit_i = j; reason = "STOP_GAP"; break
        if lo <= stop:
            exit_px = stop; exit_i = j; reason = "STOP"; break
        if op >= target or hi >= target:
            exit_px = target; exit_i = j; reason = "TARGET"; break
    gross = (exit_px-entry)/risk
    cost_r = (entry*(round_trip_bps/10000.0))/risk
    return {
        "symbol":symbol,"group":group_for(symbol),"setup":setup,"regime":regime,
        "signal_date":str(pd.Timestamp(frame.index[i]).date()),
        "entry_date":str(pd.Timestamp(frame.index[entry_i]).date()),
        "exit_date":str(pd.Timestamp(frame.index[exit_i]).date()),
        "year":int(pd.Timestamp(frame.index[entry_i]).year),"score":int(score),
        "base_threshold":int(base_threshold),"entry":round(entry,4),"risk":round(risk,4),
        "gross_r":round(float(gross),5),"baseline_cost_r":round(float(cost_r),5),
        "net_r":round(float(gross-cost_r),5),"outcome":reason,
    }


def generate_candidates(symbol, frame, regime_df):
    x = features(frame).dropna().copy()
    routed, separate = [], []
    if len(x) < 230: return routed, separate
    for i in range(210, len(x)-HOLD_BARS-1):
        row = x.iloc[i]
        try:
            m = daily_proxy_metrics(row)
        except Exception:
            continue
        if not all(np.isfinite(float(m[k])) for k in ("price","day_change_pct","rvol","ret5d","ret20d","atr_d")):
            continue
        ts = x.index[i]
        if ts in regime_df.index:
            regime = str(regime_df.loc[ts, "REGIME"])
        else:
            prior = regime_df.loc[:ts]
            regime = str(prior.iloc[-1]["REGIME"]) if len(prior) else "MIXED"
        weights = router_weights(regime)
        raw = strategy_scores(m)
        adjusted = {k:max(0,min(100,round(v*weights[k]))) for k,v in raw.items()}
        extended = m["vwap_extension_atr"] > 2.5 or m["day_change_pct"] > 15
        if extended: continue
        setup = sorted(adjusted.items(), key=lambda kv:(kv[1],raw[kv[0]]), reverse=True)[0][0]
        score = adjusted[setup]; th = threshold(setup, regime)
        if score >= th - 4:
            t = simulate_trade(symbol, setup, score, th, regime, x, i)
            if t: routed.append(t)
        for s in STRATEGIES:
            sc = adjusted[s]; sth = threshold(s, regime)
            if sc >= sth - 4:
                t = simulate_trade(symbol, s, sc, sth, regime, x, i)
                if t: separate.append(t)
    return routed, separate


def rolling_corr(symbol, other, signal_date, frames):
    if symbol == other: return 1.0
    a, b = frames.get(symbol), frames.get(other)
    if a is None or b is None or a.empty or b.empty: return None
    end = pd.Timestamp(signal_date)
    ar = a.loc[:end, "Close"].pct_change(); br = b.loc[:end, "Close"].pct_change()
    z = pd.concat([ar, br], axis=1, join="inner").dropna().tail(60)
    if len(z) < 30: return None
    return float(z.iloc[:,0].corr(z.iloc[:,1]))


def portfolio_select(candidates, frames, threshold_offset=0):
    eligible = [t for t in candidates if t["score"] >= t["base_threshold"] + threshold_offset]
    by_day = defaultdict(list)
    for t in eligible: by_day[t["signal_date"]].append(t)
    active, selected, rejected = [], [], defaultdict(int)
    for day in sorted(by_day):
        entry_day = min(t["entry_date"] for t in by_day[day])
        active = [a for a in active if a["exit_date"] >= entry_day]
        todays = sorted(by_day[day], key=lambda t:(t["score"], t["setup"]=="SWING_CONTINUATION"), reverse=True)
        new_count = 0
        for t in todays:
            if new_count >= MAX_NEW_SIGNALS_PER_DAY: rejected["daily_signal_cap"] += 1; continue
            if len(active) >= MAX_OPEN_POSITIONS: rejected["open_risk_cap"] += 1; continue
            if any(a["symbol"] == t["symbol"] for a in active): rejected["duplicate_symbol"] += 1; continue
            same_group = sum(1 for a in active if a["group"] == t["group"])
            if same_group >= MAX_GROUP_POSITIONS: rejected["group_risk_cap"] += 1; continue
            corr_block = False
            for a in active:
                c = rolling_corr(t["symbol"], a["symbol"], t["signal_date"], frames)
                if c is not None and c >= MAX_PAIR_CORRELATION:
                    corr_block = True; break
            if corr_block: rejected["correlation_cap"] += 1; continue
            selected.append(t); active.append(t); new_count += 1
    return selected, dict(rejected)


def losing_streak(rs):
    best = cur = 0
    for r in rs:
        if r <= 0: cur += 1; best = max(best, cur)
        else: cur = 0
    return best


def stats(rows, bps=None):
    if not rows:
        return {"samples":0,"win_rate":0,"avg_winner_r":0,"avg_loser_r":0,"expectancy_r":0,"profit_factor":0,"total_r":0,"max_drawdown_r":0,"losing_streak":0}
    vals = []
    for t in sorted(rows, key=lambda r:(r["entry_date"],r["symbol"])):
        if bps is None:
            vals.append(float(t["net_r"]))
        else:
            cost_r = float(t["entry"])*(float(bps)/10000.0)/float(t["risk"])
            vals.append(float(t["gross_r"])-cost_r)
    rs = np.asarray(vals, dtype=float); wins = rs[rs>0]; losses = rs[rs<=0]
    eq = np.cumsum(rs); peaks = np.maximum.accumulate(np.r_[0,eq])[1:]; dd = peaks-eq
    pf = wins.sum()/abs(losses.sum()) if len(losses) and abs(losses.sum())>1e-12 else (99.0 if len(wins) else 0.0)
    return {
        "samples":int(len(rs)),"win_rate":round(float((rs>0).mean()*100),1),
        "avg_winner_r":round(float(wins.mean()) if len(wins) else 0,3),
        "avg_loser_r":round(float(losses.mean()) if len(losses) else 0,3),
        "expectancy_r":round(float(rs.mean()),3),"profit_factor":round(float(pf),2),
        "total_r":round(float(rs.sum()),2),"max_drawdown_r":round(float(dd.max()) if len(dd) else 0,2),
        "losing_streak":int(losing_streak(rs)),
    }


def split_name(date_str):
    d = date_str[:10]
    if d <= TRAIN_END: return "train"
    if d <= VALIDATION_END: return "validation"
    return "holdout"


def split_stats(rows):
    return {k:stats([r for r in rows if split_name(r["signal_date"])==k]) for k in ("train","validation","holdout")}


def bootstrap_ci(rows, n=2000):
    rs = np.asarray([float(r["net_r"]) for r in rows], dtype=float)
    if len(rs) < 20: return None
    rng = np.random.default_rng(7421)
    means = np.empty(n)
    for i in range(n): means[i] = rng.choice(rs, size=len(rs), replace=True).mean()
    lo, hi = np.quantile(means, [0.025,0.975])
    return [round(float(lo),3), round(float(hi),3)]


def classify_verdict(validation, holdout, independent, proxy_cap=True):
    shadow = (
        holdout["samples"] >= 100 and holdout["expectancy_r"] >= 0.10 and holdout["profit_factor"] >= 1.30
        and holdout["max_drawdown_r"] <= 8 and validation["expectancy_r"] > 0 and validation["profit_factor"] >= 1.20
        and independent["samples"] >= 50 and independent["expectancy_r"] > 0 and independent["profit_factor"] >= 1.15
    )
    promising = (
        holdout["samples"] >= 60 and holdout["expectancy_r"] > 0 and holdout["profit_factor"] >= 1.15
        and holdout["max_drawdown_r"] <= 12 and validation["expectancy_r"] > 0
        and independent["samples"] >= 30 and independent["expectancy_r"] >= 0
    )
    if shadow and not proxy_cap: return "SHADOW_READY"
    if shadow or promising: return "PROMISING"
    return "FAIL"


def main():
    symbols = sorted(set(PRIMARY_SYMBOLS + INDEPENDENT_SYMBOLS + ["SPY","QQQ","^VIX"]))
    frames, errors = {}, {}
    for sym in symbols:
        df = download(sym)
        if len(df) < 260:
            errors[sym] = f"insufficient rows: {len(df)}"
        else:
            frames[sym] = df
    if not all(s in frames for s in ("SPY","QQQ","^VIX")):
        raise RuntimeError("missing market-regime data")
    regime_df = build_regime(frames["SPY"], frames["QQQ"], frames["^VIX"])

    primary_routed, primary_sep = [], []
    independent_routed, independent_sep = [], []
    for sym in PRIMARY_SYMBOLS:
        if sym not in frames: continue
        r, s = generate_candidates(sym, frames[sym], regime_df); primary_routed += r; primary_sep += s
    for sym in INDEPENDENT_SYMBOLS:
        if sym not in frames: continue
        r, s = generate_candidates(sym, frames[sym], regime_df); independent_routed += r; independent_sep += s

    baseline, rejects = portfolio_select(primary_routed, frames, threshold_offset=0)
    independent_baseline, independent_rejects = portfolio_select(independent_routed, frames, threshold_offset=0)
    split = split_stats(baseline)
    independent_hold = [r for r in independent_baseline if split_name(r["signal_date"])=="holdout"]
    independent_metrics = stats(independent_hold)

    hold = [r for r in baseline if split_name(r["signal_date"])=="holdout"]
    val = [r for r in baseline if split_name(r["signal_date"])=="validation"]
    holdout_by_strategy = {s:stats([r for r in hold if r["setup"]==s]) for s in STRATEGIES}
    separate_holdout = {s:stats([r for r in primary_sep if r["setup"]==s and split_name(r["signal_date"])=="holdout" and r["score"]>=r["base_threshold"]]) for s in STRATEGIES}
    by_regime = {r:stats([t for t in hold if t["regime"]==r]) for r in ("RISK_ON","MIXED","RISK_OFF","HIGH_VOLATILITY")}
    years = sorted({int(r["year"]) for r in baseline})
    by_year = {str(y):stats([r for r in baseline if int(r["year"])==y]) for y in years}

    walk_forward = []
    for y in years:
        test = [r for r in baseline if int(r["year"])==y]
        if y < 2019 or not test: continue
        walk_forward.append({"test_year":y,"metrics":stats(test)})

    sensitivity = {}
    for off in (-4,0,4):
        selected, _ = portfolio_select(primary_routed, frames, threshold_offset=off)
        pre_hold = [r for r in selected if split_name(r["signal_date"]) in ("train","validation")]
        sensitivity[str(off)] = stats(pre_hold)

    stress = {str(int(b)):stats(hold, bps=b) for b in STRESS_BPS}
    ci = bootstrap_ci(hold)
    verdict = classify_verdict(split["validation"], split["holdout"], independent_metrics, proxy_cap=True)

    report = {
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "engine":"V7.2 Historical Edge Falsification Harness",
        "mode":"RESEARCH_ONLY",
        "production_v7_changed":False,
        "live_execution_authorized":False,
        "verdict":verdict,
        "methodology":{
            "start":START,"fixed_split":{"train_end":TRAIN_END,"validation_end":VALIDATION_END,"holdout_start":HOLDOUT_START},
            "baseline_round_trip_bps":BASELINE_ROUND_TRIP_BPS,"hold_bars":HOLD_BARS,
            "portfolio_limits":{"risk_per_trade_pct":0.5,"max_open_risk_pct":1.5,"max_group_risk_pct":1.0,"max_pair_correlation":MAX_PAIR_CORRELATION,"max_signals_per_day":MAX_NEW_SIGNALS_PER_DAY},
            "parameter_tuning":"NONE. Production thresholds/routing are preserved. +/-4 sensitivity is reported only on train+validation and is not used to change holdout parameters.",
            "execution":"signal on completed daily bar, entry next session open, stop checked before target on ambiguous bars, gap-through-stop filled at open, costs expressed from bps into R.",
            "fidelity":"Daily proxy. No historical catalyst bonus, no positive MTF bonus, no point-in-time Sharia fundamentals. Intraday-only families are not claimed as fill-perfect replays.",
            "holdout_note":"The time holdout is locked for this harness but is not globally pristine because earlier project backtests already inspected overlapping dates. Independent symbols provide an additional cross-sectional holdout.",
        },
        "data_quality":{"primary_symbols_requested":len(PRIMARY_SYMBOLS),"independent_symbols_requested":len(INDEPENDENT_SYMBOLS),"download_errors":errors,"survivorship_bias_warning":True},
        "combined_portfolio":{"split":split,"holdout_expectancy_bootstrap_95pct":ci,"holdout_by_strategy":holdout_by_strategy,"holdout_by_regime":by_regime,"by_year":by_year,"walk_forward":walk_forward,"rejections":rejects},
        "strategy_diagnostics_unconstrained":{"holdout":separate_holdout},
        "independent_symbol_holdout":{"symbols":INDEPENDENT_SYMBOLS,"holdout":independent_metrics,"rejections":independent_rejects},
        "parameter_sensitivity_train_validation_only":{"threshold_offset_points":sensitivity},
        "holdout_cost_stress_round_trip_bps":stress,
        "decision_rules":{"SHADOW_READY":">=100 holdout trades, >=0.10R expectancy, PF>=1.30, DD<=8R, validation PF>=1.20, positive independent holdout; exact/full-fidelity replay required.","PROMISING":"positive validation + holdout, holdout PF>=1.15 and DD<=12R, non-negative independent holdout.","FAIL":"otherwise."},
        "interpretation":"A PROMISING result here can justify continued shadow work; because intraday/catalyst components are proxies, this harness alone cannot certify V7.2 as SHADOW_READY. A FAIL result is sufficient evidence not to spend another month merely waiting for forward trades under the unchanged hypothesis set.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
