from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT=Path("data/published_rotation_round7.json")
BASE_BPS=10.0
STRESS_BPS=30.0
START="2005-01-01"

GTAA=["VTV","MTUM","VBR","DWAS","EFA","EEM","IEF","IGOV","LQD","TLT","GSG","IAU","VNQ"]
COUNTRY=["EWA","EWC","EWG","EWH","EWJ","EWQ","EWS","EWU","EWT","EWW"]
PAIR=["SPY","AGG"]
ALL=sorted(set(GTAA+COUNTRY+PAIR))

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

def mt(df):
    x=df.copy();x["month"]=x.index.to_period("M");groups=list(x.groupby("month",sort=True));rows=[]
    for i,(m,g) in enumerate(groups):
        g=g.sort_index()
        rows.append({"month":m,"close":float(g.Close.iloc[-1]),"next_open":float(groups[i+1][1].sort_index().Open.iloc[0]) if i+1<len(groups) else np.nan})
    t=pd.DataFrame(rows).set_index("month").sort_index()
    for n in [1,3,6,12]:t[f"ret{n}"]=t.close/t.close.shift(n)-1
    t["sma10"]=t.close.rolling(10).mean()
    t["mom_avg"]=(t.ret1+t.ret3+t.ret6+t.ret12)/4.0
    return t

def nextret(t,m,bps):
    if m not in t.index:return None
    i=t.index.get_loc(m)
    if isinstance(i,slice) or i+1>=len(t):return None
    e=float(t.iloc[i].next_open)
    if not math.isfinite(e) or e<=0:return None
    return float(t.iloc[i+1].close)/e-1-bps/10000.0

def equal_weight(tables,symbols,bps):
    common=sorted(set.intersection(*[set(tables[s].index) for s in symbols]))
    out={}
    for m in common:
        rs=[nextret(tables[s],m,bps) for s in symbols];rs=[r for r in rs if r is not None]
        if len(rs)==len(symbols):out[m.to_timestamp(how="end")]=float(np.mean(rs))
    return pd.Series(out,dtype=float).sort_index()

def gtaaseries(tables,bps):
    common=sorted(set.intersection(*[set(tables[s].index) for s in GTAA]))
    out={}
    for m in common:
        q=[]
        for s in GTAA:
            r=tables[s].loc[m]
            if pd.notna(r.mom_avg) and pd.notna(r.sma10) and pd.notna(r.next_open):
                q.append((s,float(r.mom_avg),float(r.close)>float(r.sma10)))
        if len(q)<len(GTAA):continue
        ranked=sorted(q,key=lambda z:z[1],reverse=True)[:3]
        ret=0.0
        for s,_,above in ranked:
            if above:
                rr=nextret(tables[s],m,bps)
                if rr is not None:ret += (1/3)*rr
        out[m.to_timestamp(how="end")]=float(ret)
    return pd.Series(out,dtype=float).sort_index()

def countryseries(tables,bps):
    common=sorted(set.intersection(*[set(tables[s].index) for s in COUNTRY]))
    out={}
    for m in common:
        q=[(s,float(tables[s].loc[m,"ret12"])) for s in COUNTRY if pd.notna(tables[s].loc[m,"ret12"]) and pd.notna(tables[s].loc[m,"next_open"])]
        if len(q)<len(COUNTRY):continue
        picks=[s for s,_ in sorted(q,key=lambda z:z[1],reverse=True)[:3]]
        rs=[nextret(tables[s],m,bps) for s in picks];rs=[r for r in rs if r is not None]
        if len(rs)==3:out[m.to_timestamp(how="end")]=float(np.mean(rs))
    return pd.Series(out,dtype=float).sort_index()

def paired(tables,bps):
    common=sorted(set.intersection(*[set(tables[s].index) for s in PAIR]))
    out={};current=None
    for m in common:
        # Rebalance once per quarter using trailing 3-month performance known at this month-end.
        if m.month in [3,6,9,12]:
            vals=[(s,tables[s].loc[m,"ret3"]) for s in PAIR]
            if all(pd.notna(v) for _,v in vals):current=max(vals,key=lambda z:z[1])[0]
        if current is None or pd.isna(tables[current].loc[m,"next_open"]):continue
        r=nextret(tables[current],m,bps)
        if r is not None:out[m.to_timestamp(how="end")]=float(r)
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

