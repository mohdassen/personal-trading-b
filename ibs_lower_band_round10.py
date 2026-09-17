from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT=Path("data/ibs_lower_band_round10.json")
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

def simulate(df,bps):
    x=df.copy()
    x["range25"]=(x.High-x.Low).rolling(25).mean()
    x["high10"]=x.High.rolling(10).max()
    x["lower_band"]=x.high10-2.5*x.range25
    x["ibs"]=(x.Close-x.Low)/(x.High-x.Low).replace(0,np.nan)
    x["sma200"]=x.Close.rolling(200).mean()
    x["prev_high"]=x.High.shift(1)
    out=[];i=200;n=len(x)
    while i<n-2:
        r=x.iloc[i]
        # Published IBS/lower-band mean reversion setup.
        if not (pd.notna(r.lower_band) and pd.notna(r.ibs) and r.Close<r.lower_band and r.ibs<0.30 and r.Close>r.sma200):
            i+=1;continue
        ei=i+1;entry=float(x.iloc[ei].Open)
        if not math.isfinite(entry) or entry<=0:i+=1;continue
        exit_i=None;exitp=None
        for j in range(ei,n-1):
            b=x.iloc[j]
            # Exit only after today's close is known; fill next open.
            if (pd.notna(b.prev_high) and b.Close>b.prev_high) or (pd.notna(b.sma200) and b.Close<b.sma200):
                exit_i=j+1;exitp=float(x.iloc[exit_i].Open);break
        if exit_i is None:
            exit_i=n-1;exitp=float(x.iloc[exit_i].Close)
        net=exitp/entry-1-bps/10000.0
        out.append({"signal_date":str(x.index[i].date()),"entry_date":str(x.index[ei].date()),"exit_date":str(x.index[exit_i].date()),"net_return":net,"holding_days":exit_i-ei})
        i=exit_i+1
    return out

def inperiod(t,a,e=None):
    d=pd.Timestamp(t["signal_date"]);return d>=pd.Timestamp(a) and (e is None or d<=pd.Timestamp(e))

def metrics(t):
    if not t:return {"trades":0,"win_rate":None,"avg_return":None,"profit_factor":None,"median_return":None,"avg_hold_days":None}
    a=np.array([z["net_return"] for z in t],dtype=float);w=a[a>0];l=a[a<0]
    pf=float(w.sum()/abs(l.sum())) if len(l) else 999.0
    return {"trades":len(a),"win_rate":round(float((a>0).mean()),3),"avg_return":round(float(a.mean()),5),"profit_factor":round(pf,3),"median_return":round(float(np.median(a)),5),"avg_hold_days":round(float(np.mean([z["holding_days"] for z in t])),2)}

def eval_symbol(s,df):
    tb=simulate(df,BASE_BPS);ts=simulate(df,STRESS_BPS)
    periods=[("2006_2012","2006-01-01","2012-12-31"),("2013_2019","2013-01-01","2019-12-31"),("2020_PLUS","2020-01-01",None)]
    return {"symbol":s,"cells":[{"period":n,"baseline":metrics([z for z in tb if inperiod(z,a,e)]),"stress":metrics([z for z in ts if inperiod(z,a,e)])} for n,a,e in periods]}

def gate(results,p,key):
    ms=[next(c for c in r["cells"] if c["period"]==p)[key] for r in results]
    eligible=[m for m in ms if m["trades"]>=8]
    passing=sum(1 for m in eligible if m["avg_return"] is not None and m["avg_return"]>0 and m["profit_factor"] is not None and m["profit_factor"]>=1.15)
    return {"eligible_symbols":len(eligible),"passing_symbols":passing}

def main():
    raw=download();results=[eval_symbol(s,raw[s]) for s in SYMBOLS if not raw[s].empty]
    periods=["2006_2012","2013_2019","2020_PLUS"]
    gates={p:{"baseline":gate(results,p,"baseline"),"stress":gate(results,p,"stress")} for p in periods}
    # Locked before results, same breadth/cost gate as Round 8.
    passed=all(gates[p]["baseline"]["eligible_symbols"]>=6 and gates[p]["baseline"]["passing_symbols"]>=6 and gates[p]["stress"]["passing_symbols"]>=5 for p in periods)
    out={"strategy":"IBS_LOWER_BAND_MEAN_REVERSION","rules":"close below 10d-high minus 2.5x 25d avg range, IBS<0.30, close>SMA200; next-open entry; exit next open after close>prior high or close<SMA200","base_bps":BASE_BPS,"stress_bps":STRESS_BPS,"results":results,"period_gates":gates,"status":"ROUND10_CANDIDATE" if passed else "REJECT","limitations":["daily adjusted Yahoo bars are research-grade","published rule reproduction uses conservative next-open fills","candidate would require portfolio overlap/risk simulation and forward validation"]}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps({"status":out["status"],"period_gates":gates,"summary":[{"symbol":r["symbol"],"cells":r["cells"]} for r in results]},indent=2))
if __name__=="__main__":main()
