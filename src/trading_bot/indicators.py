from __future__ import annotations
import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def session_vwap(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        dates = idx.tz_convert("America/New_York").date
    else:
        dates = idx.date
    groups = pd.Series(dates, index=df.index)
    pv = typical * df["Volume"]
    cum_pv = pv.groupby(groups).cumsum()
    cum_vol = df["Volume"].groupby(groups).cumsum().replace(0, np.nan)
    return cum_pv / cum_vol


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["EMA9"] = ema(x["Close"], 9)
    x["EMA21"] = ema(x["Close"], 21)
    x["EMA50"] = ema(x["Close"], 50)
    x["RSI14"] = rsi(x["Close"], 14)
    x["ATR14"] = atr(x, 14)
    x["VWAP"] = session_vwap(x)
    x["VOL_MA20"] = x["Volume"].rolling(20).mean()
    x["VOL_RATIO"] = x["Volume"] / x["VOL_MA20"].replace(0, np.nan)
    x["RET_1H"] = x["Close"].pct_change(4) * 100
    x["RET_1D"] = x["Close"].pct_change(26) * 100
    return x
