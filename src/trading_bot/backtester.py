from __future__ import annotations
import json
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
    exit_price=float(future['Close'].iloc[-1]);out='TIME_EXIT'
    for _,r in future.iterrows():
        lo=float(r['Low']);hi=float(r['High'])
        if lo<=s.stop_loss:out='STOP';exit_price=s.stop_loss;break
        if hi>=s.target2:out='TARGET2';exit_price=s.target2;break
        if hi>=s.target1:out='TARGET1';exit_price=s.target1;break
    ret=(exit_price-s.price)/s.price*100-(2*cost_bps/100)
    return out,round(ret,3)
def _stats(rows):
    if not rows:return {'samples':0,'win_rate':0.0,'avg_return_pct':0.0,'profit_factor':0.0}
    wins=[x['return_pct'] for x in rows if x['return_pct']>0];loss=[x['return_pct'] for x in rows if x['return_pct']<=0];gp=sum(wins);gl=abs(sum(loss))
    return {'samples':len(rows),'win_rate':round(len(wins)/len(rows)*100,1),'avg_return_pct':round(sum(x['return_pct'] for x in rows)/len(rows),3),'profit_factor':round(gp/gl,2) if gl else (999.0 if gp else 0.0)}
def _swing(symbol,df,threshold,cost):
    rows=[]
    for i in range(220,len(df)-6,5):
        s=swing_setup(symbol,df.iloc[:i+1])
        if not s or s.raw_score<threshold:continue
        o=_outcome(s,df.iloc[i+1:i+6],cost)
        if o:rows.append({'symbol':symbol,'strategy':'SWING','date':str(df.index[i].date()),'score':s.raw_score,'outcome':o[0],'return_pct':o[1]})
    return rows
def _day(symbol,df,threshold,cost):
    rows=[]
    if len(df)<80:return rows
    dates=pd.Series(df.index.date,index=df.index)
    for day in sorted(set(dates)):
        d=df[dates==day]
        if len(d)<30:continue
        # Evaluate a few spaced decision points; future is strictly after the signal bar.
        for i in range(55,len(d)-4,8):
            history=df[df.index<=d.index[i]].tail(300)
            s=intraday_setup(symbol,history)
            if not s or s.raw_score<threshold:continue
            future=d.iloc[i+1:min(i+14,len(d))];o=_outcome(s,future,cost)
            if o:rows.append({'symbol':symbol,'strategy':'DAY','date':str(day),'score':s.raw_score,'outcome':o[0],'return_pct':o[1]})
    return rows
def run_backtest():
    cfg=yaml.safe_load((ROOT/'config/settings.yml').read_text())['settings'];universe=yaml.safe_load((ROOT/'config/universe.yml').read_text())['universe'];threshold=int(cfg.get('backtest_min_score',85));years=int(cfg.get('backtest_years',3));cost=float(cfg.get('backtest_transaction_cost_bps',10));rows=[]
    for symbol in universe:
        try:
            daily=_norm(yf.download(symbol,period=f'{years}y',interval='1d',auto_adjust=False,progress=False,threads=False),symbol);rows.extend(_swing(symbol,daily,threshold,cost))
        except Exception as e:print('BT SWING',symbol,e)
        try:
            intra=_norm(yf.download(symbol,period='60d',interval='15m',auto_adjust=False,progress=False,threads=False),symbol);rows.extend(_day(symbol,intra,threshold,cost))
        except Exception as e:print('BT DAY',symbol,e)
    # Walk-forward style reporting by chronology: earlier 70% development, later 30% validation.
    rows=sorted(rows,key=lambda x:x['date']);cut=int(len(rows)*.70);dev=rows[:cut];val=rows[cut:]
    out={'engine':'V4-Precision','method':'Conservative chronological walk-forward proxy; no future bars used in signal construction. Transaction costs included.','threshold':threshold,'transaction_cost_bps':cost,'overall':_stats(rows),'development':_stats(dev),'validation':_stats(val),'by_strategy':{n:_stats([x for x in val if x['strategy']==n]) for n in ('DAY','SWING')},'validation_samples':val[-500:]}
    (ROOT/'data/backtest.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in out.items() if k!='validation_samples'},indent=2));return 0
