from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT=Path("data/published_tactical_etf_round6.json")
START="2006-01-01"
BASE_BPS=10.0
STRESS_BPS=30.0
RISK=["SPY","EFA","VNQ","DBC"]
DEFENSIVE="IEF"
ALL=RISK+[DEFENSIVE]

def norm(raw,s):
    if raw is None or raw.empty:return pd.DataFrame()
    try:
        if isinstance(raw.columns,pd.MultiIndex):
            if s in raw.columns.get_level_values(0):x=raw[s].copy()
            elif s in raw.columns.get_level_values(-1):x=raw.xs(s,axis=1,level=-1).copy()
            else:return pd.DataFrame()
        else:x=raw.copy()
        if not all(c in x.columns for c in ["Open","Close"]):return pd.DataFrame()
        x=x[["Open","Close"]].dropna().copy()
        idx=pd.DatetimeIndex(x.index)
        if idx.tz is not None:idx=idx.tz_convert(None)
        x.index=idx
        return x.sort_index()
    except Exception:return pd.DataFrame()

def download():
    raw=yf.download(ALL,start=START,interval="1d",auto_adjust=True,progress=False,threads=True,group_by="ticker",timeout=30)
    out={}
    for s in ALL:
        x=norm(raw,s);out[s]=x;print(s,len(x))
    return out

def month_table(df):
    x=df.copy();x["month"]=x.index.to_period("M");groups=list(x.groupby("month",sort=True));rows=[]
    for i,(m,g) in enumerate(groups):
        g=g.sort_index()
        rows.append({"month":m,"close":float(g.Close.iloc[-1]),
                     "next_open":float(groups[i+1][1].sort_index().Open.iloc[0]) if i+1<len(groups) else np.nan})
    t=pd.DataFrame(rows).set_index("month").sort_index()
    t["mom12"]=t.close/t.close.shift(12)-1.0
    t["sma10"]=t.close.rolling(10).mean()
    return t

def nextret(t,m,bps):
    if m not in t.index:return None
    i=t.index.get_loc(m)
    if isinstance(i,slice) or i+1>=len(t):return None
    e=float(t.iloc[i].next_open)
    if not math.isfinite(e) or e<=0:return None
    return float(t.iloc[i+1].close)/e-1-bps/10000.0

def baseline(tables,bps):
    months=sorted(set.intersection(*[set(tables[s].index) for s in RISK]))
    out={}
    for m in months:
        rs=[nextret(tables[s],m,bps) for s in RISK]
        rs=[r for r in rs if r is not None]
        if len(rs)==len(RISK):out[m.to_timestamp(how="end")]=float(np.mean(rs))
    return pd.Series(out,dtype=float).sort_index()

def strategy_series(tables,name,bps):
    months=sorted(set.intersection(*[set(tables[s].index) for s in ALL]))
    out={}
    for m in months:
        if any(pd.isna(tables[s].loc[m,"next_open"]) for s in ALL):continue
        ret=0.0
        if name=="FABER_GTAA_10M":
            # Published GTAA-style rule: fixed 20% sleeve per asset; risk sleeves below 10m SMA go to cash.
            # We use the five canonical cross-asset ETFs, each with 20% capital.
            for s in ALL:
                active=pd.notna(tables[s].loc[m,"sma10"]) and tables[s].loc[m,"close"]>tables[s].loc[m,"sma10"]
                if active:
                    r=nextret(tables[s],m,bps)
                    if r is not None:ret += 0.20*r
        elif name=="GLOBAL_EQUITY_DUAL_MOMENTUM":
            # Classic GEM-style adaptation: choose stronger of US/international equities;
            # if its 12m momentum is non-positive, rotate to aggregate defensive bond ETF.
            pair=["SPY","EFA"]
            vals=[(s,tables[s].loc[m,"mom12"]) for s in pair]
            if any(pd.isna(v) for _,v in vals):continue
            best,score=max(vals,key=lambda z:z[1])
            target=best if score>0 else DEFENSIVE
            r=nextret(tables[target],m,bps)
            if r is not None:ret=r
        elif name=="ABSOLUTE_MOMENTUM_5":
            # Each of five assets receives 20% only when trailing 12m return is positive; otherwise sleeve stays cash.
            for s in ALL:
                score=tables[s].loc[m,"mom12"]
                if pd.notna(score) and score>0:
                    r=nextret(tables[s],m,bps)
                    if r is not None:ret += 0.20*r
        else:
            continue
        out[m.to_timestamp(how="end")]=float(ret)
    return pd.Series(out,dtype=float).sort_index()

