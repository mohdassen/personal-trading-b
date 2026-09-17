from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT=Path("data/risk_managed_round3.json")
START="2014-01-01"
COST_BPS=30.0
SYMBOLS=[
"AAPL","MSFT","NVDA","AMD","AMZN","GOOGL","META","TSLA","AVGO","ORCL","CRM","ADBE","NFLX","MU","QCOM","AMAT","LRCX","KLAC","CRWD","PANW","NOW","PLTR","XOM","CVX","COP","SLB","ABBV","JNJ","LLY","UNH","CAT","DE","GE","WMT","COST","HD","DIS","ROKU","TMUS","GILD","INTC","SNOW","DDOG","NET","MDB","SHOP","UBER","ABNB","BKNG","TGT","LOW","NKE","SBUX","MCD","ISRG","VRTX","REGN","PFE","BA","HON","RTX","CMCSA","SPOT","RBLX","MELI"]
STRATEGIES=["MARKET_TREND_10M","DUAL_MOMENTUM_12_1","TREND_PLUS_MOMENTUM"]

def norm(raw,s):
    if raw is None or raw.empty: return pd.DataFrame()
    try:
        if isinstance(raw.columns,pd.MultiIndex):
            if s in raw.columns.get_level_values(0): x=raw[s].copy()
            elif s in raw.columns.get_level_values(-1): x=raw.xs(s,axis=1,level=-1).copy()
            else: return pd.DataFrame()
        else: x=raw.copy()
        need=["Open","Close","Volume"]
        if not all(c in x.columns for c in need): return pd.DataFrame()
        x=x[need].dropna(subset=["Open","Close"]).copy()
        idx=pd.DatetimeIndex(x.index)
        if idx.tz is not None: idx=idx.tz_convert(None)
        x.index=idx
        return x.sort_index()
    except Exception:
        return pd.DataFrame()

def download():
    tickers=SYMBOLS+["SPY"]
    raw=yf.download(tickers,start=START,interval="1d",auto_adjust=True,progress=False,threads=True,group_by="ticker",timeout=30)
    out={}
    for s in tickers:
        x=norm(raw,s)
        if len(x)>=500: out[s]=x
        print(f"{s}: rows={len(x)}")
    return out

def mt(df):
    x=df.copy(); x["month"]=x.index.to_period("M")
    groups=list(x.groupby("month",sort=True))
    rows=[]
    for i,(m,g) in enumerate(groups):
        g=g.sort_index()
        rows.append({"month":m,"close":float(g.Close.iloc[-1]),"next_open":float(groups[i+1][1].sort_index().Open.iloc[0]) if i+1<len(groups) else np.nan})
    z=pd.DataFrame(rows).set_index("month").sort_index()
    z["ret1m"]=z.close.pct_change()
    z["mom12_1"]=z.close.shift(1)/z.close.shift(12)-1
    z["ret12"]=z.close/z.close.shift(12)-1
    z["sma10"]=z.close.rolling(10).mean()
    return z

def build(raw):
    tables={s:mt(raw[s]) for s in SYMBOLS if s in raw}
    spy=mt(raw["SPY"])
    rows=[]
    for s,t in tables.items():
        for m,r in t.iterrows():
            rows.append({"symbol":s,"month":m,"next_open":r.next_open,"close":r.close,"mom12_1":r.mom12_1})
    return tables,spy,pd.DataFrame(rows)

def nextret(t,m,cost_bps=COST_BPS):
    if m not in t.index:return None
    i=t.index.get_loc(m)
    if isinstance(i,slice) or i+1>=len(t):return None
    entry=float(t.iloc[i].next_open)
    if not math.isfinite(entry) or entry<=0:return None
    exitc=float(t.iloc[i+1].close)
    return exitc/entry-1-cost_bps/10000.0

def pick_momentum(g):
    q=g.dropna(subset=["mom12_1","next_open"])
    if len(q)<5:return []
    n=max(2,math.ceil(len(q)*0.20))
    return q.nlargest(n,"mom12_1").symbol.tolist()

def series_for(tables,spy,panel,symbols,strategy):
    q=panel[panel.symbol.isin(symbols)].copy()
    out={}
    for m,g in q.groupby("month",sort=True):
        if m not in spy.index: continue
        sp=spy.loc[m]
        active=True
        picks=g.symbol.tolist()
        if strategy=="MARKET_TREND_10M":
            active=bool(pd.notna(sp.sma10) and sp.close>sp.sma10)
        elif strategy=="DUAL_MOMENTUM_12_1":
            active=bool(pd.notna(sp.ret12) and sp.ret12>0)
            picks=pick_momentum(g)
        elif strategy=="TREND_PLUS_MOMENTUM":
            active=bool(pd.notna(sp.sma10) and sp.close>sp.sma10)
            picks=pick_momentum(g)
        if not active:
            out[m.to_timestamp(how="end")]=0.0
            continue
        rs=[nextret(tables[s],m) for s in picks if s in tables]
        rs=[r for r in rs if r is not None]
        if rs: out[m.to_timestamp(how="end")]=float(np.mean(rs))
    return pd.Series(out,dtype=float).sort_index()

def bench(tables,panel,symbols):
    q=panel[panel.symbol.isin(symbols)].copy(); out={}
    for m,g in q.groupby("month",sort=True):
        rs=[nextret(tables[s],m) for s in g.symbol.tolist() if s in tables]
        rs=[r for r in rs if r is not None]
        if rs: out[m.to_timestamp(how="end")]=float(np.mean(rs))
    return pd.Series(out,dtype=float).sort_index()

