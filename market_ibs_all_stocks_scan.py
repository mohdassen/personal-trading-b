#!/usr/bin/env python3
"""Breadth scan of the exact published IBS/lower-band entry condition across U.S.-listed stocks.

This is an opportunity scanner, not evidence that the QQQ edge transfers to individual stocks.
It deliberately freezes the Round-13 entry rule and does not optimize parameters.
"""
from __future__ import annotations

import io
import json
import math
import time
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
OUT = Path("data/market_ibs_all_stocks_scan.json")
BATCH = 120
PERIOD = "4mo"
IBS_MAX = 0.30
BAND_MULT = 2.5
HIGH_WINDOW = 10
RANGE_WINDOW = 25

EXCLUDE_NAME_TERMS = (
    " warrant", " warrants", " unit", " units", " right", " rights",
    " preferred", " preference", " notes due", " note due", " bond",
    " debenture", " depositary shares", " beneficial interest in a trust",
)


def fetch_pipe(url: str) -> pd.DataFrame:
    headers = {"User-Agent": "Mozilla/5.0 market-research/1.0"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    lines = [x for x in r.text.splitlines() if x and not x.startswith("File Creation Time")]
    return pd.read_csv(io.StringIO("\n".join(lines)), sep="|", dtype=str)


def build_universe() -> pd.DataFrame:
    n = fetch_pipe(NASDAQ_URL)
    o = fetch_pipe(OTHER_URL)

    n = n[(n["Test Issue"] == "N") & (n["ETF"] == "N")].copy()
    n = n.rename(columns={"Symbol": "symbol", "Security Name": "name"})[["symbol", "name"]]

    o = o[(o["Test Issue"] == "N") & (o["ETF"] == "N")].copy()
    o = o.rename(columns={"ACT Symbol": "symbol", "Security Name": "name"})[["symbol", "name"]]

    u = pd.concat([n, o], ignore_index=True).drop_duplicates("symbol")
    u = u[u["symbol"].notna() & u["name"].notna()].copy()
    low = u["name"].str.lower()
    keep = pd.Series(True, index=u.index)
    for term in EXCLUDE_NAME_TERMS:
        keep &= ~low.str.contains(term, regex=False)
    u = u[keep].copy()

    # Yahoo uses '-' for class-share tickers such as BRK-B. Drop symbols Yahoo cannot address cleanly.
    u["yf_symbol"] = u["symbol"].str.replace(".", "-", regex=False)
    u = u[~u["yf_symbol"].str.contains(r"[/$^ ]", regex=True)].copy()
    return u.sort_values("symbol").reset_index(drop=True)


def latest_complete_ny_date() -> str:
    now = datetime.now(ZoneInfo("America/New_York"))
    # Avoid the still-forming regular-session daily candle. Give Yahoo 20 minutes after close.
    d = now.date()
    if now.weekday() >= 5:
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    elif now.time() < dtime(16, 20):
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d.isoformat()


def extract_symbol_frame(raw: pd.DataFrame, ticker: str, batch_size: int) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            # yfinance can return either [Price,Ticker] or [Ticker,Price].
            if ticker in raw.columns.get_level_values(0):
                d = raw[ticker].copy()
            elif ticker in raw.columns.get_level_values(1):
                d = raw.xs(ticker, axis=1, level=1).copy()
            else:
                return None
        else:
            if batch_size != 1:
                return None
            d = raw.copy()
        needed = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in d.columns for c in needed):
            return None
        d = d[needed].dropna(subset=["High", "Low", "Close"])
        if d.empty:
            return None
        idx = pd.to_datetime(d.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert("America/New_York").tz_localize(None)
        d.index = idx.normalize()
        return d
    except Exception:
        return None


def scan_frame(d: pd.DataFrame, cutoff: pd.Timestamp) -> dict | None:
    d = d[d.index <= cutoff].copy()
    if len(d) < RANGE_WINDOW:
        return None
    d["range"] = d["High"] - d["Low"]
    d["avg_range25"] = d["range"].rolling(RANGE_WINDOW).mean()
    d["high10"] = d["High"].rolling(HIGH_WINDOW).max()
    d["lower_band"] = d["high10"] - BAND_MULT * d["avg_range25"]
    d["ibs"] = (d["Close"] - d["Low"]) / d["range"].where(d["range"] > 0)
    row = d.iloc[-1]
    if pd.isna(row["lower_band"]) or pd.isna(row["ibs"]):
        return None
    close = float(row["Close"])
    lower = float(row["lower_band"])
    ibs = float(row["ibs"])
    signal = bool(close < lower and ibs < IBS_MAX)
    # Negative means close is below the lower band.
    band_distance_pct = (close / lower - 1.0) * 100.0 if lower else math.nan
    volume = int(row["Volume"]) if pd.notna(row["Volume"]) else None
    return {
        "date": d.index[-1].date().isoformat(),
        "close": round(close, 4),
        "lower_band": round(lower, 4),
        "ibs": round(ibs, 4),
        "band_distance_pct": round(band_distance_pct, 4),
        "volume": volume,
        "signal": signal,
    }


def main() -> None:
    started = datetime.now(ZoneInfo("UTC"))
    universe = build_universe()
    cutoff_str = latest_complete_ny_date()
    cutoff = pd.Timestamp(cutoff_str)
    name_by_yf = dict(zip(universe["yf_symbol"], universe["name"]))
    original_by_yf = dict(zip(universe["yf_symbol"], universe["symbol"]))
    tickers = universe["yf_symbol"].tolist()

    signals: list[dict] = []
    near: list[dict] = []
    valid = 0
    latest_date_counts: dict[str, int] = {}
    download_failures: list[str] = []

    for start in range(0, len(tickers), BATCH):
        batch = tickers[start:start + BATCH]
        raw = None
        for attempt in range(3):
            try:
                raw = yf.download(
                    batch,
                    period=PERIOD,
                    interval="1d",
                    auto_adjust=True,
                    group_by="ticker",
                    threads=True,
                    progress=False,
                    timeout=30,
                )
                if raw is not None and not raw.empty:
                    break
            except Exception:
                raw = None
            time.sleep(2 * (attempt + 1))

        for t in batch:
            d = extract_symbol_frame(raw, t, len(batch)) if raw is not None else None
            r = scan_frame(d, cutoff) if d is not None else None
            if r is None:
                download_failures.append(original_by_yf.get(t, t))
                continue
            # Require the security to have a bar on the dominant/latest complete session.
            latest_date_counts[r["date"]] = latest_date_counts.get(r["date"], 0) + 1
            item = {
                "symbol": original_by_yf.get(t, t),
                "name": name_by_yf.get(t, ""),
                **r,
            }
            near.append(item)
            valid += 1
        print(f"progress {min(start+BATCH,len(tickers))}/{len(tickers)} valid={valid}", flush=True)

    # Pick the market date represented by the largest number of valid securities, then only compare that date.
    dominant_date = max(latest_date_counts, key=latest_date_counts.get) if latest_date_counts else None
    same_day = [x for x in near if x["date"] == dominant_date] if dominant_date else []
    signals = [x for x in same_day if x["signal"]]
    signals.sort(key=lambda x: (x["band_distance_pct"], x["ibs"]))

    # Diagnostic only: closest non-signals to the fixed rule. Never use these as trades.
    near_non = [x for x in same_day if not x["signal"]]
    near_non.sort(key=lambda x: (max(x["band_distance_pct"], 0), max(x["ibs"] - IBS_MAX, 0)))

    finished = datetime.now(ZoneInfo("UTC"))
    report = {
        "scan_name": "ALL_US_LISTED_STOCKS_EXACT_IBS_ENTRY_BREADTH",
        "generated_at_utc": finished.isoformat(),
        "latest_complete_session_cutoff_ny": cutoff_str,
        "asof_market_date": dominant_date,
        "universe_definition": "NASDAQ/NYSE/NYSE American and other Nasdaq-Trader-listed non-ETF securities; test issues, warrants, units, rights, preferred/debt-like names excluded; current listings only",
        "rule_frozen": {
            "lower_band": "rolling 10-day high - 2.5 * rolling 25-day mean(high-low)",
            "entry_signal": "close < lower_band AND IBS < 0.30",
            "IBS": "(close-low)/(high-low)",
            "execution_if_validated": "next-session open",
        },
        "important_scope_note": "Breadth/opportunity scan only. It does NOT prove that the QQQ strategy edge transfers to individual stocks and it is not a buy recommendation.",
        "counts": {
            "current_non_etf_listings_after_security_filters": int(len(universe)),
            "valid_with_recent_bars_any_date": int(valid),
            "valid_on_asof_market_date": int(len(same_day)),
            "signal_count": int(len(signals)),
            "failed_or_insufficient_data": int(len(download_failures)),
        },
        "signals": signals,
        "nearest_non_signals_top20": near_non[:20],
        "failed_symbols_sample": download_failures[:100],
        "runtime_seconds": round((finished - started).total_seconds(), 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "asof": dominant_date,
        "universe": len(universe),
        "valid_asof": len(same_day),
        "signals": len(signals),
        "runtime_seconds": report["runtime_seconds"],
        "signal_symbols": [x["symbol"] for x in signals],
    }, indent=2))


if __name__ == "__main__":
    main()
