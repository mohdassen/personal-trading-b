from __future__ import annotations
import time
import pandas as pd
import yfinance as yf

def _flat(df,symbol):
    if df is None or df.empty:return pd.DataFrame()
    if isinstance(df.columns,pd.MultiIndex):
        if symbol in df.columns.get_level_values(-1):df=df.xs(symbol,axis=1,level=-1)
        else:df.columns=df.columns.get_level_values(0)
    req=[c for c in ['Open','High','Low','Close'] if c in df.columns]
    return df.dropna(subset=req)
def fetch(symbol,period,interval,prepost=False,retries=3):
    last=None
    for a in range(retries):
        try:
            df=yf.download(symbol,period=period,interval=interval,auto_adjust=False,progress=False,threads=False,prepost=prepost)
            df=_flat(df,symbol)
            if not df.empty:return df
        except Exception as e:last=e
        time.sleep(2**a)
    if last:raise RuntimeError(f'{symbol}: {last}')
    return pd.DataFrame()
def quote_daily(symbol):return fetch(symbol,'6mo','1d')
def quote_intraday(symbol,period='5d',interval='15m'):return fetch(symbol,period,interval)
def recent_news(symbol,limit=8):
    try:return (yf.Ticker(symbol).news or [])[:limit]
    except Exception:return []
def earnings_date(symbol):
    try:
        cal=yf.Ticker(symbol).calendar
        if isinstance(cal,dict):
            v=cal.get('Earnings Date')
            if isinstance(v,(list,tuple)) and v:return pd.Timestamp(v[0])
            if v is not None:return pd.Timestamp(v)
        if hasattr(cal,'loc') and 'Earnings Date' in cal.index:
            v=cal.loc['Earnings Date']; v=v.iloc[0] if hasattr(v,'iloc') else v; return pd.Timestamp(v)
    except Exception:return None
    return None
