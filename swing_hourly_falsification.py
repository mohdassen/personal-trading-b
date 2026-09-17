"""No-secret point-in-time falsification test for V7.2 SWING_CONTINUATION.

This is deliberately a *proxy*, not a certification test. Yahoo 60-minute history
is used to test whether the SWING hypothesis survives a recent point-in-time
replay without waiting for Alpaca API credentials in GitHub.

Rules kept from V7.2:
- exact SWING score components, regime weights and thresholds
- hourly + daily MTF confirmation
- 0.5% / 1.5% / 1.0% portfolio risk structure represented as 1 / 3 / 2 slots
- 0.85 pair-correlation cap and max 3 new signals/day
- neutral catalyst (historical headlines are not fabricated)
- next-bar execution, stop-first ambiguity, 1.8R target, 30bps baseline costs

Fidelity limitations:
- 60m instead of the live 15m scan/entry cadence
- session VWAP/ATR are reconstructed from 60m bars
- current universe has survivorship/selection bias
- this test can falsify; it cannot by itself authorize paper/live promotion
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

import v7_edge_research as old

NY = ZoneInfo("America/New_York")
OUT = Path("data/swing_hourly_proxy_research.json")
DAILY_START = "2023-01-01"
VALIDATION_END = "2024-12-31"
BASELINE_BPS = 30.0
STRESS_BPS = (30.0, 60.0, 100.0)
MAX_HOLD_HOURS = 33  # ~130 x 15m bars
MAX_OPEN = 3
MAX_GROUP = 2
MAX_SIGNALS_DAY = 3
MAX_CORR = 0.85
PRIMARY = old.PRIMARY_SYMBOLS
INDEPENDENT = old.INDEPENDENT_SYMBOLS


def _f(v, default=0.0):
    try:
        x = float(v)
        return default if not math.isfinite(x) else x
    except Exception:
        return default


def _norm(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
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
    if idx.tz is None:
        idx = idx.tz_localize("America/New_York")
    x.index = idx.tz_convert(NY)
    return x.sort_index()


def _download_daily(symbol: str) -> pd.DataFrame:
    try:
        x = yf.download(symbol, start=DAILY_START, interval="1d", auto_adjust=True,
                        progress=False, threads=False, timeout=30)
        x = _norm(x, symbol)
        if not x.empty:
            # Daily timestamps may be midnight local. Use session dates as index.
            x.index = pd.Index([ts.date() for ts in x.index], name="session")
        return x
    except Exception:
        return pd.DataFrame()


def _download_hourly(symbol: str) -> pd.DataFrame:
    try:
        x = yf.download(symbol, period="729d", interval="60m", auto_adjust=True,
                        prepost=False, progress=False, threads=False, timeout=30)
        x = _norm(x, symbol)
        if x.empty:
            return x
        mins = x.index.hour * 60 + x.index.minute
        return x[(mins >= 570) & (mins < 960) & (x.index.dayofweek < 5)].copy()
    except Exception:
        return pd.DataFrame()


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _rsi_state(close, n=14):
    d = close.diff(); gain = d.clip(lower=0); loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    al = loss.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    rsi = (100 - 100/(1 + ag/al.replace(0, np.nan))).fillna(50)
    return rsi, ag, al


def _atr(df, n=14):
    pc = df["Close"].shift(1)
    tr = pd.concat([df["High"]-df["Low"], (df["High"]-pc).abs(), (df["Low"]-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, min_periods=n, adjust=False).mean()


def prepare_daily(x: pd.DataFrame) -> pd.DataFrame:
    d = x.copy()
    d["EMA20"] = _ema(d.Close, 20); d["EMA50"] = _ema(d.Close, 50); d["EMA200"] = _ema(d.Close, 200)
    d["RSI14"], d["AG14"], d["AL14"] = _rsi_state(d.Close, 14)
    d["ATR14"] = _atr(d, 14)
    return d


def prepare_hourly(x: pd.DataFrame) -> pd.DataFrame:
    h = x.copy()
    h["EMA9"] = _ema(h.Close, 9); h["EMA21"] = _ema(h.Close, 21); h["EMA50"] = _ema(h.Close, 50)
    h["ATR14"] = _atr(h, 14)
    return h


def partial_daily(d: pd.DataFrame, session, o, hi, lo, c, vol):
    prior = d.loc[[idx < session for idx in d.index]]
    if len(prior) < 205:
        return None
    prev = prior.iloc[-1]
    prev_close = _f(prev.Close)
    e20 = (2/21)*c + (19/21)*_f(prev.EMA20)
    e50 = (2/51)*c + (49/51)*_f(prev.EMA50)
    e200 = (2/201)*c + (199/201)*_f(prev.EMA200)
    delta = c - prev_close; gain = max(delta, 0); loss = max(-delta, 0)
    ag = (1/14)*gain + (13/14)*_f(prev.AG14)
    al = (1/14)*loss + (13/14)*_f(prev.AL14)
    rsi = 100.0 if al <= 1e-12 and ag > 0 else 50.0 if al <= 1e-12 else 100 - 100/(1 + ag/al)
    tr = max(hi-lo, abs(hi-prev_close), abs(lo-prev_close))
    atrd = max((1/14)*tr + (13/14)*_f(prev.ATR14, c*0.025), c*0.005)
    p19 = prior.Volume.tail(19)
    ma20 = (float(p19.sum()) + vol)/20 if len(p19) == 19 else np.nan
    drvol = vol/ma20 if math.isfinite(ma20) and ma20 > 0 else 1.0
    close5 = _f(prior.Close.iloc[-5]); close20 = _f(prior.Close.iloc[-20])
    high20 = _f(prior.High.tail(20).max(), c)
    return {
        "prev_close": prev_close,
        "ema20": e20, "ema50": e50, "ema200": e200,
        "rsi_d": rsi, "atr_d": atrd, "daily_rvol": drvol,
        "ret5d": (c/close5-1)*100 if close5 else 0.0,
        "ret20d": (c/close20-1)*100 if close20 else 0.0,
        "distance_20d_high_pct": (c/high20-1)*100 if high20 else 0.0,
        "daily_trend": bool(c > e20 > e50 and e50 > e200),
        "daily_bear": bool(c < e20 < e50),
        "price_above_ema20": bool(c > e20),
    }


def swing_score(m):
    sc = 0
    sc += 25 if m["daily_trend"] else 8 if m["price_above_ema20"] else 0
    sc += 20 if m["ret20d"] >= 8 else 12 if m["ret20d"] >= 4 else 0
    sc += 15 if 0 < m["ret5d"] <= 8 else 6 if m["ret5d"] > 0 else 0
    sc += 15 if m["distance_20d_high_pct"] >= -3 else 7 if m["distance_20d_high_pct"] >= -6 else 0
    sc += 10 if 50 <= m["rsi_d"] <= 68 else 0
    sc += 10 if m["daily_rvol"] >= 1.2 else 4 if m["daily_rvol"] >= 1.0 else 0
    sc += 5 if m["day_change_pct"] >= 0 else 0
    return int(max(0, min(100, sc)))


def weight(regime):
    if regime == "RISK_ON": return 1.02
    if regime in ("RISK_OFF", "HIGH_VOLATILITY"): return 0.94
    return 1.05


def threshold(regime):
    return 79 if regime in ("RISK_OFF", "HIGH_VOLATILITY") else 74


def scan_features(daily: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    d = prepare_daily(daily); h = prepare_hourly(hourly)
    rows = []
    for session, day in h.groupby([ts.date() for ts in h.index], sort=True):
        so = shi = slo = sc = sv = None
        cum_pv = cum_v = 0.0
        for ts, bar in day.sort_index().iterrows():
            o, hi, lo, c, v = map(float, (bar.Open, bar.High, bar.Low, bar.Close, bar.Volume))
            if so is None:
                so, shi, slo, sc, sv = o, hi, lo, c, v
            else:
                shi, slo, sc, sv = max(shi, hi), min(slo, lo), c, sv + v
            dm = partial_daily(d, session, so, shi, slo, sc, sv)
            typical = (hi + lo + c)/3.0
            cum_pv += typical*v; cum_v += v
            if dm is None:
                continue
            vwap = cum_pv/cum_v if cum_v > 0 else c
            atrh = max(_f(bar.ATR14, c*0.015), c*0.002)
            hourly_positive = bool(c > _f(bar.EMA21) and _f(bar.EMA9) >= _f(bar.EMA21))
            hourly_bull = bool(c > _f(bar.EMA9) > _f(bar.EMA21) > _f(bar.EMA50))
            row = {
                "bar_start": ts,
                "signal_at": ts + pd.Timedelta(hours=1),
                "session": session,
                "price": c,
                **dm,
                "day_change_pct": (c/dm["prev_close"]-1)*100 if dm["prev_close"] else 0.0,
                "hourly_positive": hourly_positive,
                "hourly_bull": hourly_bull,
                "vwap": vwap,
                "vwap_extension_atr": (c-vwap)/max(atrh, 1e-9),
            }
            row["raw_score"] = swing_score(row)
            rows.append(row)
    return pd.DataFrame(rows).set_index("bar_start").sort_index() if rows else pd.DataFrame()


def previous_vix(vix_daily: pd.DataFrame, session) -> float:
    if vix_daily.empty: return 20.0
    prior = vix_daily.loc[[idx < session for idx in vix_daily.index]]
    return _f(prior.Close.iloc[-1], 20.0) if len(prior) else 20.0


def build_regime(spy: pd.DataFrame, qqq: pd.DataFrame, vix_daily: pd.DataFrame) -> pd.DataFrame:
    common = spy.index.intersection(qqq.index)
    rows = []
    for ts in common:
        s, q = spy.loc[ts], qqq.loc[ts]
        session = s.session
        vv = previous_vix(vix_daily, session)
        if bool(s.daily_trend) and bool(q.daily_trend) and vv < 25:
            label = "RISK_ON"
        elif bool(s.daily_bear) and bool(q.daily_bear):
            label = "RISK_OFF"
        elif vv >= 30:
            label = "HIGH_VOLATILITY"
        else:
            label = "MIXED"
        rows.append({"bar_start": ts, "regime": label, "vix_previous_close": vv})
    return pd.DataFrame(rows).set_index("bar_start") if rows else pd.DataFrame()


def make_candidates(symbol, f: pd.DataFrame, regime: pd.DataFrame):
    out = []
    for ts in f.index.intersection(regime.index):
        r = f.loc[ts]; reg = str(regime.loc[ts, "regime"])
        if not (bool(r.hourly_positive) and bool(r.daily_trend)):
            continue
        strong = bool(r.hourly_bull) and bool(r.daily_trend)
        routed = int(max(0, min(100, round(float(r.raw_score)*weight(reg)))))
        final = int(max(0, min(100, routed + (6 if strong else 2))))
        if final < threshold(reg):
            continue
        if float(r.vwap_extension_atr) > 2.5 or float(r.day_change_pct) > 15:
            continue
        risk = max(1.5*float(r.atr_d), float(r.price)*0.025)
        risk = min(risk, float(r.price)*0.08)
        out.append({
            "symbol": symbol, "group": old.group_for(symbol), "setup": "SWING_CONTINUATION",
            "regime": reg, "signal_at": pd.Timestamp(r.signal_at).isoformat(),
            "signal_date": r.session.isoformat(), "year": int(r.session.year),
            "score": final, "raw_score": int(r.raw_score), "risk": float(risk),
            "signal_price": float(r.price), "mtf": "PASS_STRONG" if strong else "PASS",
        })
    return out


def simulate(c, hourly: pd.DataFrame, bps=BASELINE_BPS):
    signal_at = pd.Timestamp(c["signal_at"])
    idx = hourly.index
    p = int(idx.searchsorted(signal_at, side="left"))
    if p >= len(hourly): return None
    entry = float(hourly.iloc[p].Open); risk = float(c["risk"])
    if entry <= 0 or risk <= 0: return None
    stop = entry-risk; target = entry+1.8*risk
    last = min(p+MAX_HOLD_HOURS-1, len(hourly)-1)
    exit_px = float(hourly.iloc[last].Close); exit_p = last; reason = "TIME"
    for j in range(p, last+1):
        b = hourly.iloc[j]; op, lo, hi = float(b.Open), float(b.Low), float(b.High)
        if op <= stop:
            exit_px, exit_p, reason = op, j, "STOP_GAP"; break
        if lo <= stop:
            exit_px, exit_p, reason = stop, j, "STOP"; break
        if op >= target or hi >= target:
            exit_px, exit_p, reason = target, j, "TARGET"; break
    gross = (exit_px-entry)/risk
    cost_r = entry*(bps/10000.0)/risk
    z = dict(c)
    z.update({
        "entry": entry, "entry_at": idx[p].isoformat(), "entry_date": idx[p].date().isoformat(),
        "exit": exit_px, "exit_at": idx[exit_p].isoformat(), "exit_date": idx[exit_p].date().isoformat(),
        "gross_r": gross, "baseline_cost_r": cost_r, "net_r": gross-cost_r, "outcome": reason,
    })
    return z


def rolling_corr(symbol, other, signal_date, daily):
    if symbol == other: return 1.0
    a, b = daily.get(symbol), daily.get(other)
    if a is None or b is None: return None
    d = datetime.fromisoformat(signal_date).date()
    ar = a.loc[[x < d for x in a.index], "Close"].pct_change()
    br = b.loc[[x < d for x in b.index], "Close"].pct_change()
    z = pd.concat([ar, br], axis=1, join="inner").dropna().tail(60)
    return float(z.iloc[:,0].corr(z.iloc[:,1])) if len(z) >= 30 else None


def select(rows, daily):
    ordered = sorted(rows, key=lambda x: (x["entry_at"], -x["score"], x["symbol"]))
    active, selected = [], []
    reject = defaultdict(int); day_count = defaultdict(int)
    for t in ordered:
        entry_at = pd.Timestamp(t["entry_at"])
        active = [a for a in active if pd.Timestamp(a["exit_at"]) >= entry_at]
        day = t["signal_date"]
        if day_count[day] >= MAX_SIGNALS_DAY: reject["daily_signal_cap"] += 1; continue
        if len(active) >= MAX_OPEN: reject["open_risk_cap"] += 1; continue
        if any(a["symbol"] == t["symbol"] for a in active): reject["duplicate_symbol"] += 1; continue
        if sum(a["group"] == t["group"] for a in active) >= MAX_GROUP: reject["group_risk_cap"] += 1; continue
        blocked = False
        for a in active:
            corr = rolling_corr(t["symbol"], a["symbol"], t["signal_date"], daily)
            if corr is not None and corr >= MAX_CORR:
                blocked = True; break
        if blocked: reject["correlation_cap"] += 1; continue
        selected.append(t); active.append(t); day_count[day] += 1
    return selected, dict(reject)


def stats(rows, bps=None):
    return old.stats(rows, bps=bps)


def split(rows):
    val = [r for r in rows if r["signal_date"] <= VALIDATION_END]
    hold = [r for r in rows if r["signal_date"] > VALIDATION_END]
    return val, hold


def run(symbols, daily_frames, hourly_frames, feature_frames, regime):
    simulated = []
    for sym in symbols:
        if sym not in feature_frames or sym not in hourly_frames: continue
        for c in make_candidates(sym, feature_frames[sym], regime):
            t = simulate(c, hourly_frames[sym])
            if t: simulated.append(t)
    return select(simulated, daily_frames)


def main():
    symbols = sorted(set(PRIMARY + INDEPENDENT + ["SPY", "QQQ", "^VIX"]))
    daily, hourly, features, errors = {}, {}, {}, {}
    for i, sym in enumerate(symbols, 1):
        d = _download_daily(sym)
        if sym == "^VIX":
            if d.empty: errors[sym] = "daily VIX unavailable"
            else: daily[sym] = d
            continue
        h = _download_hourly(sym)
        if d.empty or h.empty:
            errors[sym] = f"daily={len(d)} hourly={len(h)}"
            continue
        daily[sym] = d; hourly[sym] = h
        f = scan_features(d, h)
        if f.empty: errors[sym] = "no point-in-time features"
        else: features[sym] = f
        print(f"[{i}/{len(symbols)}] {sym}: daily={len(d)} hourly={len(h)} scans={len(f)}")

    if "SPY" not in features or "QQQ" not in features:
        raise RuntimeError(f"market proxy unavailable: {errors}")
    regime = build_regime(features["SPY"], features["QQQ"], daily.get("^VIX", pd.DataFrame()))
    primary_rows, primary_rejects = run(PRIMARY, daily, hourly, features, regime)
    indep_rows, independent_rejects = run(INDEPENDENT, daily, hourly, features, regime)
    val, hold = split(primary_rows); _, ihold = split(indep_rows)
    vm, hm, im = stats(val), stats(hold), stats(ihold)

    if vm["samples"] < 30 or hm["samples"] < 60 or im["samples"] < 30:
        verdict = "INSUFFICIENT_PROXY"
    elif (vm["expectancy_r"] > 0 and vm["profit_factor"] >= 1.10
          and hm["expectancy_r"] > 0 and hm["profit_factor"] >= 1.15 and hm["max_drawdown_r"] <= 12
          and im["expectancy_r"] >= 0):
        verdict = "SUPPORTIVE_PROXY"
    else:
        verdict = "FAIL_PROXY"

    stress = {str(int(b)): stats(hold, bps=b) for b in STRESS_BPS}
    by_regime = {r: stats([x for x in hold if x["regime"] == r]) for r in ("RISK_ON","MIXED","RISK_OFF","HIGH_VOLATILITY")}
    by_year = {str(y): stats([x for x in primary_rows if x["year"] == y]) for y in sorted({x["year"] for x in primary_rows})}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "SWING_CONTINUATION Recent 60m Point-in-Time Falsification",
        "mode": "RESEARCH_ONLY_PROXY",
        "verdict": verdict,
        "production_v7_changed": False,
        "live_execution_authorized": False,
        "methodology": {
            "provider": "Yahoo/yfinance",
            "hourly_history": "729d maximum recent intraday window",
            "daily_history_start": DAILY_START,
            "validation_end": VALIDATION_END,
            "entry": "next 60m bar open after completed 60m signal bar",
            "target": "1.8R", "max_hold_hours": MAX_HOLD_HOURS,
            "catalyst": "neutral 0; not fabricated",
            "cost_baseline_round_trip_bps": BASELINE_BPS,
            "limitations": [
                "60m execution proxy; live V7.2 scans/enters on 15m cadence",
                "60m session VWAP/ATR approximation",
                "recent-window validation is shorter than the desired multi-year full-fidelity test",
                "current symbol universe has survivorship/selection bias",
                "cannot authorize PAPER_CANDIDATE or live trading by itself",
            ],
        },
        "data_errors": errors,
        "primary": {
            "validation": vm, "holdout": hm,
            "holdout_bootstrap_95pct": old.bootstrap_ci(hold) if len(hold) >= 20 else None,
            "holdout_cost_stress_round_trip_bps": stress,
            "holdout_by_regime": by_regime, "by_year": by_year,
            "rejections": primary_rejects,
        },
        "independent_holdout": im,
        "independent_rejections": independent_rejects,
        "interpretation": {
            "SUPPORTIVE_PROXY": "Recent hourly evidence supports continuing to the Alpaca 5m full-fidelity test; it is not promotion evidence.",
            "FAIL_PROXY": "Recent point-in-time hourly evidence fails; this is additional evidence to retire/rework SWING rather than wait for more engineering.",
            "INSUFFICIENT_PROXY": "Not enough selected trades for a decision; proceed to the 5m full-fidelity dataset if available.",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "validation": vm, "holdout": hm, "independent": im, "stress": stress}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