def maxdd(s):
    if s.empty:return 0.0
    w=(1+s).cumprod(); p=w.cummax()
    return float((1-w/p).max())

def stats(s):
    s=s.dropna()
    if len(s)<2:return {"months":len(s),"annual_return":None,"sharpe":None,"max_drawdown":None,"total_return":None}
    wealth=float((1+s).prod()); years=len(s)/12
    ann=wealth**(1/years)-1 if wealth>0 else -1
    sd=float(s.std(ddof=1)); sh=float(s.mean()/sd*math.sqrt(12)) if sd>1e-12 else None
    return {"months":len(s),"annual_return":round(ann,4),"sharpe":None if sh is None else round(sh,3),"max_drawdown":round(maxdd(s),4),"total_return":round(wealth-1,4)}

def cell_metrics(s,b,start,end=None):
    x=pd.concat([s.rename("s"),b.rename("b")],axis=1).dropna()
    x=x[x.index>=pd.Timestamp(start)]
    if end:x=x[x.index<=pd.Timestamp(end)]
    ss=stats(x.s); bb=stats(x.b)
    rel=float(((1+x.s)/(1+x.b)).prod()-1) if not x.empty else None
    return {"months":len(x),"strategy":ss,"benchmark":bb,"relative_wealth":None if rel is None else round(rel,4),
            "sharpe_delta":None if ss["sharpe"] is None or bb["sharpe"] is None else round(ss["sharpe"]-bb["sharpe"],3),
            "drawdown_improvement":None if ss["max_drawdown"] is None or bb["max_drawdown"] is None else round(bb["max_drawdown"]-ss["max_drawdown"],4)}

def folds(symbols,k=5):
    o=sorted(symbols); return [set(o[i::k]) for i in range(k)]

def evaluate(strategy,tables,spy,panel,available):
    periods=[("2016_2020","2016-01-01","2020-12-31"),("2021_2023","2021-01-01","2023-12-31"),("2024_PLUS","2024-01-01",None)]
    cells=[]
    for fi,syms in enumerate(folds(available)):
        s=series_for(tables,spy,panel,syms,strategy); b=bench(tables,panel,syms)
        for name,start,end in periods:
            cells.append({"fold":fi,"period":name,**cell_metrics(s,b,start,end)})
    valid=[c for c in cells if c["months"]>=24]
    sharpe_wins=sum(1 for c in valid if (c["sharpe_delta"] or -99)>0)
    dd_wins=sum(1 for c in valid if (c["drawdown_improvement"] or -99)>0)
    rel_wins=sum(1 for c in valid if (c["relative_wealth"] or -99)>0)
    recent=[c for c in valid if c["period"]=="2024_PLUS"]
    recent_sharpe=sum(1 for c in recent if (c["sharpe_delta"] or -99)>0)
    recent_dd=sum(1 for c in recent if (c["drawdown_improvement"] or -99)>0)
    med={}
    for p,_,_ in periods:
        vals=[c["sharpe_delta"] for c in valid if c["period"]==p and c["sharpe_delta"] is not None]
        med[p]=None if not vals else round(float(np.median(vals)),3)
    # Locked before results: candidate must improve risk-adjusted behavior broadly,
    # not merely win one recent period.
    passed=(len(valid)>=14 and sharpe_wins>=10 and dd_wins>=10 and rel_wins>=7 and recent_sharpe>=4 and recent_dd>=4
            and all(med[p] is not None and med[p]>0 for p,_,_ in periods))
    return {"strategy":strategy,"status":"ROUND3_ROBUST_CANDIDATE" if passed else "REJECT","valid_cells":len(valid),
            "sharpe_wins":sharpe_wins,"drawdown_wins":dd_wins,"relative_wealth_wins":rel_wins,
            "recent_sharpe_wins":recent_sharpe,"recent_drawdown_wins":recent_dd,
            "median_sharpe_delta_by_period":med,"cells":cells}

def main():
    raw=download()
    if "SPY" not in raw: raise RuntimeError("SPY missing")
    tables,spy,panel=build(raw); available=[s for s in SYMBOLS if s in tables]
    results=[evaluate(s,tables,spy,panel,available) for s in STRATEGIES]
    candidates=[r["strategy"] for r in results if r["status"]=="ROUND3_ROBUST_CANDIDATE"]
    out={"method":"published risk-managed round 3; fixed rules before result; monthly next-open fills; 5 symbol folds x 3 time regimes",
         "strategies":STRATEGIES,"downloaded_symbols":len(available),"results":results,"candidates":candidates,
         "overall":"ROUND3_CANDIDATE_FOUND" if candidates else "NO_ROUND3_CANDIDATE",
         "limitations":["current-survivor universe is biased","Yahoo is not institutional point-in-time data","cash return modeled as zero","candidate is research-only and not permission for paper/live trading"]}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps({"overall":out["overall"],"candidates":candidates,"summary":[{k:r[k] for k in ["strategy","status","sharpe_wins","drawdown_wins","relative_wealth_wins","recent_sharpe_wins","recent_drawdown_wins","median_sharpe_delta_by_period"]} for r in results]},indent=2))

if __name__=="__main__": main()
