from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT=Path("data/qqq_ibs_portfolio_round14.json")
START="1999-01-01"
SEED=20260918

def norm(raw):
    x=raw.copy()
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x[["Open","High","Low","Close"]].dropna().copy()
    idx=pd.DatetimeIndex(x.index)
    if idx.tz is not None: idx=idx.tz_convert(None)
    x.index=idx
    return x.sort_index()

def prep(x):
    x=x.copy()
    x["range25"]=(x.High-x.Low).rolling(25).mean()
    x["high10"]=x.High.rolling(10).max()
    x["lower_band"]=x.high10-2.5*x.range25
    x["ibs"]=(x.Close-x.Low)/(x.High-x.Low).replace(0,np.nan)
    x["sma300"]=x.Close.rolling(300).mean()
    x["prev_high"]=x.High.shift(1)
    return x

def signals(x,entry_delay=0):
    out=[];i=300;n=len(x)
    while i<n-3:
        r=x.iloc[i]
        if not(pd.notna(r.lower_band) and pd.notna(r.ibs) and r.Close<r.lower_band and r.ibs<0.30):
            i+=1;continue
        ei=i+1+entry_delay
        if ei>=n-1:break
        entry=float(x.iloc[ei].Open)
        if not math.isfinite(entry) or entry<=0:i+=1;continue
        xi=None
        for j in range(ei,n-1):
            b=x.iloc[j]
            if (pd.notna(b.prev_high) and b.Close>b.prev_high) or (pd.notna(b.sma300) and b.Close<b.sma300):
                xi=j+1;break
        if xi is None:xi=n-1
        exitp=float(x.iloc[xi].Open if xi<n-1 else x.iloc[xi].Close)
        out.append({"signal_i":i,"entry_i":ei,"exit_i":xi,"entry_date":x.index[ei],"exit_date":x.index[xi],
                    "gross":exitp/entry-1.0,"entry":entry,"exit":exitp})
        i=xi+1
    return out

def trade_returns(trades,cost_bps,slip_bps_each=0):
    # roundtrip fixed cost + adverse slippage on both entry and exit
    drag=cost_bps/10000.0 + 2*slip_bps_each/10000.0
    return np.array([t["gross"]-drag for t in trades],dtype=float)

def maxdd_monthly(returns):
    w=(1+returns).cumprod();p=w.cummax()
    return float((1-w/p).max()) if len(w) else 0.0

def portfolio_path(x,trades,allocation,cost_bps,slip_each=0):
    # Real cash portfolio: only 'allocation' of equity enters each signal; rest remains cash.
    # No leverage, one QQQ position at a time because signal engine is non-overlapping.
    equity=1.0;peak=1.0;maxdd=0.0;rets=[]
    drag=cost_bps/10000.0+2*slip_each/10000.0
    for t in trades:
        r=t["gross"]-drag
        pr=allocation*r
        equity*=1+pr
        peak=max(peak,equity);maxdd=max(maxdd,1-equity/peak);rets.append(pr)
    years=(x.index[-1]-x.index[0]).days/365.25
    cagr=equity**(1/years)-1 if years>0 else None
    a=np.array(rets,dtype=float)
    sd=float(a.std(ddof=1)) if len(a)>1 else 0
    pf=float(a[a>0].sum()/abs(a[a<0].sum())) if np.any(a<0) else 999
    return {"trades":len(a),"allocation":allocation,"ending_wealth":round(equity,4),"cagr":round(float(cagr),4),
            "max_drawdown":round(maxdd,4),"avg_portfolio_return_per_trade":round(float(a.mean()),5),
            "profit_factor":round(pf,3)}

def monthly_strategy(x,trades,allocation,cost_bps,slip_each=0):
    s=pd.Series(0.0,index=x.index,dtype=float)
    drag=cost_bps/10000.0+2*slip_each/10000.0
    for t in trades:
        s.loc[t["exit_date"]]+=allocation*(t["gross"]-drag)
    return s.resample("ME").sum()

def benchmark_monthly(x):
    return x.Close.resample("ME").last().pct_change().dropna()

