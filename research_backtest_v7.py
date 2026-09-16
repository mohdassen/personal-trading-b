"""Historical edge validation for the V7 strategy families.

Purpose: answer one question quickly: do the current technical strategy rules show
an out-of-sample edge after conservative friction? This is research only and does
not place orders. News/catalyst and point-in-time Sharia fundamentals are excluded
from the historical simulator because free sources do not provide reliable
point-in-time history; the report states that limitation explicitly.
"""
from __future__ import annotations
import json, math
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import yfinance as yf
from src.trading_bot.indicators import add_daily

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data/v7_historical_edge_report.json'
UNIVERSE=['AAPL','MSFT','NVDA','AMD','AMZN','GOOGL','META','TSLA','AVGO','ORCL','CRM','ADBE','NFLX','MU','QCOM','AMAT','LRCX','KLAC','CRWD','PANW','NOW','PLTR','XOM','CVX','COP','SLB','ABBV','JNJ','LLY','UNH','CAT','DE','GE','WMT','COST','HD','DIS','ROKU','TMUS','GILD']
FEE_BPS=5
SLIPPAGE_BPS=10
ROUND_TRIP_BPS=2*(FEE_BPS+SLIPPAGE_BPS)


def f(x,d=0.):
    try:
        v=float(x); return d if math.isnan(v) else v
    except Exception:return d

def strategy_scores(m):
    scores={}
    sc=(25 if m['trend'] else 8 if m['above20'] else 0)+(20 if m['ret20']>=8 else 12 if m['ret20']>=4 else 0)+(15 if 0<m['ret5']<=8 else 6 if m['ret5']>0 else 0)+(15 if m['dist20']>=-3 else 7 if m['dist20']>=-6 else 0)+(10 if 50<=m['rsi']<=68 else 0)+(10 if m['rvol']>=1.2 else 4 if m['rvol']>=1 else 0)
    scores['SWING_CONTINUATION']=min(100,sc)
    sc=(22 if m['above_high'] else 12 if m['dist20']>=-1 else 0)+(20 if m['rvol']>=1.8 else 12 if m['rvol']>=1.2 else 0)+(15 if m['trend'] else 0)+(10 if m['ret5']>0 else 0)+(10 if m['rsi']<=72 and m['rsi']>=50 else 0)
    scores['BREAKOUT_DAILY_PROXY']=min(100,sc)
    return scores

def simulate_symbol(symbol):
    raw=yf.download(symbol,start='2021-01-01',end=None,auto_adjust=True,progress=False,threads=False)
    if raw is None or len(raw)<300:return []
    if isinstance(raw.columns,pd.MultiIndex): raw.columns=raw.columns.get_level_values(0)
    d=add_daily(raw.dropna()).copy()
    d['RET20']=d['Close'].pct_change(20)*100
    trades=[]; i=220
    while i < len(d)-16:
        r=d.iloc[i]; price=f(r.Close); atr=max(f(r.ATR14,price*.025),price*.005); high20=f(r.HIGH20,price)
        m={'trend':price>f(r.EMA20)>f(r.EMA50) and f(r.EMA50)>f(r.EMA200),'above20':price>f(r.EMA20),'ret20':f(r.RET20),'ret5':f(r.RET_5D),'dist20':(price/high20-1)*100 if high20 else 0,'rsi':f(r.RSI14,50),'rvol':f(r.VOL_RATIO,1),'above_high':price>high20}
        scores=strategy_scores(m); setup,score=max(scores.items(),key=lambda x:x[1])
        if score<72: i+=1; continue
        entry=f(d.iloc[i+1].Open)
        if entry<=0:i+=1;continue
        risk=max(1.5*atr,entry*.025); risk=min(risk,entry*.08); stop=entry-risk; target=entry+1.8*risk
        exit_px=f(d.iloc[min(i+15,len(d)-1)].Close); outcome='TIME'; exit_i=min(i+15,len(d)-1)
        for j in range(i+1,min(i+16,len(d))):
            bar=d.iloc[j]
            if f(bar.Low)<=stop: exit_px=stop; outcome='STOP'; exit_i=j; break
            if f(bar.High)>=target: exit_px=target; outcome='TARGET'; exit_i=j; break
        gross=(exit_px-entry)/risk
        friction=(entry*ROUND_TRIP_BPS/10000)/risk
        net=gross-friction
        trades.append({'symbol':symbol,'setup':setup,'signal_date':str(d.index[i].date()),'entry_date':str(d.index[i+1].date()),'exit_date':str(d.index[exit_i].date()),'score':int(score),'gross_r':round(gross,4),'net_r':round(net,4),'outcome':outcome})
        i=exit_i+1
    return trades

def stats(rows):
    rs=[x['net_r'] for x in rows]
    if not rs:return {'samples':0}
    wins=[x for x in rs if x>0]; losses=[x for x in rs if x<=0]
    eq=np.cumsum(rs); peak=np.maximum.accumulate(np.r_[0,eq]); dd=peak[1:]-eq
    return {'samples':len(rs),'win_rate':round(len(wins)/len(rs),4),'expectancy_r':round(float(np.mean(rs)),4),'profit_factor':round(sum(wins)/abs(sum(losses)),3) if losses and sum(losses)!=0 else None,'total_r':round(sum(rs),3),'max_drawdown_r':round(float(max(dd)) if len(dd) else 0,3),'median_r':round(float(np.median(rs)),4)}
def main():
    all_trades=[]; errors={}
    for s in UNIVERSE:
        try: all_trades.extend(simulate_symbol(s))
        except Exception as e: errors[s]=str(e)[:200]
    all_trades.sort(key=lambda x:x['signal_date'])
    n=len(all_trades); a=int(n*.60); b=int(n*.80)
    train,valid,hold=all_trades[:a],all_trades[a:b],all_trades[b:]
    setups=sorted(set(x['setup'] for x in all_trades))
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'engine':'V7-HISTORICAL-EDGE-VALIDATOR','research_only':True,'friction':{'fee_bps_each_side':FEE_BPS,'slippage_bps_each_side':SLIPPAGE_BPS,'round_trip_bps':ROUND_TRIP_BPS},'limitations':['Daily proxy backtest: intraday/VWAP/session-leader rules are not claimed as historically validated here.','Historical news catalyst and point-in-time Sharia fundamentals are excluded to avoid look-ahead bias.','Yahoo Finance data can contain survivorship/corporate-action limitations.'],'split':{'method':'chronological 60/20/20','train':stats(train),'validation':stats(valid),'untouched_holdout':stats(hold)},'holdout_by_strategy':{s:stats([x for x in hold if x['setup']==s]) for s in setups},'all_by_strategy':{s:stats([x for x in all_trades if x['setup']==s]) for s in setups},'errors':errors,'sample_trades':hold[-25:]}
    h=report['split']['untouched_holdout']; report['decision']='PROMISING' if h.get('samples',0)>=50 and h.get('expectancy_r',-9)>0 and (h.get('profit_factor') or 0)>=1.25 and h.get('max_drawdown_r',999)<=12 else 'FAIL_OR_INSUFFICIENT'
    OUT.parent.mkdir(exist_ok=True); OUT.write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps(report,indent=2))
if __name__=='__main__':main()
