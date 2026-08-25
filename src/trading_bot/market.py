from __future__ import annotations
import time
import yfinance as yf
import pandas as pd


def fetch_intraday(symbol: str, period: str = "5d", interval: str = "15m", retries: int = 3) -> pd.DataFrame:
    last_exc = None
    for attempt in range(retries):
        try:
            df = yf.download(
                symbol,
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False,
                threads=False,
                prepost=False,
            )
            if isinstance(df.columns, pd.MultiIndex):
                if symbol in df.columns.get_level_values(-1):
                    df = df.xs(symbol, axis=1, level=-1)
                else:
                    df.columns = df.columns.get_level_values(0)
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if not df.empty:
                return df
        except Exception as exc:
            last_exc = exc
        time.sleep(2 ** attempt)
    if last_exc:
        raise RuntimeError(f"Failed to fetch {symbol}: {last_exc}")
    return pd.DataFrame()


def fetch_daily(symbol: str, period: str = "3mo") -> pd.DataFrame:
    df = yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        if symbol in df.columns.get_level_values(-1):
            df = df.xs(symbol, axis=1, level=-1)
        else:
            df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Close"])