def rolling_windows(s,b,years=5):
    z=pd.concat([s.rename("s"),b.rename("b")],axis=1).dropna()
    wins=[];n=years*12
    for i in range(n,len(z)+1):
        q=z.iloc[i-n:i]
        sw=float((1+q.s).prod()-1);bw=float((1+q.b).prod()-1)
        wins.append({"end":str(q.index[-1].date()),"strategy_return":sw,"benchmark_return":bw,"beat":sw>bw})
    return {"windows":len(wins),"beat_count":sum(w["beat"] for w in wins),"beat_rate":round(sum(w["beat"] for w in wins)/len(wins),3) if wins else None,
            "worst_strategy_return":round(min(w["strategy_return"] for w in wins),4) if wins else None}

def block_bootstrap(trades,allocation,cost_bps,slip_each=0,block=5,iters=5000):
    r=trade_returns(trades,cost_bps,slip_each)*allocation
    if len(r)<30:return {}
    rng=np.random.default_rng(SEED);n=len(r);cagrs=[];mdds=[]
    years=26.0
    starts=np.arange(max(1,n-block+1))
    for _ in range(iters):
        seq=[]
        while len(seq)<n:
            st=int(rng.choice(starts));seq.extend(r[st:st+block])
        a=np.array(seq[:n]);w=np.cumprod(1+a);peak=np.maximum.accumulate(np.r_[1.0,w]);w2=np.r_[1.0,w]
        cagrs.append(float(w[-1]**(1/years)-1));mdds.append(float(np.max(1-w2/peak)))
    return {"cagr_95":[round(float(x),4) for x in np.quantile(cagrs,[.025,.975])],
            "max_drawdown_95":[round(float(x),4) for x in np.quantile(mdds,[.025,.975])]}

def timing_perturbations(x):
    results=[]
    for delay in [0,1,2]:
        t=signals(x,delay)
        for cost,slip in [(30,0),(50,5),(100,10)]:
            r=trade_returns(t,cost,slip)
            w=r[r>0];l=r[r<0];pf=float(w.sum()/abs(l.sum())) if len(l) else 999
            results.append({"entry_delay_days":delay,"roundtrip_cost_bps":cost,"slippage_each_side_bps":slip,
                            "trades":len(r),"avg_return":round(float(r.mean()),5),"profit_factor":round(pf,3),
                            "win_rate":round(float((r>0).mean()),3)})
    return results

def main():
    raw=yf.download("QQQ",start=START,interval="1d",auto_adjust=True,progress=False,threads=False,timeout=30)
    x=prep(norm(raw));t=signals(x,0)
    paths=[portfolio_path(x,t,a,30,0) for a in [0.25,0.50,1.0]]
    s50=monthly_strategy(x,t,.50,30,0);b=benchmark_monthly(x)
    roll=rolling_windows(s50,b,5)
    boot=block_bootstrap(t,.50,50,5)
    pert=timing_perturbations(x)
    severe=[p for p in pert if p["entry_delay_days"]==0 and p["roundtrip_cost_bps"]==100 and p["slippage_each_side_bps"]==10][0]
    delay1=[p for p in pert if p["entry_delay_days"]==1 and p["roundtrip_cost_bps"]==50 and p["slippage_each_side_bps"]==5][0]
    # Locked before result: candidate survives if practical 50% sizing DD<=15%, severe no-delay remains positive PF>=1.15,
    # 1-day delay realistic stress PF>=1.10, bootstrap lower CAGR >0, and >=35% rolling 5y windows beat passive QQQ.
    passed=(paths[1]["max_drawdown"]<=.15 and severe["avg_return"]>0 and severe["profit_factor"]>=1.15
            and delay1["avg_return"]>0 and delay1["profit_factor"]>=1.10
            and boot.get("cagr_95",[0])[0]>0 and roll["beat_rate"] is not None and roll["beat_rate"]>=.35)
    out={"strategy":"QQQ_IBS_LOWER_BAND","status":"ROUND14_PORTFOLIO_CANDIDATE" if passed else "REJECT",
         "portfolio_paths":paths,"rolling_5y_vs_buy_hold":roll,"block_bootstrap_realistic_50pct":boot,
         "timing_and_cost_perturbations":pert,"limitations":["Yahoo adjusted daily bars are research-grade","cash return is zero","buy-and-hold comparison is informational; strategy is intended as tactical risk-managed exposure","still requires broker-data shadow/forward validation"]}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps(out,indent=2))
if __name__=="__main__":main()
