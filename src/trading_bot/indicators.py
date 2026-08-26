from __future__ import annotations
import numpy as np
import pandas as pd

def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def rsi(s,n=14):
    d=s.diff(); gain=d.clip(lower=0); loss=-d.clip(upper=0)
    ag=gain.ewm(alpha=1/n,min_periods=n,adjust=False).mean(); al=loss.ewm(alpha=1/n,min_periods=n,adjust=False).mean()
    return (100-100/(1+ag/al.replace(0,np.nan))).fillna(50)
def atr(df,n=14):
    pc=df['Close'].shift(1)
    tr=pd.concat([df['High']-df['Low'],(df['High']-pc).abs(),(df['Low']-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,min_periods=n,adjust=False).mean()
def session_vwap(df):
    if df.empty:return pd.Series(dtype=float)
    typical=(df['High']+df['Low']+df['Close'])/3; idx=df.index
    dates=idx.tz_convert('America/New_York').date if getattr(idx,'tz',None) is not None else idx.date
    groups=pd.Series(dates,index=idx); pv=typical*df['Volume']
    return pv.groupby(groups).cumsum()/df['Volume'].groupby(groups).cumsum().replace(0,np.nan)
def add_intraday(df):
    x=df.copy(); x['EMA9']=ema(x['Close'],9); x['EMA21']=ema(x['Close'],21); x['EMA50']=ema(x['Close'],50)
    x['RSI14']=rsi(x['Close']); x['ATR14']=atr(x); x['VWAP']=session_vwap(x)
    x['VOL_MA20']=x['Volume'].rolling(20).mean(); x['VOL_RATIO']=x['Volume']/x['VOL_MA20'].replace(0,np.nan)
    x['RET_1H']=x['Close'].pct_change(4)*100; x['HIGH20']=x['High'].rolling(20).max().shift(1); x['LOW20']=x['Low'].rolling(20).min().shift(1)
    return x
def add_daily(df):
    x=df.copy(); x['EMA20']=ema(x['Close'],20); x['EMA50']=ema(x['Close'],50); x['EMA200']=ema(x['Close'],200)
    x['RSI14']=rsi(x['Close']); x['ATR14']=atr(x); x['VOL_MA20']=x['Volume'].rolling(20).mean(); x['VOL_RATIO']=x['Volume']/x['VOL_MA20'].replace(0,np.nan)
    x['RET_5D']=x['Close'].pct_change(5)*100; x['HIGH20']=x['High'].rolling(20).max().shift(1); x['LOW20']=x['Low'].rolling(20).min().shift(1)
    return x