def maxdd(s):
    if s.empty:return 0.0
    w=(1+s).cumprod();p=w.cummax();return float((1-w/p).max())

def stats(s):
    s=s.dropna()
    if len(s)<2:return {"months":len(s),"annual_return":None,"sharpe":None,"max_drawdown":None,"total_return":None}
    wealth=float((1+s).prod());years=len(s)/12
    ann=wealth**(1/years)-1 if wealth>0 else -1
    sd=float(s.std(ddof=1));sh=float(s.mean()/sd*math.sqrt(12)) if sd>1e-12 else None
    return {"months":len(s),"annual_return":round(ann,4),"sharpe":None if sh is None else round(sh,3),"max_drawdown":round(maxdd(s),4),"total_return":round(wealth-1,4)}

def cut(s,start,end=None):
    x=s[s.index>=pd.Timestamp(start)]
    if end:x=x[x.index<=pd.Timestamp(end)]
    return stats(x)

def evaluate(tables,name):
    s=strategy_series(tables,name,BASE_BPS);st=strategy_series(tables,name,STRESS_BPS);b=baseline(tables,BASE_BPS)
    periods=[("2008_2013","2008-01-01","2013-12-31"),("2014_2019","2014-01-01","2019-12-31"),("2020_PLUS","2020-01-01",None)]
    cells=[]
    for n,a,e in periods:
        sm=cut(s,a,e);bm=cut(b,a,e);stm=cut(st,a,e)
        cells.append({"period":n,"strategy":sm,"benchmark":bm,"stress":stm,
                      "return_positive":bool(sm["annual_return"] is not None and sm["annual_return"]>0),
                      "sharpe_win":bool(sm["sharpe"] is not None and bm["sharpe"] is not None and sm["sharpe"]>bm["sharpe"]),
                      "drawdown_win":bool(sm["max_drawdown"] is not None and bm["max_drawdown"] is not None and sm["max_drawdown"]<bm["max_drawdown"]),
                      "stress_positive":bool(stm["annual_return"] is not None and stm["annual_return"]>0)})
    pos=sum(c["return_positive"] for c in cells);sh=sum(c["sharpe_win"] for c in cells);dd=sum(c["drawdown_win"] for c in cells);sp=sum(c["stress_positive"] for c in cells)
    recent=cells[-1]["strategy"]
    # Locked before seeing results: for a risk-managed published strategy, candidate means
    # positive return in every regime, Sharpe improvement in >=2/3, drawdown improvement in all 3,
    # positive under 30bps stress in all 3, and recent Sharpe >=0.70.
    passed=(pos==3 and sh>=2 and dd==3 and sp==3 and recent["sharpe"] is not None and recent["sharpe"]>=0.70)
    return {"strategy":name,"status":"ROUND6_CANDIDATE" if passed else "REJECT","positive_periods":pos,
            "sharpe_wins":sh,"drawdown_wins":dd,"stress_positive_periods":sp,"cells":cells}

def main():
    raw=download();tables={s:month_table(raw[s]) for s in ALL}
    names=["FABER_GTAA_10M","GLOBAL_EQUITY_DUAL_MOMENTUM","ABSOLUTE_MOMENTUM_5"]
    results=[evaluate(tables,n) for n in names]
    cand=[r["strategy"] for r in results if r["status"]=="ROUND6_CANDIDATE"]
    out={"method":"published tactical ETF round 6; fixed rules; disjoint regimes; cost stress","base_bps":BASE_BPS,"stress_bps":STRESS_BPS,
         "results":results,"candidates":cand,"overall":"ROUND6_CANDIDATE_FOUND" if cand else "NO_ROUND6_CANDIDATE",
         "limitations":["cash yield is modeled as zero, conservative for cash sleeves","fixed ETF set does not establish Sharia compliance","candidate would require timing-fragility and alternate-universe validation before paper trading"]}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps({"overall":out["overall"],"candidates":cand,"summary":[{k:v for k,v in r.items() if k!="cells"} for r in results]},indent=2))

if __name__=="__main__":main()
