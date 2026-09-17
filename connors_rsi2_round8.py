from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT=Path("data/connors_rsi2_round8.json")
START="2005-01-01"
BASE_BPS=10.0
STRESS_BPS=30.0
SYMBOLS=["SPY","QQQ","DIA","IWM","EFA","EEM","MDY","VTI"]

def norm(raw,s):
    if raw is None or raw.empty:return pd.DataFrame()
    try:
        if isinstance(raw.columns,pd.MultiIndex):
            if s in raw.columns.get_level_values(0):x=raw[s].copy()
            elif s in raw.columns.get_level_values(-1):x=raw.xs(s,axis=1,level=-1).copy()
            else:return pd.DataFrame()
        else:x=raw.copy()
        need=["Open","High","Low","Close"]
        if not all(c in x.columns for c in need):return pd.DataFrame()
        x=x[need].dropna().copy()
        idx=pd.DatetimeIndex(x.index)
        if idx.tz is not None:idx=idx.tz_convert(None)
        x.index=idx
        return x.sort_index()
    except Exception:return pd.DataFrame()

def download():
    raw=yf.download(SYMBOLS,start=START,interval="1d",auto_adjust=True,progress=False,threads=True,group_by="ticker",timeout=30)
    out={}
    for s in SYMBOLS:
        x=norm(raw,s);out[s]=x;print(s,len(x))
    return out

def rsi_wilder(s,n=2):
    d=s.diff();up=d.clip(lower=0);dn=-d.clip(upper=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    rs=au/ad.replace(0,np.nan)
    return (100-100/(1+rs)).fillna(100.0)

def trades(df,bps):
    x=df.copy()
    x["sma200"]=x.Close.rolling(200).mean()
    x["sma5"]=x.Close.rolling(5).mean()
    x["rsi2"]=rsi_wilder(x.Close,2)
    out=[];i=200;n=len(x)
    while i<n-2:
        row=x.iloc[i]
        if not (row.Close>row.sma200 and row.rsi2<10):
            i+=1;continue
        entry_i=i+1;entry=float(x.iloc[entry_i].Open)
        if not math.isfinite(entry) or entry<=0:
            i+=1;continue
        exit_i=None;exitp=None
        # Entry at next open; exit signal only from closes after entry is established.
        for j in range(entry_i,n-1):
            if pd.notna(x.iloc[j].sma5) and x.iloc[j].Close>x.iloc[j].sma5:
                exit_i=j+1;exitp=float(x.iloc[exit_i].Open);break
        if exit_i is None:
            exit_i=n-1;exitp=float(x.iloc[exit_i].Close)
        gross=exitp/entry-1
        net=gross-bps/10000.0
        out.append({"signal_date":str(x.index[i].date()),"entry_date":str(x.index[entry_i].date()),"exit_date":str(x.index[exit_i].date()),"entry":entry,"exit":exitp,"net_return":net,"holding_days":exit_i-entry_i})
        i=exit_i+1
    return out

def period(ts,start,end=None):
    d=pd.Timestamp(ts)
    return d>=pd.Timestamp(start) and (end is None or d<=pd.Timestamp(end))

def metrics(t):
    if not t:return {"trades":0,"win_rate":None,"avg_return":None,"profit_factor":None,"median_return":None,"avg_hold_days":None}
    a=np.array([z["net_return"] for z in t],dtype=float)
    wins=a[a>0];loss=a[a<0]
    pf=float(wins.sum()/abs(loss.sum())) if len(loss) else 999.0
    return {"trades":len(a),"win_rate":round(float((a>0).mean()),3),"avg_return":round(float(a.mean()),5),"profit_factor":round(pf,3),"median_return":round(float(np.median(a)),5),"avg_hold_days":round(float(np.mean([z["holding_days"] for z in t])),2)}

def evaluate_symbol(symbol,df):
    tb=trades(df,BASE_BPS);ts=trades(df,STRESS_BPS)
    periods=[("2006_2012","2006-01-01","2012-12-31"),("2013_2019","2013-01-01","2019-12-31"),("2020_PLUS","2020-01-01",None)]
    cells=[]
    for name,a,e in periods:
        b=[z for z in tb if period(z["signal_date"],a,e)]
        s=[z for z in ts if period(z["signal_date"],a,e)]
        cells.append({"period":name,"baseline":metrics(b),"stress":metrics(s)})
    return {"symbol":symbol,"cells":cells}

def aggregate(results,period_name,key="baseline"):
    rows=[]
    for r in results:
        c=next(x for x in r["cells"] if x["period"]==period_name)
        # Recompute aggregate from summary-weighted values is unsafe; caller only uses per-cell pass counts.
        rows.append(c[key])
    eligible=[m for m in rows if m["trades"]>=8]
    pass_count=sum(1 for m in eligible if m["avg_return"] is not None and m["avg_return"]>0 and m["profit_factor"] is not None and m["profit_factor"]>=1.15)
    return {"eligible_symbols":len(eligible),"passing_symbols":pass_count}

def main():
    raw=download();results=[evaluate_symbol(s,raw[s]) for s in SYMBOLS if not raw[s].empty]
    period_names=["2006_2012","2013_2019","2020_PLUS"]
    gates={}
    for p in period_names:
        gates[p]={"baseline":aggregate(results,p,"baseline"),"stress":aggregate(results,p,"stress")}
    # Locked before results: broad-index RSI2 candidate requires at least 6/8 ETFs pass in each baseline era,
    # at least 5/8 pass 30bps stress in each era, and no era with fewer than 6 eligible ETFs.
    passed=all(gates[p]["baseline"]["eligible_symbols"]>=6 and gates[p]["baseline"]["passing_symbols"]>=6 and gates[p]["stress"]["passing_symbols"]>=5 for p in period_names)
    out={"strategy":"CONNORS_RSI2_INDEX_ETF","rules":"long when close>SMA200 and RSI2<10; next-open entry; exit next open after close>SMA5; no stop; non-overlapping per ETF",
         "base_bps":BASE_BPS,"stress_bps":STRESS_BPS,"results":results,"period_gates":gates,"status":"ROUND8_CANDIDATE" if passed else "REJECT",
         "limitations":["ETF histories are continuous but this is not a point-in-time stock-universe test","Yahoo adjusted daily bars are research-grade, not execution-grade","candidate requires portfolio simulation, timing perturbation, and Sharia-compatible retest before paper trading"]}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps({"status":out["status"],"period_gates":gates,"summary":[{"symbol":r["symbol"],"cells":r["cells"]} for r in results]},indent=2))

if __name__=="__main__":main()
