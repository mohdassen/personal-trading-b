"""Historical edge research for V7-style long setups.
Research only: evaluates whether the underlying trend/momentum hypotheses have
out-of-sample edge before more engineering work is justified.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT=Path('data/v7_edge_research.json')
SYMBOLS=['AAPL','MSFT','NVDA','AMD','AVGO','META','GOOGL','AMZN','CRWD','PANW','NOW','ORCL','MU','QCOM','NFLX','DIS','ROKU','ABBV','XOM','CVX','COP','SLB','CAT','GE','JPM','V','MA','COST','WMT','LLY']
COST_R=0.06  # conservative round-trip slippage/fees expressed in R


def stats(rows):
    rs=np.array([r['net_r'] for r in rows],dtype=float)
    if not len(rs): return {'samples':0,'win_rate':0,'expectancy_r':0,'profit_factor':0,'total_r':0,'max_drawdown_r':0}
    wins=rs[rs>0]; losses=-rs[rs<0]
    eq=np.cumsum(rs); peak=np.maximum.accumulate(np.r_[0,eq]); dd=peak[1:]-eq
    return {'samples':int(len(rs)),'win_rate':round(float((rs>0).mean()*100),1),'expectancy_r':round(float(rs.mean()),3),'profit_factor':round(float(wins.sum()/losses.sum()),2) if losses.sum()>0 else 99.0,'total_r':round(float(rs.sum()),2),'max_drawdown_r':round(float(dd.max()),2)}


def prepare(df):
    c=df['Close'].astype(float); h=df['High'].astype(float); l=df['Low'].astype(float); v=df['Volume'].astype(float)
    x=pd.DataFrame(index=df.index); x['close']=c; x['high']=h; x['low']=l
    x['ema9']=c.ewm(span=9,adjust=False).mean(); x['ema21']=c.ewm(span=21,adjust=False).mean(); x['ema50']=c.ewm(span=50,adjust=False).mean()
    tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1); x['atr']=tr.rolling(14).mean()
    x['rvol']=v/v.rolling(20).mean(); x['ret20']=c.pct_change(20); x['hh20']=h.shift(1).rolling(20).max()
    return x.dropna()


def signal(row,kind):
    trend=row.close>row.ema21>row.ema50 and row.ema9>=row.ema21
    if kind=='MOMENTUM_LEADER': return trend and row.ret20>=0.04 and row.rvol>=1.0
    if kind=='BREAKOUT': return trend and row.close>row.hh20 and row.rvol>=1.15
    if kind=='SWING_CONTINUATION': return trend and row.ret20>=0.04
    return False


def simulate(x,symbol,kind):
    rows=[]; cooldown=-1
    idx=list(x.index)
    for i in range(60,len(x)-11):
        if i<=cooldown or not signal(x.iloc[i],kind): continue
        s=x.iloc[i]; entry=float(x.iloc[i+1].close); atr=float(s.atr)
        if not atr or math.isnan(atr): continue
        stop=entry-1.8*atr; risk=entry-stop; target=entry+(1.8 if kind=='SWING_CONTINUATION' else 1.6)*risk
        exitp=float(x.iloc[min(i+10,len(x)-1)].close); reason='TIME'
        for j in range(i+1,min(i+11,len(x))):
            b=x.iloc[j]
            if b.low<=stop: exitp=stop; reason='STOP'; cooldown=j+2; break
            if b.high>=target: exitp=target; reason='TARGET'; cooldown=j+2; break
        gross=(exitp-entry)/risk; net=gross-COST_R
        rows.append({'symbol':symbol,'setup':kind,'date':str(idx[i].date()),'net_r':round(net,4),'reason':reason})
        cooldown=max(cooldown,i+3)
    return rows


def main():
    allrows=[]; errors=[]
    for sym in SYMBOLS:
        try:
            df=yf.download(sym,period='5y',interval='1d',auto_adjust=True,progress=False,threads=False)
            if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
            x=prepare(df)
            for kind in ['MOMENTUM_LEADER','BREAKOUT','SWING_CONTINUATION']: allrows += simulate(x,sym,kind)
        except Exception as e: errors.append({'symbol':sym,'error':str(e)[:160]})
    dates=sorted({r['date'] for r in allrows}); cut1=dates[int(len(dates)*.60)] if dates else ''; cut2=dates[int(len(dates)*.80)] if dates else ''
    train=[r for r in allrows if r['date']<=cut1]; val=[r for r in allrows if cut1<r['date']<=cut2]; hold=[r for r in allrows if r['date']>cut2]
    by_setup={}
    for k in ['MOMENTUM_LEADER','BREAKOUT','SWING_CONTINUATION']:
        by_setup[k]={'train':stats([r for r in train if r['setup']==k]),'validation':stats([r for r in val if r['setup']==k]),'holdout':stats([r for r in hold if r['setup']==k])}
    hs=stats(hold)
    robust=hs['samples']>=100 and hs['expectancy_r']>0 and hs['profit_factor']>=1.20 and hs['max_drawdown_r']<=12
    report={'engine':'V7 Historical Edge Research','mode':'RESEARCH_ONLY','years':5,'symbols':len(SYMBOLS),'cost_assumption_r':COST_R,'splits':{'train_end':cut1,'validation_end':cut2},'overall':{'train':stats(train),'validation':stats(val),'holdout':hs},'by_setup':by_setup,'errors':errors,'verdict':'PROMISING' if robust else 'FAIL_OR_REWORK','live_execution_authorized':False,'note':'Daily proxy research of V7 hypotheses; not a fill-perfect replay of the live multi-timeframe engine.'}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps(report,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
