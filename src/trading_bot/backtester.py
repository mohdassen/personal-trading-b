from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import pandas as pd
import yfinance as yf
import yaml
from .strategies import swing_setup,intraday_setup

ROOT=Path(__file__).resolve().parents[2]
ENGINE='V4.4-Validation'
SCORE_GRID=(70,75,80,82,85,87,88,90,92,95)
RR_GRID=(1.2,1.4,1.5,1.7,1.9,2.0,2.2,2.5)
COLLECT_MIN_SCORE=55
COLLECT_MIN_RR=0.25


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
        # Conservative ambiguity rule: stop wins if stop and target touch same bar.
        if lo<=s.stop_loss:out='STOP';exit_price=float(s.stop_loss);break
        if hi>=s.target2:out='TARGET2';exit_price=float(s.target2);break
        if hi>=s.target1:out='TARGET1';exit_price=float(s.target1);break
    cost_pct=2*cost_bps/100
    ret_pct=(exit_price-entry)/entry*100-cost_pct
    cost_dollars=entry*(cost_pct/100)
    r_mult=(exit_price-entry-cost_dollars)/risk
    return out,round(ret_pct,3),round(r_mult,3)


def _max_drawdown_r(rows):
    equity=0.0;peak=0.0;max_dd=0.0
    for x in sorted(rows,key=lambda z:z['timestamp']):
        equity+=float(x['r_multiple']);peak=max(peak,equity);max_dd=max(max_dd,peak-equity)
    return round(max_dd,3)


def _stats(rows):
    if not rows:return {'samples':0,'win_rate':0.0,'avg_return_pct':0.0,'avg_r':0.0,'median_r':0.0,'profit_factor_r':0.0,'max_drawdown_r':0.0,'expectancy_r':0.0}
    rs=[float(x['r_multiple']) for x in rows];wins=[r for r in rs if r>0];losses=[r for r in rs if r<=0]
    gp=sum(wins);gl=abs(sum(losses));s=pd.Series(rs)
    avg=sum(rs)/len(rs)
    return {'samples':len(rows),'win_rate':round(len(wins)/len(rows)*100,1),'avg_return_pct':round(sum(float(x['return_pct']) for x in rows)/len(rows),3),'avg_r':round(avg,3),'median_r':round(float(s.median()),3),'profit_factor_r':round(gp/gl,2) if gl else (999.0 if gp else 0.0),'max_drawdown_r':_max_drawdown_r(rows),'expectancy_r':round(avg,3)}


def _collectable(s):
    return bool(s and int(s.raw_score)>=COLLECT_MIN_SCORE and float(s.rr1)>=COLLECT_MIN_RR and float(s.entry_low)<=float(s.price)<=float(s.entry_high))


def _eligible_row(x,score,rr):
    return int(x['score'])>=score and float(x['rr1'])>=rr


def _view(rows,score,rr):
    return _stats([x for x in rows if _eligible_row(x,score,rr)])


def _row(symbol,strategy,ts,s,o):
    return {'symbol':symbol,'strategy':strategy,'setup_type':s.setup_type,'timestamp':pd.Timestamp(ts).isoformat(),'date':str(pd.Timestamp(ts).date()),'score':int(s.raw_score),'rr1':round(float(s.rr1),3),'entry':round(float(s.price),4),'stop':round(float(s.stop_loss),4),'target1':round(float(s.target1),4),'target2':round(float(s.target2),4),'outcome':o[0],'return_pct':o[1],'r_multiple':o[2]}


def _swing(symbol,df,cost):
    rows=[];last_signal_i=-99
    # Evaluate every completed daily bar. A 3-bar cooldown reduces repeated near-identical entries.
    for i in range(220,len(df)-6):
        if i-last_signal_i<3:continue
        s=swing_setup(symbol,df.iloc[:i+1])
        if not _collectable(s):continue
        o=_outcome(s,df.iloc[i+1:i+6],cost)
        if o:
            rows.append(_row(symbol,'SWING',df.index[i],s,o));last_signal_i=i
    return rows


def _day(symbol,df,cost):
    rows=[];last_day=None
    if len(df)<100:return rows
    # Test multiple decision points per day while allowing at most one sampled DAY setup per symbol/day.
    for i in range(60,len(df)-4,4):
        signal_time=df.index[i]
        if last_day==signal_time.date():continue
        history=df.iloc[:i+1].tail(350);s=intraday_setup(symbol,history)
        if not _collectable(s):continue
        same_day=df[(df.index>signal_time)&(df.index.date==signal_time.date())].head(12)
        if same_day.empty:continue
        o=_outcome(s,same_day,cost)
        if o:
            rows.append(_row(symbol,'DAY',signal_time,s,o));last_day=signal_time.date()
    return rows


