from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT=Path("data/etf_published_round5.json")
START="2006-01-01"
BASE_BPS=10.0
STRESS_BPS=20.0

UNIVERSES={
    "SECTOR_MOMENTUM":["XLB","XLE","XLF","XLI","XLK","XLP","XLU","XLV","XLY"],
    "ASSET_CLASS_MOMENTUM":["SPY","EFA","IEF","VNQ","DBC"],
}
ALL=sorted(set(sum(UNIVERSES.values(),[])+["SPY"]))

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
        rows.append({"month":m,"close":float(g.Close.iloc[-1]),"first_open":float(g.Open.iloc[0]),
                     "next_open":float(groups[i+1][1].sort_index().Open.iloc[0]) if i+1<len(groups) else np.nan})
    t=pd.DataFrame(rows).set_index("month").sort_index()
    t["mom12"]=t.close/t.close.shift(12)-1
    return t

def next_month_return(t,m,bps):
    if m not in t.index:return None
    i=t.index.get_loc(m)
    if isinstance(i,slice) or i+1>=len(t):return None
    e=float(t.iloc[i].next_open)
    if not math.isfinite(e) or e<=0:return None
    return float(t.iloc[i+1].close)/e-1-bps/10000.0

def rotation(tables,symbols,bps,top_n=3):
    months=sorted(set.intersection(*[set(tables[s].index) for s in symbols]))
    strat={};bench={}
    for m in months:
        q=[(s,float(tables[s].loc[m,"mom12"])) for s in symbols if pd.notna(tables[s].loc[m,"mom12"]) and pd.notna(tables[s].loc[m,"next_open"])]
        if len(q)<len(symbols):continue
        picks=[s for s,_ in sorted(q,key=lambda z:z[1],reverse=True)[:top_n]]
        sr=[next_month_return(tables[s],m,bps) for s in picks]
        br=[next_month_return(tables[s],m,bps) for s in symbols]
        sr=[x for x in sr if x is not None];br=[x for x in br if x is not None]
        if sr and br:
            dt=m.to_timestamp(how="end");strat[dt]=float(np.mean(sr));bench[dt]=float(np.mean(br))
    return pd.Series(strat,dtype=float).sort_index(),pd.Series(bench,dtype=float).sort_index()

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

def slice_stats(s,start,end=None):
    x=s[s.index>=pd.Timestamp(start)]
    if end:x=x[x.index<=pd.Timestamp(end)]
    return stats(x)

def eval_rotation(name,tables):
    symbols=UNIVERSES[name]
    s,b=rotation(tables,symbols,BASE_BPS,3)
    ss,bs=rotation(tables,symbols,STRESS_BPS,3)
    periods=[("2008_2013","2008-01-01","2013-12-31"),("2014_2019","2014-01-01","2019-12-31"),("2020_PLUS","2020-01-01",None)]
    cells=[]
    for n,a,e in periods:
        sm=slice_stats(s,a,e);bm=slice_stats(b,a,e);stm=slice_stats(ss,a,e)
        cells.append({"period":n,"strategy":sm,"benchmark":bm,"stress":stm,
                      "return_win":bool(sm["annual_return"] is not None and bm["annual_return"] is not None and sm["annual_return"]>bm["annual_return"]),
                      "sharpe_win":bool(sm["sharpe"] is not None and bm["sharpe"] is not None and sm["sharpe"]>bm["sharpe"]),
                      "drawdown_win":bool(sm["max_drawdown"] is not None and bm["max_drawdown"] is not None and sm["max_drawdown"]<bm["max_drawdown"])})
    retwins=sum(c["return_win"] for c in cells);shwins=sum(c["sharpe_win"] for c in cells);ddwins=sum(c["drawdown_win"] for c in cells)
    stresspos=sum(1 for c in cells if c["stress"]["annual_return"] is not None and c["stress"]["annual_return"]>0)
    passed=retwins>=2 and shwins>=2 and ddwins>=2 and stresspos==3 and cells[-1]["strategy"]["sharpe"] is not None and cells[-1]["strategy"]["sharpe"]>=0.6
    return {"strategy":name,"status":"ROUND5_CANDIDATE" if passed else "REJECT","return_wins":retwins,"sharpe_wins":shwins,"drawdown_wins":ddwins,"stress_positive_periods":stresspos,"cells":cells}

def turn_of_month(df,bps):
    x=df.copy();x["month"]=x.index.to_period("M");out={}
    groups=list(x.groupby("month",sort=True))
    # Approximate QC logic: enter on the final trading day's open, hold through first 3 trading days of next month, exit at close of day 3.
    for i,(m,g) in enumerate(groups[:-1]):
        g=g.sort_index();ng=groups[i+1][1].sort_index()
        if len(ng)<3:continue
        entry=float(g.Open.iloc[-1]);exitp=float(ng.Close.iloc[2])
        if entry>0:
            out[ng.index[2]]=exitp/entry-1-bps/10000.0
    return pd.Series(out,dtype=float).sort_index()

def eval_turn(df):
    s=turn_of_month(df,BASE_BPS);st=turn_of_month(df,STRESS_BPS)
    # One observation per month; annualize as monthly strategy returns.
    periods=[("2008_2013","2008-01-01","2013-12-31"),("2014_2019","2014-01-01","2019-12-31"),("2020_PLUS","2020-01-01",None)]
    cells=[]
    for n,a,e in periods:
        sm=slice_stats(s,a,e);stm=slice_stats(st,a,e);cells.append({"period":n,"strategy":sm,"stress":stm})
    passed=all(c["strategy"]["annual_return"] is not None and c["strategy"]["annual_return"]>0.03 and c["strategy"]["sharpe"] is not None and c["strategy"]["sharpe"]>0.5 and c["strategy"]["max_drawdown"]<0.20 and c["stress"]["annual_return"]>0 for c in cells)
    return {"strategy":"TURN_OF_MONTH_SPY","status":"ROUND5_CANDIDATE" if passed else "REJECT","cells":cells}

def main():
    raw=download();tables={s:month_table(raw[s]) for s in ALL if not raw[s].empty}
    results=[eval_rotation("SECTOR_MOMENTUM",tables),eval_rotation("ASSET_CLASS_MOMENTUM",tables),eval_turn(raw["SPY"])]
    cand=[r["strategy"] for r in results if r["status"]=="ROUND5_CANDIDATE"]
    out={"method":"published ETF strategies; fixed rules before results; three disjoint time regimes; cost stress","base_bps":BASE_BPS,"stress_bps":STRESS_BPS,"results":results,"candidates":cand,"overall":"ROUND5_CANDIDATE_FOUND" if cand else "NO_ROUND5_CANDIDATE","limitations":["ETF universes are fixed current symbols, but they have long continuous histories","ETF candidates may not satisfy the user's Sharia preference and would require a separate Sharia-compliant universe retest","candidate is research-only, not paper/live approval"]}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps({"overall":out["overall"],"candidates":cand,"summary":[{k:v for k,v in r.items() if k!="cells"} for r in results]},indent=2))

if __name__=="__main__":main()
