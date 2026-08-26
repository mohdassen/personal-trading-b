from __future__ import annotations
import os, time, threading
import pandas as pd
import requests, yfinance as yf

SESSION=requests.Session()
SESSION.headers.update({"User-Agent":"Mozilla/5.0 PersonalTradingAssistant"})
URL="https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
LOCK=threading.Lock()
HEALTH={"success":0,"failures":0,"providers":{},"errors":[]}

def reset_health():
    with LOCK:
        HEALTH.update({"success":0,"failures":0,"providers":{},"errors":[]})

def health_snapshot():
    with LOCK:
        return {"success":HEALTH["success"],"failures":HEALTH["failures"],
                "providers":dict(HEALTH["providers"]),"errors":list(HEALTH["errors"])}

def _ok(provider):
    with LOCK:
        HEALTH["success"]+=1
        HEALTH["providers"][provider]=HEALTH["providers"].get(provider,0)+1

def _fail(msg):
    with LOCK:
        HEALTH["failures"]+=1
        if len(HEALTH["errors"])<20: HEALTH["errors"].append(msg)

def _validate(df,symbol):
    if df is None or df.empty: raise RuntimeError(f"{symbol}: empty data")
    if isinstance(df.columns,pd.MultiIndex):
        if symbol in df.columns.get_level_values(-1): df=df.xs(symbol,axis=1,level=-1)
        else: df.columns=df.columns.get_level_values(0)
    for c in ["Open","High","Low","Close"]:
        if c not in df.columns: raise RuntimeError(f"{symbol}: missing {c}")
    if "Volume" not in df.columns: df["Volume"]=0
    df=df[["Open","High","Low","Close","Volume"]].dropna(subset=["Open","High","Low","Close"])
    if df.empty or float(df["Close"].iloc[-1])<=0: raise RuntimeError(f"{symbol}: invalid data")
    return df

def _chart(symbol,period,interval,prepost=False):
    r=SESSION.get(URL.format(symbol=requests.utils.quote(symbol,safe="^")),
        params={"range":period,"interval":interval,"includePrePost":str(prepost).lower(),"events":"div,splits"},
        timeout=20)
    r.raise_for_status()
    p=r.json().get("chart",{})
    if p.get("error"): raise RuntimeError(str(p["error"]))
    result=p.get("result")
    if not result: raise RuntimeError("no chart result")
    x=result[0]; ts=x.get("timestamp") or []
    q=((x.get("indicators") or {}).get("quote") or [{}])[0]
    if not ts: raise RuntimeError("no timestamps")
    df=pd.DataFrame({"Open":q.get("open"),"High":q.get("high"),"Low":q.get("low"),
                     "Close":q.get("close"),"Volume":q.get("volume")},
                    index=pd.to_datetime(ts,unit="s",utc=True))
    return _validate(df,symbol)

def _yf(symbol,period,interval,prepost=False):
    df=yf.download(symbol,period=period,interval=interval,auto_adjust=False,
                   progress=False,threads=False,prepost=prepost,timeout=20)
    return _validate(df,symbol)

def fetch(symbol,period,interval,prepost=False,retries=2):
    errors=[]
    for attempt in range(retries):
        try:
            df=_chart(symbol,period,interval,prepost); _ok("yahoo_chart"); return df
        except Exception as e: errors.append(f"chart:{e}")
        try:
            df=_yf(symbol,period,interval,prepost); _ok("yfinance"); return df
        except Exception as e: errors.append(f"yfinance:{e}")
        if attempt+1<retries: time.sleep(1.5*(attempt+1))
    msg=f"{symbol}: all providers failed | "+" | ".join(errors[-4:])
    _fail(msg); raise RuntimeError(msg)

def quote_daily(symbol): return fetch(symbol,"6mo","1d")
def quote_intraday(symbol,period="5d",interval="15m"): return fetch(symbol,period,interval)

def recent_news(symbol,limit=8):
    try: return (yf.Ticker(symbol).news or [])[:limit]
    except Exception: return []

def earnings_date(symbol):
    try:
        cal=yf.Ticker(symbol).calendar
        if isinstance(cal,dict):
            v=cal.get("Earnings Date")
            if isinstance(v,(list,tuple)) and v: return pd.Timestamp(v[0])
            if v is not None: return pd.Timestamp(v)
        if hasattr(cal,"loc") and "Earnings Date" in cal.index:
            v=cal.loc["Earnings Date"]
            if hasattr(v,"iloc"): v=v.iloc[0]
            return pd.Timestamp(v)
    except Exception: pass
    return None
