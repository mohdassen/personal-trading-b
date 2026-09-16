"""Historical edge test for V7 research.

Purpose: answer one question quickly: do simple V7-style trend/pullback rules retain
positive expectancy on unseen history after costs? This does NOT promote live trading.
Uses daily Yahoo data so it can run free in GitHub Actions.
"""
from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

SYMBOLS = ["AAPL","MSFT","NVDA","META","AMZN","GOOGL","AMD","AVGO","CRM","ORCL","NFLX","COST","JPM","GS","XOM","CVX","COP","ABBV","LLY","UNH","CAT","DE","SPOT","CRWD","MU"]
START="2021-01-01"
COST_R=0.04  # deliberately conservative round-trip friction in R
HOLD_BARS=10


def _series(x):
    if isinstance(x, pd.DataFrame): return x.iloc[:,0]
    return x


def trades_for(symbol):
    d=yf.download(symbol,start=START,auto_adjust=True,progress=False,threads=False)
    if d is None or len(d)<260: return []
    c=_series(d['Close']); h=_series(d['High']); l=_series(d['Low']); v=_series(d['Volume'])
    ema20=c.ewm(span=20,adjust=False).mean(); ema50=c.ewm(span=50,adjust=False).mean(); ema200=c.ewm(span=200,adjust=False).mean()
    atr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1).rolling(14).mean()
    vol20=v.rolling(20).mean(); rsi_delta=c.diff(); up=rsi_delta.clip(lower=0).rolling(14).mean(); dn=(-rsi_delta.clip(upper=0)).rolling(14).mean(); rsi=100-(100/(1+up/dn.replace(0,np.nan)))
    out=[]
    # V7-style long trend continuation: strong primary trend + controlled pullback + recovery.
    for i in range(210,len(d)-HOLD_BARS-1):
        if any(pd.isna(x) for x in (ema20.iloc[i],ema50.iloc[i],ema200.iloc[i],atr.iloc[i],rsi.iloc[i])): continue
        trend= c.iloc[i] > ema50.iloc[i] > ema200.iloc[i]
        pullback= l.iloc[i-3:i+1].min() <= ema20.iloc[i]*1.015 and c.iloc[i] >= ema20.iloc[i]
        momentum= 48 <= rsi.iloc[i] <= 72
        liquidity= v.iloc[i] >= 0.75*vol20.iloc[i]
        if not (trend and pullback and momentum and liquidity): continue
        entry=float(c.iloc[i]); risk=max(float(atr.iloc[i])*1.25,entry*0.012); stop=entry-risk; target=entry+2*risk
        result=None; exit_px=None
        for j in range(i+1,min(i+HOLD_BARS+1,len(d))):
            # pessimistic same-bar ordering: stop first.
            if float(l.iloc[j]) <= stop: result=-1.0-COST_R; exit_px=stop; break
            if float(h.iloc[j]) >= target: result=2.0-COST_R; exit_px=target; break
        if result is None:
            exit_px=float(c.iloc[min(i+HOLD_BARS,len(d)-1)])
            result=(exit_px-entry)/risk-COST_R
        out.append({'symbol':symbol,'date':str(d.index[i].date()),'entry':round(entry,4),'r':round(float(result),4)})
    return out


def metrics(rows):
    rs=np.array([x['r'] for x in rows],dtype=float)
    if len(rs)==0:return {'samples':0}
    wins=rs[rs>0]; losses=rs[rs<0]; equity=np.cumsum(rs); peaks=np.maximum.accumulate(np.r_[0,equity]) [1:]; dd=peaks-equity
    pf=float(wins.sum()/abs(losses.sum())) if len(losses) and losses.sum()!=0 else None
    return {'samples':len(rs),'win_rate':round(float((rs>0).mean()),4),'expectancy_r':round(float(rs.mean()),4),'profit_factor':round(pf,3) if pf is not None else None,'total_r':round(float(rs.sum()),3),'max_drawdown_r':round(float(dd.max()) if len(dd) else 0,3)}


def main():
    rows=[]
    for s in SYMBOLS:
        try: rows.extend(trades_for(s))
        except Exception as e: print('WARN',s,e)
    rows.sort(key=lambda x:x['date'])
    n=len(rows); a=int(n*.60); b=int(n*.80)
    train,validation,holdout=rows[:a],rows[a:b],rows[b:]
    report={'method':'daily V7-style trend/pullback historical falsification test','symbols':len(SYMBOLS),'start':START,'cost_r':COST_R,'splits':{'train':metrics(train),'validation':metrics(validation),'untouched_holdout':metrics(holdout)},'all':metrics(rows)}
    h=report['splits']['untouched_holdout']; v=report['splits']['validation']
    robust=(v.get('samples',0)>=30 and h.get('samples',0)>=30 and v.get('expectancy_r',-9)>0 and h.get('expectancy_r',-9)>0 and (h.get('profit_factor') or 0)>=1.20 and h.get('max_drawdown_r',999)<=12)
    report['decision']='PROMISING_FORWARD_CANDIDATE' if robust else 'FAIL_OR_INSUFFICIENT_EDGE'
    report['safe_for_live']=False
    Path('data').mkdir(exist_ok=True); Path('data/v7_historical_edge_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
