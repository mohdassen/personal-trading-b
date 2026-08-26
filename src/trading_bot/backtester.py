from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import pandas as pd
import yfinance as yf
import yaml
from .strategies import swing_setup,intraday_setup
ROOT=Path(__file__).resolve().parents[2]
def _norm(df,symbol):
    if isinstance(df.columns,pd.MultiIndex):
        if symbol in df.columns.get_level_values(-1):df=df.xs(symbol,axis=1,level=-1)
        else:df.columns=df.columns.get_level_values(0)
    return df.dropna(subset=['Open','High','Low','Close'])
def _outcome(s,future,cost_bps):
    if future.empty:return None
    entry=float(s.price);risk=max(entry-float(s.stop_loss),.01);exit_price=float(future['Close'].iloc[-1]);out='TIME_EXIT'
    for _,r in future.iterrows():
        lo=float(r['Low']);hi=float(r['High'])
        if lo<=s.stop_loss:out='STOP';exit_price=s.stop_loss;break
        if hi>=s.target2:out='TARGET2';exit_price=s.target2;break
        if hi>=s.target1:out='TARGET1';exit_price=s.target1;break
    cost_pct=2*cost_bps/100;ret_pct=(exit_price-entry)/entry*100-cost_pct;cost_dollars=entry*(cost_pct/100);r_mult=(exit_price-entry-cost_dollars)/risk
    return out,round(ret_pct,3),round(r_mult,3)
def _stats(rows):
    if not rows:return {'samples':0,'win_rate':0.0,'avg_return_pct':0.0,'avg_r':0.0,'profit_factor_r':0.0}
    wins=[x for x in rows if x['r_multiple']>0];gp=sum(x['r_multiple'] for x in rows if x['r_multiple']>0);gl=abs(sum(x['r_multiple'] for x in rows if x['r_multiple']<=0))
    return {'samples':len(rows),'win_rate':round(len(wins)/len(rows)*100,1),'avg_return_pct':round(sum(x['return_pct'] for x in rows)/len(rows),3),'avg_r':round(sum(x['r_multiple'] for x in rows)/len(rows),3),'profit_factor_r':round(gp/gl,2) if gl else (999.0 if gp else 0.0)}
def _keep(s,threshold,min_rr):return bool(s and s.raw_score>=threshold and float(s.rr1)>=min_rr)
def _swing(symbol,df,threshold,min_rr,cost):
    rows=[]
    for i in range(220,len(df)-6,5):
        s=swing_setup(symbol,df.iloc[:i+1])
        if not _keep(s,threshold,min_rr):continue
        o=_outcome(s,df.iloc[i+1:i+6],cost)
        if o:rows.append({'symbol':symbol,'strategy':'SWING','date':str(df.index[i].date()),'score':s.raw_score,'rr1':round(s.rr1,2),'outcome':o[0],'return_pct':o[1],'r_multiple':o[2]})
    return rows
def _day(symbol,df,threshold,min_rr,cost):
    rows=[]
    if len(df)<100:return rows
    # Global history creates indicators; future bars are restricted to the same trading day.
    for i in range(60,len(df)-4,8):
        signal_time=df.index[i];history=df.iloc[:i+1].tail(350);s=intraday_setup(symbol,history)
        if not _keep(s,threshold,min_rr):continue
        same_day=df[(df.index>signal_time)&(df.index.date==signal_time.date())].head(12)
        if same_day.empty:continue
        o=_outcome(s,same_day,cost)
        if o:rows.append({'symbol':symbol,'strategy':'DAY','date':str(signal_time.date()),'score':s.raw_score,'rr1':round(s.rr1,2),'outcome':o[0],'return_pct':o[1],'r_multiple':o[2]})
    return rows
def _one(symbol,years,threshold,min_rr,cost):
    rows=[]
    try:
        daily=_norm(yf.download(symbol,period=f'{years}y',interval='1d',auto_adjust=False,progress=False,threads=False,timeout=20),symbol);rows.extend(_swing(symbol,daily,threshold,min_rr,cost))
    except Exception as e:print('BT SWING',symbol,e)
    try:
        intra=_norm(yf.download(symbol,period='60d',interval='15m',auto_adjust=False,progress=False,threads=False,timeout=20),symbol);rows.extend(_day(symbol,intra,threshold,min_rr,cost))
    except Exception as e:print('BT DAY',symbol,e)
    return rows
def _threshold_view(rows,threshold):return _stats([x for x in rows if int(x['score'])>=threshold])
def run_backtest():
    cfg=yaml.safe_load((ROOT/'config/settings.yml').read_text())['settings'];universe=yaml.safe_load((ROOT/'config/universe.yml').read_text())['universe'];threshold=int(cfg.get('backtest_min_score',85));min_rr=float(cfg.get('min_risk_reward',2));years=int(cfg.get('backtest_years',3));cost=float(cfg.get('backtest_transaction_cost_bps',10));rows=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut={pool.submit(_one,s,years,threshold,min_rr,cost):s for s in universe}
        for f in as_completed(fut):
            try:rows.extend(f.result())
            except Exception as e:print('BT',fut[f],e)
    rows=sorted(rows,key=lambda x:x['date']);cut=int(len(rows)*.70);dev=rows[:cut];val=rows[cut:]
    out={'engine':'V4-Precision','method':'Conservative chronological validation. Signal uses only prior data; RR>=configured minimum; same-bar ambiguity counts stop first; round-trip costs included.','threshold':threshold,'min_rr':min_rr,'transaction_cost_bps':cost,'overall':_stats(rows),'development':_stats(dev),'validation':_stats(val),'by_strategy':{n:_stats([x for x in val if x['strategy']==n]) for n in ('DAY','SWING')},'validation_by_threshold':{str(t):_threshold_view(val,t) for t in (85,88,90,92,95)},'validation_samples':val[-500:]}
    (ROOT/'data/backtest.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in out.items() if k!='validation_samples'},indent=2));return 0
