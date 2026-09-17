"""Focused point-in-time research for V7.2 SWING_CONTINUATION.

Purpose: answer one question only: does SWING_CONTINUATION retain a tradeable edge
when the historical replay is much closer to the live V7.2 timing semantics?

Key design choices
------------------
* Alpaca 5-minute history is used to reconstruct the V7.2 scan cadence
  (09:35, 09:50, ... 15:50, 16:05 America/New_York).
* Daily and 60-minute indicators use only information available at each scan.
* The current 15-minute bar is reconstructed as a partial bar at scan time, which
  matters because V7.2 scans five minutes into most 15-minute candles.
* Catalyst score is neutral (0); historical news is not fabricated.
* VIX uses the PREVIOUS completed session close. This is point-in-time safe but
  slightly lagged versus the live Yahoo daily VIX observation.
* Execution follows current paper semantics: signal now -> next 15-minute bar open,
  fixed signal risk, stop-first on ambiguous bars, max 130 15-minute bars.
* Production V7.2 is not modified and live execution remains forbidden.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from alpaca_integration import AlpacaMarketData
import v7_edge_research as old


NY = ZoneInfo("America/New_York")
OUT = Path("data/swing_full_fidelity_research.json")
START = "2019-01-01"
TRAIN_END = "2022-12-31"
VALIDATION_END = "2024-12-31"
BASELINE_BPS = 30.0
STRESS_BPS = (30.0, 60.0, 100.0)
MAX_HOLD_15M_BARS = 130
MAX_OPEN = 3
MAX_GROUP = 2
MAX_SIGNALS_DAY = 3
MAX_CORR = 0.85

PRIMARY = old.PRIMARY_SYMBOLS
INDEPENDENT = old.INDEPENDENT_SYMBOLS


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _rsi_state(close: pd.Series, n: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    d = close.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    al = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rsi = (100 - 100 / (1 + ag / al.replace(0, np.nan))).fillna(50)
    return rsi, ag, al


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["Close"].shift(1)
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()


def _regular_5m(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    x = df.copy()
    idx = pd.DatetimeIndex(x.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    x.index = idx.tz_convert(NY)
    minutes = x.index.hour * 60 + x.index.minute
    x = x[(minutes >= 570) & (minutes < 960) & (x.index.dayofweek < 5)]
    return x.sort_index()


def _aggregate_by(df: pd.DataFrame, keys: Iterable) -> pd.DataFrame:
    z = df.copy()
    z["_key"] = list(keys)
    out = z.groupby("_key", sort=False).agg(
        Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"),
        Close=("Close", "last"), Volume=("Volume", "sum")
    )
    return out


def _session_date_index(df: pd.DataFrame) -> pd.Series:
    return pd.Series([ts.date() for ts in df.index], index=df.index)


def _bin_key(ts: pd.Timestamp, width_min: int) -> Tuple[date, int]:
    mins = ts.hour * 60 + ts.minute - 570
    return ts.date(), int(max(0, mins) // width_min)


@dataclass
class Prepared:
    five: pd.DataFrame
    daily: pd.DataFrame
    daily_pos: Dict[date, int]
    full15: pd.DataFrame
    full15_pos: Dict[Tuple[date, int], int]
    hourly: pd.DataFrame
    hourly_pos: Dict[Tuple[date, int], int]


def prepare(df5: pd.DataFrame) -> Prepared:
    five = _regular_5m(df5)
    if five.empty:
        raise ValueError("empty regular-session 5m frame")

    # Completed daily bars.
    daily = _aggregate_by(five, [ts.date() for ts in five.index])
    daily.index.name = "session"
    daily["EMA20"] = _ema(daily["Close"], 20)
    daily["EMA50"] = _ema(daily["Close"], 50)
    daily["EMA200"] = _ema(daily["Close"], 200)
    daily["RSI14"], daily["AVG_GAIN14"], daily["AVG_LOSS14"] = _rsi_state(daily["Close"], 14)
    daily["ATR14"] = _atr(daily, 14)
    daily_pos = {d: i for i, d in enumerate(daily.index)}

    # Completed 15m bars, for point-in-time ATR state and later trade simulation.
    keys15 = [_bin_key(ts, 15) for ts in five.index]
    full15 = _aggregate_by(five, keys15)
    full15.index.name = "bin15"
    full15["ATR14"] = _atr(full15, 14)
    full15_pos = {k: i for i, k in enumerate(full15.index)}

    # Completed 60m bars anchored at 09:30 ET, matching the session structure.
    keys60 = [_bin_key(ts, 60) for ts in five.index]
    hourly = _aggregate_by(five, keys60)
    hourly.index.name = "bin60"
    hourly["EMA9"] = _ema(hourly["Close"], 9)
    hourly["EMA21"] = _ema(hourly["Close"], 21)
    hourly["EMA50"] = _ema(hourly["Close"], 50)
    hourly_pos = {k: i for i, k in enumerate(hourly.index)}
    return Prepared(five, daily, daily_pos, full15, full15_pos, hourly, hourly_pos)


def _safe(v, default=0.0) -> float:
    try:
        f = float(v)
        return default if not math.isfinite(f) else f
    except Exception:
        return default


def _partial_daily_metrics(prep: Prepared, session: date, o: float, h: float, l: float, c: float, v: float) -> Optional[dict]:
    pos = prep.daily_pos.get(session)
    if pos is None or pos < 205:
        return None
    d = prep.daily
    prev = d.iloc[pos - 1]
    prev_close = float(prev.Close)

    a20, a50, a200 = 2 / 21, 2 / 51, 2 / 201
    e20 = a20 * c + (1 - a20) * float(prev.EMA20)
    e50 = a50 * c + (1 - a50) * float(prev.EMA50)
    e200 = a200 * c + (1 - a200) * float(prev.EMA200)

    delta = c - prev_close
    gain, loss = max(delta, 0.0), max(-delta, 0.0)
    ag = (1 / 14) * gain + (13 / 14) * _safe(prev.AVG_GAIN14)
    al = (1 / 14) * loss + (13 / 14) * _safe(prev.AVG_LOSS14)
    rsi = 100.0 if al <= 1e-12 and ag > 0 else 50.0 if al <= 1e-12 else 100 - 100 / (1 + ag / al)

    tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
    atr_d = (1 / 14) * tr + (13 / 14) * _safe(prev.ATR14, c * 0.025)
    atr_d = max(atr_d, c * 0.005)

    prev19_vol = d["Volume"].iloc[max(0, pos - 19):pos]
    vol_ma20 = (float(prev19_vol.sum()) + v) / 20.0 if len(prev19_vol) == 19 else float("nan")
    daily_rvol = v / vol_ma20 if math.isfinite(vol_ma20) and vol_ma20 > 0 else 1.0

    close5 = float(d["Close"].iloc[pos - 5])
    close20 = float(d["Close"].iloc[pos - 20])
    high20 = float(d["High"].iloc[pos - 20:pos].max())
    ret5 = (c / close5 - 1) * 100 if close5 else 0.0
    ret20 = (c / close20 - 1) * 100 if close20 else 0.0
    dist20 = (c / high20 - 1) * 100 if high20 else 0.0

    bull = c > e20 > e50 and e50 > e200
    bear = c < e20 < e50
    return {
        "prev_close": prev_close,
        "ema20": e20, "ema50": e50, "ema200": e200,
        "rsi_d": rsi, "atr_d": atr_d, "daily_rvol": daily_rvol,
        "ret5d": ret5, "ret20d": ret20, "high20": high20,
        "distance_20d_high_pct": dist20,
        "daily_trend": bool(bull), "daily_bear": bool(bear),
        "price_above_ema20": bool(c > e20),
    }


def _partial_hourly(prep: Prepared, key: Tuple[date, int], close: float) -> Optional[dict]:
    pos = prep.hourly_pos.get(key)
    if pos is None or pos < 55:
        return None
    prev = prep.hourly.iloc[pos - 1]
    e9 = (2 / 10) * close + (8 / 10) * float(prev.EMA9)
    e21 = (2 / 22) * close + (20 / 22) * float(prev.EMA21)
    e50 = (2 / 51) * close + (49 / 51) * float(prev.EMA50)
    return {
        "hourly_positive": bool(close > e21 and e9 >= e21),
        "hourly_bull": bool(close > e9 > e21 > e50),
        "h9": e9, "h21": e21, "h50": e50,
    }


def _partial_intraday_atr(prep: Prepared, key: Tuple[date, int], o: float, h: float, l: float, c: float) -> float:
    pos = prep.full15_pos.get(key)
    if pos is None or pos < 15:
        return max(c * 0.015, c * 0.002)
    prev = prep.full15.iloc[pos - 1]
    pc = float(prev.Close)
    tr = max(h - l, abs(h - pc), abs(l - pc))
    atr_i = (1 / 14) * tr + (13 / 14) * _safe(prev.ATR14, c * 0.015)
    return max(atr_i, c * 0.002)


def swing_raw_score(m: dict) -> int:
    sc = 0
    sc += 25 if m["daily_trend"] else 8 if m["price_above_ema20"] else 0
    sc += 20 if m["ret20d"] >= 8 else 12 if m["ret20d"] >= 4 else 0
    sc += 15 if 0 < m["ret5d"] <= 8 else 6 if m["ret5d"] > 0 else 0
    sc += 15 if m["distance_20d_high_pct"] >= -3 else 7 if m["distance_20d_high_pct"] >= -6 else 0
    sc += 10 if 50 <= m["rsi_d"] <= 68 else 0
    sc += 10 if m["daily_rvol"] >= 1.2 else 4 if m["daily_rvol"] >= 1.0 else 0
    sc += 5 if m["day_change_pct"] >= 0 else 0
    return int(max(0, min(100, sc)))


def swing_weight(regime: str) -> float:
    if regime == "RISK_ON":
        return 1.02
    if regime in ("RISK_OFF", "HIGH_VOLATILITY"):
        return 0.94
    return 1.05


def swing_threshold(regime: str) -> int:
    return 79 if regime in ("RISK_OFF", "HIGH_VOLATILITY") else 74


def build_scan_features(prep: Prepared) -> pd.DataFrame:
    rows: List[dict] = []
    five = prep.five
    by_day = five.groupby([ts.date() for ts in five.index], sort=True)

    for session, day in by_day:
        if session not in prep.daily_pos or prep.daily_pos[session] < 205:
            continue
        day = day.sort_index()
        do = dh = dl = dc = dv = None
        current15 = None
        b15o = b15h = b15l = b15c = b15v = None
        completed15_pv = 0.0
        completed15_vol = 0.0
        current60 = None
        b60o = b60h = b60l = b60c = b60v = None

        def finalize15():
            nonlocal completed15_pv, completed15_vol
            if b15c is None or b15v is None:
                return
            typical = (b15h + b15l + b15c) / 3.0
            completed15_pv += typical * b15v
            completed15_vol += b15v

        def emit(scan_ts: pd.Timestamp):
            if dc is None or current15 is None or current60 is None:
                return
            dm = _partial_daily_metrics(prep, session, do, dh, dl, dc, dv)
            hm = _partial_hourly(prep, current60, b60c)
            if not dm or not hm:
                return
            atr_i = _partial_intraday_atr(prep, current15, b15o, b15h, b15l, b15c)
            current_typ = (b15h + b15l + b15c) / 3.0
            denom = completed15_vol + b15v
            vwap = (completed15_pv + current_typ * b15v) / denom if denom > 0 else dc
            row = {
                "scan_at": scan_ts,
                "price": float(dc),
                **dm, **hm,
                "day_change_pct": (dc / dm["prev_close"] - 1) * 100 if dm["prev_close"] else 0.0,
                "vwap": float(vwap),
                "atr_i": float(atr_i),
                "vwap_extension_atr": (dc - vwap) / max(atr_i, 1e-9),
            }
            row["raw_score"] = swing_raw_score(row)
            rows.append(row)

        for ts, bar in day.iterrows():
            o, h, l, c, v = map(float, (bar.Open, bar.High, bar.Low, bar.Close, bar.Volume))
            if do is None:
                do, dh, dl, dc, dv = o, h, l, c, v
            else:
                dh, dl, dc, dv = max(dh, h), min(dl, l), c, dv + v

            k15 = _bin_key(ts, 15)
            if current15 != k15:
                if current15 is not None:
                    finalize15()
                current15 = k15
                b15o, b15h, b15l, b15c, b15v = o, h, l, c, v
            else:
                b15h, b15l, b15c, b15v = max(b15h, h), min(b15l, l), c, b15v + v

            k60 = _bin_key(ts, 60)
            if current60 != k60:
                current60 = k60
                b60o, b60h, b60l, b60c, b60v = o, h, l, c, v
            else:
                b60h, b60l, b60c, b60v = max(b60h, h), min(b60l, l), c, b60v + v

            end_ts = ts + pd.Timedelta(minutes=5)
            mins = end_ts.hour * 60 + end_ts.minute
            if 575 <= mins <= 950 and (mins - 575) % 15 == 0:
                emit(end_ts)

        # V7.2 also runs at 16:05 ET. No 16:00-16:05 regular bar exists, so
        # this uses the final 15:55-16:00 data exactly as the live scan would.
        if dc is not None:
            end = pd.Timestamp(datetime.combine(session, time(16, 5), tzinfo=NY))
            emit(end)

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).set_index("scan_at").sort_index()
    return out


def _download_vix_previous_close(start: str, end: str) -> pd.Series:
    try:
        x = yf.download("^VIX", start=start, end=end, auto_adjust=False, progress=False, threads=False, timeout=30)
        if isinstance(x.columns, pd.MultiIndex):
            x.columns = x.columns.get_level_values(0)
        s = pd.to_numeric(x["Close"], errors="coerce").dropna()
        s.index = pd.to_datetime(s.index).date
        return s
    except Exception:
        return pd.Series(dtype=float)


def build_regime(spy: pd.DataFrame, qqq: pd.DataFrame, vix_close: pd.Series) -> pd.DataFrame:
    idx = spy.index.intersection(qqq.index)
    rows = []
    vix_dates = sorted(vix_close.index) if len(vix_close) else []
    for ts in idx:
        s, q = spy.loc[ts], qqq.loc[ts]
        d = ts.date()
        vv = 20.0
        if vix_dates:
            prior = [x for x in vix_dates if x < d]
            if prior:
                vv = _safe(vix_close.loc[prior[-1]], 20.0)
        if bool(s.daily_trend) and bool(q.daily_trend) and vv < 25:
            label = "RISK_ON"
        elif bool(s.daily_bear) and bool(q.daily_bear):
            label = "RISK_OFF"
        elif vv >= 30:
            label = "HIGH_VOLATILITY"
        else:
            label = "MIXED"
        rows.append({"scan_at": ts, "regime": label, "vix_previous_close": vv})
    return pd.DataFrame(rows).set_index("scan_at") if rows else pd.DataFrame()


def candidates(symbol: str, features: pd.DataFrame, regime: pd.DataFrame) -> List[dict]:
    out = []
    common = features.index.intersection(regime.index)
    for ts in common:
        r = features.loc[ts]
        reg = str(regime.loc[ts, "regime"])
        mtf_pass = bool(r.hourly_positive) and bool(r.daily_trend)
        strong = bool(r.hourly_bull) and bool(r.daily_trend)
        if not mtf_pass:
            continue
        mtf_adjust = 6 if strong else 2
        routed = int(max(0, min(100, round(float(r.raw_score) * swing_weight(reg)))))
        final = int(max(0, min(100, routed + mtf_adjust)))  # catalyst neutral = 0
        th = swing_threshold(reg)
        extended = float(r.vwap_extension_atr) > 2.5 or float(r.day_change_pct) > 15
        if extended or final < th:
            continue
        risk = max(1.5 * float(r.atr_d), float(r.price) * 0.025)
        risk = min(risk, float(r.price) * 0.08)
        out.append({
            "symbol": symbol,
            "group": old.group_for(symbol),
            "setup": "SWING_CONTINUATION",
            "regime": reg,
            "signal_at": ts.isoformat(),
            "signal_date": ts.date().isoformat(),
            "year": ts.year,
            "score": final,
            "raw_score": int(r.raw_score),
            "base_threshold": th,
            "signal_price": round(float(r.price), 6),
            "risk": round(float(risk), 6),
            "mtf": "PASS_STRONG" if strong else "PASS",
            "vwap_extension_atr": round(float(r.vwap_extension_atr), 4),
            "vix_previous_close": round(float(regime.loc[ts, "vix_previous_close"]), 3),
        })
    return out


def simulate(c: dict, prep: Prepared, bps: float = BASELINE_BPS) -> Optional[dict]:
    bars = prep.full15
    signal_ts = pd.Timestamp(c["signal_at"])
    # full15 index is (session_date, bin). Build a real timestamp index for starts.
    starts = []
    for d, b in bars.index:
        starts.append(pd.Timestamp(datetime.combine(d, time(9, 30), tzinfo=NY)) + pd.Timedelta(minutes=15 * int(b)))
    start_idx = pd.DatetimeIndex(starts)
    p = int(start_idx.searchsorted(signal_ts, side="right"))
    if p >= len(bars):
        return None
    entry = float(bars.iloc[p].Open)
    risk = float(c["risk"])
    if entry <= 0 or risk <= 0:
        return None
    stop = entry - risk
    target = entry + 1.8 * risk
    last = min(p + MAX_HOLD_15M_BARS - 1, len(bars) - 1)
    exit_px = float(bars.iloc[last].Close)
    exit_p = last
    reason = "TIME"
    for j in range(p, last + 1):
        b = bars.iloc[j]
        op, lo, hi = float(b.Open), float(b.Low), float(b.High)
        if op <= stop:
            exit_px, exit_p, reason = op, j, "STOP_GAP"; break
        if lo <= stop:
            exit_px, exit_p, reason = stop, j, "STOP"; break
        if op >= target or hi >= target:
            exit_px, exit_p, reason = target, j, "TARGET"; break
    gross = (exit_px - entry) / risk
    cost_r = entry * (bps / 10000.0) / risk
    z = dict(c)
    z.update({
        "entry": round(entry, 6),
        "entry_at": start_idx[p].isoformat(),
        "entry_date": start_idx[p].date().isoformat(),
        "exit": round(exit_px, 6),
        "exit_at": start_idx[exit_p].isoformat(),
        "exit_date": start_idx[exit_p].date().isoformat(),
        "gross_r": round(float(gross), 6),
        "baseline_cost_r": round(float(cost_r), 6),
        "net_r": round(float(gross - cost_r), 6),
        "outcome": reason,
    })
    return z


def _rolling_corr_pti(symbol: str, other: str, signal_date: str, daily_frames: Dict[str, pd.DataFrame]) -> Optional[float]:
    if symbol == other:
        return 1.0
    a, b = daily_frames.get(symbol), daily_frames.get(other)
    if a is None or b is None:
        return None
    d = datetime.fromisoformat(signal_date).date()
    ar = a.loc[[x < d for x in a.index], "Close"].pct_change()
    br = b.loc[[x < d for x in b.index], "Close"].pct_change()
    z = pd.concat([ar, br], axis=1, join="inner").dropna().tail(60)
    if len(z) < 30:
        return None
    return float(z.iloc[:, 0].corr(z.iloc[:, 1]))


def portfolio_select(rows: List[dict], daily_frames: Dict[str, pd.DataFrame]) -> Tuple[List[dict], Dict[str, int]]:
    ordered = sorted(rows, key=lambda x: (x["signal_at"], -x["score"], x["symbol"]))
    active: List[dict] = []
    selected: List[dict] = []
    rejects = defaultdict(int)
    day_counts = defaultdict(int)
    for t in ordered:
        entry_at = pd.Timestamp(t["entry_at"])
        active = [a for a in active if pd.Timestamp(a["exit_at"]) >= entry_at]
        day = t["signal_date"]
        if day_counts[day] >= MAX_SIGNALS_DAY:
            rejects["daily_signal_cap"] += 1; continue
        if len(active) >= MAX_OPEN:
            rejects["open_risk_cap"] += 1; continue
        if any(a["symbol"] == t["symbol"] for a in active):
            rejects["duplicate_symbol"] += 1; continue
        if sum(1 for a in active if a["group"] == t["group"]) >= MAX_GROUP:
            rejects["group_risk_cap"] += 1; continue
        blocked = False
        for a in active:
            corr = _rolling_corr_pti(t["symbol"], a["symbol"], t["signal_date"], daily_frames)
            if corr is not None and corr >= MAX_CORR:
                blocked = True; break
        if blocked:
            rejects["correlation_cap"] += 1; continue
        selected.append(t)
        active.append(t)
        day_counts[day] += 1
    return selected, dict(rejects)


def split_name(d: str) -> str:
    if d <= TRAIN_END:
        return "train"
    if d <= VALIDATION_END:
        return "validation"
    return "holdout"


def split_stats(rows: List[dict]) -> dict:
    return {k: old.stats([r for r in rows if split_name(r["signal_date"]) == k]) for k in ("train", "validation", "holdout")}


def research(symbols: List[str], independent: bool, start: str, end: str, feed: str) -> Tuple[List[dict], Dict[str, pd.DataFrame], Dict[str, str], pd.DataFrame]:
    data = AlpacaMarketData(feed=feed)
    errors: Dict[str, str] = {}
    prepared: Dict[str, Prepared] = {}
    scan_features: Dict[str, pd.DataFrame] = {}

    market_symbols = sorted(set(symbols + ["SPY", "QQQ"]))
    for sym in market_symbols:
        try:
            bars = data.bars(sym, "5Min", start, end, adjustment="all")
            p = prepare(bars)
            prepared[sym] = p
            scan_features[sym] = build_scan_features(p)
        except Exception as exc:
            errors[sym] = str(exc)

    if "SPY" not in scan_features or "QQQ" not in scan_features:
        raise RuntimeError(f"Market regime data unavailable: {errors}")
    vix = _download_vix_previous_close(start, end)
    regime = build_regime(scan_features["SPY"], scan_features["QQQ"], vix)

    simulated: List[dict] = []
    daily_frames: Dict[str, pd.DataFrame] = {s: p.daily[["Close"]].copy() for s, p in prepared.items()}
    for sym in symbols:
        if sym not in scan_features:
            continue
        try:
            for c in candidates(sym, scan_features[sym], regime):
                t = simulate(c, prepared[sym], BASELINE_BPS)
                if t:
                    simulated.append(t)
        except Exception as exc:
            errors[sym] = str(exc)

    selected, rejects = portfolio_select(simulated, daily_frames)
    errors["_portfolio_rejections"] = json.dumps(rejects, sort_keys=True)
    return selected, daily_frames, errors, regime


def _verdict(validation: dict, holdout: dict, independent_holdout: dict) -> str:
    promising = (
        validation["samples"] >= 60 and validation["expectancy_r"] > 0 and validation["profit_factor"] >= 1.15
        and holdout["samples"] >= 60 and holdout["expectancy_r"] > 0 and holdout["profit_factor"] >= 1.15
        and holdout["max_drawdown_r"] <= 12
        and independent_holdout["samples"] >= 30 and independent_holdout["expectancy_r"] >= 0
    )
    strong = (
        validation["samples"] >= 100 and validation["expectancy_r"] >= 0.08 and validation["profit_factor"] >= 1.20
        and holdout["samples"] >= 100 and holdout["expectancy_r"] >= 0.10 and holdout["profit_factor"] >= 1.30
        and holdout["max_drawdown_r"] <= 8
        and independent_holdout["samples"] >= 50 and independent_holdout["expectancy_r"] > 0 and independent_holdout["profit_factor"] >= 1.15
    )
    if strong:
        return "PAPER_CANDIDATE"
    if promising:
        return "PROMISING"
    return "FAIL"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START)
    ap.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--feed", default="iex", choices=["iex", "sip", "delayed_sip"])
    ap.add_argument("--primary-limit", type=int, default=0, help="0=all")
    ap.add_argument("--independent-limit", type=int, default=0, help="0=all")
    args = ap.parse_args()

    primary = PRIMARY[:args.primary_limit] if args.primary_limit else list(PRIMARY)
    independent = INDEPENDENT[:args.independent_limit] if args.independent_limit else list(INDEPENDENT)

    p_rows, _, p_errors, regime = research(primary, False, args.start, args.end, args.feed)
    i_rows, _, i_errors, _ = research(independent, True, args.start, args.end, args.feed)
    split = split_stats(p_rows)
    independent_hold = [r for r in i_rows if split_name(r["signal_date"]) == "holdout"]
    im = old.stats(independent_hold)
    hold = [r for r in p_rows if split_name(r["signal_date"]) == "holdout"]
    val = [r for r in p_rows if split_name(r["signal_date"]) == "validation"]
    stress = {str(int(b)): old.stats(hold, bps=b) for b in STRESS_BPS}
    ci = old.bootstrap_ci(hold)
    verdict = _verdict(split["validation"], split["holdout"], im)

    by_regime = {r: old.stats([x for x in hold if x["regime"] == r]) for r in ("RISK_ON", "MIXED", "RISK_OFF", "HIGH_VOLATILITY")}
    years = sorted({x["year"] for x in p_rows})
    by_year = {str(y): old.stats([x for x in p_rows if x["year"] == y]) for y in years}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "SWING_CONTINUATION 5m Point-in-Time Fidelity Research",
        "mode": "RESEARCH_ONLY",
        "production_v7_changed": False,
        "live_execution_authorized": False,
        "verdict": verdict,
        "data": {
            "provider": "Alpaca Market Data",
            "feed": args.feed,
            "bar_source": "5Min reconstructed into partial 15m / partial 60m / partial daily state",
            "start": args.start, "end": args.end,
            "primary_symbols": primary, "independent_symbols": independent,
            "primary_errors": p_errors, "independent_errors": i_errors,
        },
        "fidelity": {
            "scan_times": "09:35, 09:50, then every 15m through 15:50, plus 16:05 America/New_York",
            "entry": "next 15m bar open after scan, matching current V7.2 paper pending-entry semantics",
            "mtf": "hourly partial EMA state + partial daily state reconstructed point-in-time",
            "daily": "partial current session OHLCV; no end-of-day look-ahead",
            "extended_filter": "V7.2 VWAP-extension >2.5 ATR or day change >15% blocks entry",
            "catalyst": "neutral 0; historical headline score is not fabricated",
            "vix": "previous completed VIX close; point-in-time safe but one-session lagged",
            "sharia": "same conservative known-group precheck; not formal Sharia certification",
            "portfolio_limits": {"risk_per_trade_pct": 0.5, "max_open_risk_pct": 1.5, "max_group_risk_pct": 1.0, "max_pair_correlation": 0.85, "max_signals_per_day": 3},
            "cost_baseline_round_trip_bps": BASELINE_BPS,
            "survivorship_selection_bias_warning": True,
        },
        "primary": {
            "split": split,
            "holdout_expectancy_bootstrap_95pct": ci,
            "holdout_cost_stress_round_trip_bps": stress,
            "holdout_by_regime": by_regime,
            "by_year": by_year,
        },
        "independent_holdout": im,
        "decision": {
            "PAPER_CANDIDATE": "Strong validation + holdout + independent evidence. Still paper-only; live remains locked.",
            "PROMISING": "Continue focused paper/forward validation; do not broaden V7 features.",
            "FAIL": "Retire current SWING hypothesis instead of further V7 engineering.",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "split": split, "independent_holdout": im, "stress": stress}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