def cut(s,a,e=None):
    x=s[s.index>=pd.Timestamp(a)]
    if e:x=x[x.index<=pd.Timestamp(e)]
    return stats(x)

def eval_cells(name,s,b,st,periods):
    cells=[]
    for n,a,e in periods:
        sm=cut(s,a,e);bm=cut(b,a,e);stm=cut(st,a,e)
        cells.append({"period":n,"strategy":sm,"benchmark":bm,"stress":stm,
                      "return_win":sm["annual_return"] is not None and bm["annual_return"] is not None and sm["annual_return"]>bm["annual_return"],
                      "sharpe_win":sm["sharpe"] is not None and bm["sharpe"] is not None and sm["sharpe"]>bm["sharpe"],
                      "drawdown_win":sm["max_drawdown"] is not None and bm["max_drawdown"] is not None and sm["max_drawdown"]<bm["max_drawdown"],
                      "stress_positive":stm["annual_return"] is not None and stm["annual_return"]>0})
    rw=sum(c["return_win"] for c in cells);sw=sum(c["sharpe_win"] for c in cells);dw=sum(c["drawdown_win"] for c in cells);sp=sum(c["stress_positive"] for c in cells)
    recent=cells[-1]["strategy"]
    # Locked before result: robust candidate needs broad benchmark improvement, not just positive return.
    passed=(rw>=2 and sw>=2 and dw>=2 and sp==3 and recent["sharpe"] is not None and recent["sharpe"]>=0.60 and recent["max_drawdown"] is not None and recent["max_drawdown"]<=0.25)
    return {"strategy":name,"status":"ROUND7_CANDIDATE" if passed else "REJECT","return_wins":rw,"sharpe_wins":sw,"drawdown_wins":dw,"stress_positive_periods":sp,"cells":cells}

def main():
    raw=download();tables={s:mt(raw[s]) for s in ALL}
    g=gtaaseries(tables,BASE_BPS);gst=gtaaseries(tables,STRESS_BPS);gb=equal_weight(tables,GTAA,BASE_BPS)
    c=countryseries(tables,BASE_BPS);cst=countryseries(tables,STRESS_BPS);cb=equal_weight(tables,COUNTRY,BASE_BPS)
    p=paired(tables,BASE_BPS);pst=paired(tables,STRESS_BPS);pb=equal_weight(tables,PAIR,BASE_BPS)
    results=[
      eval_cells("GTAA_AGGRESSIVE_13",g,gb,gst,[("2015_2018","2015-01-01","2018-12-31"),("2019_2022","2019-01-01","2022-12-31"),("2023_PLUS","2023-01-01",None)]),
      eval_cells("COUNTRY_MOMENTUM_12M",c,cb,cst,[("2008_2013","2008-01-01","2013-12-31"),("2014_2019","2014-01-01","2019-12-31"),("2020_PLUS","2020-01-01",None)]),
      eval_cells("PAIRED_SWITCHING_SPY_AGG",p,pb,pst,[("2008_2013","2008-01-01","2013-12-31"),("2014_2019","2014-01-01","2019-12-31"),("2020_PLUS","2020-01-01",None)])
    ]
    cand=[r["strategy"] for r in results if r["status"]=="ROUND7_CANDIDATE"]
    out={"method":"published rotation round 7; fixed published rules; disjoint regimes; cost stress","base_bps":BASE_BPS,"stress_bps":STRESS_BPS,"results":results,"candidates":cand,"overall":"ROUND7_CANDIDATE_FOUND" if cand else "NO_ROUND7_CANDIDATE","limitations":["GTAA fixed ETF universe starts in 2014 due ETF inception dates","country and bond ETFs are not Sharia-screened","candidate is research-only until independent timing/universe validation"]}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps({"overall":out["overall"],"candidates":cand,"summary":[{k:v for k,v in r.items() if k!="cells"} for r in results]},indent=2))
if __name__=="__main__":main()
