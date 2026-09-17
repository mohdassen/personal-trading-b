from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT=Path("data/qqq_ibs_source_validation_round13.json")
START="1999-01-01"
BASE_BPS=10.0
STRESS_BPS=30.0
SEED=20260918

def norm(raw):
    x=raw.copy()
    if isinstance(x.columns,pd.MultiIndex):
        x.columns=x.columns.get_level_values(0)
    x=x[["Open","High","Low","Close"]].dropna().copy()
    idx=pd.DatetimeIndex(x.index)
    if idx.tz is not None:idx=idx.tz_convert(None)
    x.index=idx
    return x.sort_index()

def prep(df):
    x=df.copy()
    x["range25"]=(x.High-x.Low).rolling(25).mean()
    x["high10"]=x.High.rolling(10).max()
    x["lower_band"]=x.high10-2.5*x.range25
    x["ibs"]=(x.Close-x.Low)/(x.High-x.Low).replace(0,np.nan)
    x["sma300"]=x.Close.rolling(300).mean()
    x["prev_high"]=x.High.shift(1)
    return x

def simulate(x,bps,extra_entry_delay=0):
    out=[];i=300;n=len(x)
    while i<n-3:
        r=x.iloc[i]
        if not (pd.notna(r.lower_band) and pd.notna(r.ibs) and r.Close<r.lower_band and r.ibs<0.30):
            i+=1;continue
        ei=i+1+extra_entry_delay
        if ei>=n-1:break
        entry=float(x.iloc[ei].Open)
        if not math.isfinite(entry) or entry<=0:i+=1;continue
        exit_i=None;exitp=None
        for j in range(ei,n-1):
            b=x.iloc[j]
            if (pd.notna(b.prev_high) and b.Close>b.prev_high) or (pd.notna(b.sma300) and b.Close<b.sma300):
                exit_i=j+1;exitp=float(x.iloc[exit_i].Open);break
        if exit_i is None:
            exit_i=n-1;exitp=float(x.iloc[exit_i].Close)
        net=exitp/entry-1-bps/10000.0
        out.append({"signal_date":str(x.index[i].date()),"entry_date":str(x.index[ei].date()),"exit_date":str(x.index[exit_i].date()),"net_return":net,"holding_days":exit_i-ei})
        i=exit_i+1
    return out

def metrics(t):
    if not t:return {"trades":0,"win_rate":None,"avg_return":None,"profit_factor":None,"median_return":None,"compounded_return":None,"max_drawdown":None,"avg_hold_days":None}
    a=np.array([z["net_return"] for z in t],dtype=float);w=a[a>0];l=a[a<0]
    pf=float(w.sum()/abs(l.sum())) if len(l) else 999.0
    wealth=np.cumprod(1+a);peak=np.maximum.accumulate(np.r_[1.0,wealth]);wealth2=np.r_[1.0,wealth]
    dd=float(np.max(1-wealth2/peak))
    return {"trades":len(a),"win_rate":round(float((a>0).mean()),3),"avg_return":round(float(a.mean()),5),
            "profit_factor":round(pf,3),"median_return":round(float(np.median(a)),5),
            "compounded_return":round(float(wealth[-1]-1),4),"max_drawdown":round(dd,4),
            "avg_hold_days":round(float(np.mean([z["holding_days"] for z in t])),2)}

def bootstrap_ci(t):
    a=np.array([z["net_return"] for z in t],dtype=float)
    if len(a)<30:return [None,None]
    rng=np.random.default_rng(SEED);means=[]
    for _ in range(5000):
        means.append(float(rng.choice(a,size=len(a),replace=True).mean()))
    lo,hi=np.quantile(means,[0.025,0.975])
    return [round(float(lo),5),round(float(hi),5)]

def between(t,a,e=None):
    return [z for z in t if pd.Timestamp(z["signal_date"])>=pd.Timestamp(a) and (e is None or pd.Timestamp(z["signal_date"])<=pd.Timestamp(e))]

def strip_best(t,n=5):
    return sorted(t,key=lambda z:z["net_return"],reverse=True)[n:]

def annualized_from_trades(t,start,end):
    m=metrics(t);years=(pd.Timestamp(end)-pd.Timestamp(start)).days/365.25
    if not m["trades"] or years<=0:return None
    wealth=1+m["compounded_return"]
    return round(float(wealth**(1/years)-1),4) if wealth>0 else -1.0

def main():
    raw=yf.download("QQQ",start=START,interval="1d",auto_adjust=True,progress=False,threads=False,timeout=30)
    x=prep(norm(raw));print("QQQ rows",len(x))
    base=simulate(x,BASE_BPS,0)
    stress=simulate(x,STRESS_BPS,0)
    delayed=simulate(x,STRESS_BPS,1)
    eras=[("2000_2005","2000-01-01","2005-12-31"),("2006_2010","2006-01-01","2010-12-31"),
          ("2011_2015","2011-01-01","2015-12-31"),("2016_2020","2016-01-01","2020-12-31"),
          ("2021_PLUS","2021-01-01",None)]
    cells=[]
    for n,a,e in eras:
        cells.append({"period":n,"baseline":metrics(between(base,a,e)),"stress":metrics(between(stress,a,e)),"delayed_stress":metrics(between(delayed,a,e))})
    agg=metrics(stress);delayagg=metrics(delayed);stripped=metrics(strip_best(stress,5));ci=bootstrap_ci(stress)
    passing_eras=sum(1 for c in cells if c["stress"]["trades"]>=20 and c["stress"]["avg_return"] is not None and c["stress"]["avg_return"]>0 and c["stress"]["profit_factor"] is not None and c["stress"]["profit_factor"]>=1.20)
    recent=cells[-1]["stress"]
    annualized=annualized_from_trades(stress,"2000-01-01","2026-09-17")
    # Locked source-specific validation. QQQ is the instrument explicitly used in the published strategy write-up.
    passed=(passing_eras>=4 and agg["profit_factor"]>=1.40 and agg["avg_return"]>0 and ci[0] is not None and ci[0]>0
            and delayagg["profit_factor"]>=1.10 and delayagg["avg_return"]>0
            and stripped["avg_return"]>0 and stripped["profit_factor"]>=1.15
            and agg["max_drawdown"]<=0.20 and recent["profit_factor"]>=1.25 and recent["avg_return"]>0
            and annualized is not None and annualized>=0.03)
    out={"strategy":"QQQ_IBS_LOWER_BAND_SOURCE_SPEC","status":"ROUND13_CANDIDATE" if passed else "REJECT",
         "rules":"exact published IBS/lower-band entry; 300d dynamic exit; conservative next-open execution",
         "base_bps":BASE_BPS,"stress_bps":STRESS_BPS,"cells":cells,"aggregate_stress":agg,"bootstrap95_avg_return":ci,
         "one_extra_day_entry_delay_stress":delayagg,"strip_best_5_stress":stripped,"passing_stress_eras":passing_eras,
         "annualized_compounded_stress_since_2000":annualized,
         "limitations":["QQQ is source-specified rather than a new unseen symbol, so this is strong replication evidence but not a clean untouched instrument holdout","Yahoo adjusted daily bars are research-grade","paper/forward validation is still required before real money"]}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps(out,indent=2))
if __name__=="__main__":main()