def _one(symbol,years,cost):
    rows=[]
    try:
        daily=_norm(yf.download(symbol,period=f'{years}y',interval='1d',auto_adjust=False,progress=False,threads=False,timeout=25),symbol)
        rows.extend(_swing(symbol,daily,cost))
    except Exception as e:print('BT SWING',symbol,e)
    try:
        intra=_norm(yf.download(symbol,period='60d',interval='15m',auto_adjust=False,progress=False,threads=False,timeout=25),symbol)
        rows.extend(_day(symbol,intra,cost))
    except Exception as e:print('BT DAY',symbol,e)
    return rows


def _split(rows):
    if not rows:return [],[],None
    ordered=sorted(rows,key=lambda x:x['timestamp'])
    dates=sorted(set(x['date'] for x in ordered))
    cut_idx=max(1,min(len(dates)-1,int(len(dates)*.70))) if len(dates)>1 else 1
    cut_date=dates[cut_idx-1]
    dev=[x for x in ordered if x['date']<=cut_date];val=[x for x in ordered if x['date']>cut_date]
    return dev,val,cut_date


def _sensitivity(rows):
    out=[]
    for score in SCORE_GRID:
        for rr in RR_GRID:
            st=_view(rows,score,rr)
            out.append({'score':score,'min_rr':rr,**st})
    return out


def _rank_validation(rows):
    # Prefer positive expectancy + PF, but penalize tiny samples and drawdown.
    ranked=[]
    for x in rows:
        n=int(x['samples']);pf=float(x['profit_factor_r']);exp=float(x['expectancy_r']);dd=float(x['max_drawdown_r'])
        sample_factor=min(1.0,n/50.0)
        quality=(max(-1.0,min(2.0,exp))*2.0 + max(0.0,min(3.0,pf-1.0)) - min(3.0,dd/10.0))*sample_factor
        ranked.append({**x,'validation_quality_score':round(quality,3),'sample_warning':n<30})
    return sorted(ranked,key=lambda x:(x['validation_quality_score'],x['samples']),reverse=True)


def run_backtest():
    cfg=yaml.safe_load((ROOT/'config/settings.yml').read_text())['settings'];universe=yaml.safe_load((ROOT/'config/universe.yml').read_text())['universe']
    live_score=int(cfg.get('backtest_min_score',85));live_rr=float(cfg.get('min_risk_reward',2));years=int(cfg.get('backtest_years',3));cost=float(cfg.get('backtest_transaction_cost_bps',10));rows=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut={pool.submit(_one,s,years,cost):s for s in universe}
        for f in as_completed(fut):
            try:rows.extend(f.result())
            except Exception as e:print('BT',fut[f],e)
    rows=sorted(rows,key=lambda x:x['timestamp']);dev,val,cut_date=_split(rows)
    sensitivity_dev=_sensitivity(dev);sensitivity_val=_sensitivity(val)
    val_ranked=_rank_validation(sensitivity_val)
    top_validated=[x for x in val_ranked if x['samples']>=30 and x['expectancy_r']>0 and x['profit_factor_r']>1][:10]
    live_dev=_view(dev,live_score,live_rr);live_val=_view(val,live_score,live_rr);live_all=_view(rows,live_score,live_rr)
    out={
        'engine':ENGINE,
        'method':'V4.4 candidate-first validation. Signals are collected with broad score/RR gates, must be inside the technical entry zone, use prior data only, conservative stop-first same-bar handling, transaction costs, chronological 70/30 date split, and separate score/RR sensitivity on development and untouched validation periods.',
        'collection_floor':{'score':COLLECT_MIN_SCORE,'min_rr':COLLECT_MIN_RR},
        'live_parameters':{'score':live_score,'min_rr':live_rr},
        'transaction_cost_bps':cost,'years_requested':years,'candidate_pool_samples':len(rows),'split_date':cut_date,
        'candidate_pool':_stats(rows),'development_pool':_stats(dev),'validation_pool':_stats(val),
        'live_parameters_overall':live_all,'live_parameters_development':live_dev,'live_parameters_validation':live_val,
        'live_validation_by_strategy':{n:_stats([x for x in val if x['strategy']==n and _eligible_row(x,live_score,live_rr)]) for n in ('DAY','SWING')},
        'live_validation_by_setup_type':{n:_stats([x for x in val if x['setup_type']==n and _eligible_row(x,live_score,live_rr)]) for n in ('BREAKOUT','PULLBACK')},
        'development_sensitivity':sensitivity_dev,
        'validation_sensitivity':sensitivity_val,
        'top_validated_parameter_sets':top_validated,
        'validation_samples':[x for x in val if _eligible_row(x,live_score,live_rr)][-1000:]
    }
    (ROOT/'data/backtest.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    summary={k:v for k,v in out.items() if k not in ('development_sensitivity','validation_sensitivity','validation_samples')}
    print(json.dumps(summary,indent=2));return 0
