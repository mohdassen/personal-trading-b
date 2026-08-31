from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import pandas as pd
import yfinance as yf
import yaml
from .strategies import swing_setup,intraday_setup
from .indicators import add_daily

ROOT=Path(__file__).resolve().parents[2]
ENGINE='V4.4.1-Qualification'
SCORE_GRID=(70,75,80,82,85,87,88,90,92,95)
RR_GRID=(1.2,1.4,1.5,1.7,1.9,2.0)
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
    gp=sum(wins);gl=abs(sum(losses));avg=sum(rs)/len(rs)
    return {'samples':len(rows),'win_rate':round(len(wins)/len(rows)*100,1),'avg_return_pct':round(sum(float(x['return_pct']) for x in rows)/len(rows),3),'avg_r':round(avg,3),'median_r':round(float(pd.Series(rs).median()),3),'profit_factor_r':round(gp/gl,2) if gl else (999.0 if gp else 0.0),'max_drawdown_r':_max_drawdown_r(rows),'expectancy_r':round(avg,3)}


def _collectable(s):
    return bool(s and int(s.raw_score)>=COLLECT_MIN_SCORE and float(s.rr1)>=COLLECT_MIN_RR and float(s.entry_low)<=float(s.price)<=float(s.entry_high))


def _weekly_alignment(daily):
    if daily is None or len(daily)<60:return False
    w=daily.resample('W-FRI').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
    if len(w)<12:return False
    e8=w['Close'].ewm(span=8,adjust=False).mean();e20=w['Close'].ewm(span=20,adjust=False).mean()
    return bool(float(w['Close'].iloc[-1])>float(e8.iloc[-1])>float(e20.iloc[-1]))


def _market_favorable(spy,ts):
    if spy is None or spy.empty:return False
    try:
        cutoff=pd.Timestamp(ts)
        if getattr(spy.index,'tz',None) is not None and cutoff.tzinfo is None:cutoff=cutoff.tz_localize(spy.index.tz)
        if getattr(spy.index,'tz',None) is None and cutoff.tzinfo is not None:cutoff=cutoff.tz_localize(None)
        hist=spy.loc[spy.index<=cutoff].tail(260)
        if len(hist)<60:return False
        r=add_daily(hist).iloc[-1];c=float(r['Close']);e20=float(r['EMA20']);e50=float(r['EMA50'])
        ret20=(c/float(hist['Close'].iloc[-21])-1)*100 if len(hist)>21 else 0.0
        return bool(c>e20>e50 and ret20>-1.0)
    except Exception:return False


def _confirmation(s):
    if s.strategy=='DAY':checks=(float(s.vol_ratio)>=1.2,float(s.momentum)>0,48<=float(s.rsi)<=70)
    else:checks=(float(s.vol_ratio)>=1.05,float(s.momentum)>0,47<=float(s.rsi)<=68)
    required=3 if int(s.raw_score)>=92 else 2
    return sum(bool(x) for x in checks)>=required,sum(bool(x) for x in checks)


def _qualification(s,stock_daily,spy,ts):
    reasons=[];confirmed,count=_confirmation(s)
    if not confirmed:reasons.append(f'confirmation {count}/3')
    market_ok=_market_favorable(spy,ts)
    if not market_ok:reasons.append('market regime unfavorable')
    weekly=True
    if s.strategy=='SWING':
        weekly=_weekly_alignment(stock_daily)
        if not weekly:reasons.append('weekly trend not aligned')
    return len(reasons)==0,reasons,{'confirmation_count':count,'market_favorable':market_ok,'weekly_aligned':weekly}


def _eligible_row(x,score,rr,qualified=True):
    return int(x['score'])>=score and float(x['rr1'])>=rr and (not qualified or bool(x.get('qualification_passed')))


def _view(rows,score,rr,qualified=True):
    return _stats([x for x in rows if _eligible_row(x,score,rr,qualified)])


def _row(symbol,strategy,ts,s,o,qualification):
    passed,reasons,meta=qualification
    return {'symbol':symbol,'strategy':strategy,'setup_type':s.setup_type,'timestamp':pd.Timestamp(ts).isoformat(),'date':str(pd.Timestamp(ts).date()),'score':int(s.raw_score),'rr1':round(float(s.rr1),3),'entry':round(float(s.price),4),'stop':round(float(s.stop_loss),4),'target1':round(float(s.target1),4),'target2':round(float(s.target2),4),'qualification_passed':passed,'qualification_reasons':reasons,**meta,'outcome':o[0],'return_pct':o[1],'r_multiple':o[2]}


def _daily_history_at(daily,ts):
    if daily is None or daily.empty:return daily
    d=pd.Timestamp(ts)
    try:
        if getattr(daily.index,'tz',None) is not None and d.tzinfo is None:d=d.tz_localize(daily.index.tz)
        if getattr(daily.index,'tz',None) is None and d.tzinfo is not None:d=d.tz_localize(None)
        return daily.loc[daily.index<=d]
    except Exception:return daily


def _swing(symbol,df,spy,cost):
    rows=[];last_signal_i=-99
    for i in range(220,len(df)-6):
        if i-last_signal_i<3:continue
        hist=df.iloc[:i+1];s=swing_setup(symbol,hist)
        if not _collectable(s):continue
        o=_outcome(s,df.iloc[i+1:i+6],cost)
        if o:
            rows.append(_row(symbol,'SWING',df.index[i],s,o,_qualification(s,hist,spy,df.index[i])));last_signal_i=i
    return rows


def _day(symbol,df,daily,spy,cost):
    rows=[];last_day=None
    if len(df)<100:return rows
    for i in range(60,len(df)-4,4):
        signal_time=df.index[i]
        if last_day==signal_time.date():continue
        history=df.iloc[:i+1].tail(350);s=intraday_setup(symbol,history)
        if not _collectable(s):continue
        same_day=df[(df.index>signal_time)&(df.index.date==signal_time.date())].head(12)
        if same_day.empty:continue
        o=_outcome(s,same_day,cost)
        if o:
            stock_daily=_daily_history_at(daily,signal_time)
            rows.append(_row(symbol,'DAY',signal_time,s,o,_qualification(s,stock_daily,spy,signal_time)));last_day=signal_time.date()
    return rows


def _one(symbol,years,spy,cost):
    rows=[];daily=pd.DataFrame()
    try:
        daily=_norm(yf.download(symbol,period=f'{years}y',interval='1d',auto_adjust=False,progress=False,threads=False,timeout=25),symbol)
        rows.extend(_swing(symbol,daily,spy,cost))
    except Exception as e:print('BT SWING',symbol,e)
    try:
        intra=_norm(yf.download(symbol,period='60d',interval='15m',auto_adjust=False,progress=False,threads=False,timeout=25),symbol)
        rows.extend(_day(symbol,intra,daily,spy,cost))
    except Exception as e:print('BT DAY',symbol,e)
    return rows


def _split(rows):
    if not rows:return [],[],None
    ordered=sorted(rows,key=lambda x:x['timestamp']);dates=sorted(set(x['date'] for x in ordered))
    cut_idx=max(1,min(len(dates)-1,int(len(dates)*.70))) if len(dates)>1 else 1;cut_date=dates[cut_idx-1]
    return [x for x in ordered if x['date']<=cut_date],[x for x in ordered if x['date']>cut_date],cut_date


def _sensitivity(rows,qualified=True):
    out=[]
    for score in SCORE_GRID:
        for rr in RR_GRID:out.append({'score':score,'min_rr':rr,**_view(rows,score,rr,qualified)})
    return out


def _rank_validation(rows):
    ranked=[]
    for x in rows:
        n=int(x['samples']);pf=float(x['profit_factor_r']);exp=float(x['expectancy_r']);dd=float(x['max_drawdown_r']);sample_factor=min(1.0,n/50.0)
        quality=(max(-1.0,min(2.0,exp))*2.0+max(0.0,min(3.0,pf-1.0))-min(3.0,dd/10.0))*sample_factor
        ranked.append({**x,'validation_quality_score':round(quality,3),'sample_warning':n<30})
    return sorted(ranked,key=lambda x:(x['validation_quality_score'],x['samples']),reverse=True)


def run_backtest():
    cfg=yaml.safe_load((ROOT/'config/settings.yml').read_text())['settings'];universe=yaml.safe_load((ROOT/'config/universe.yml').read_text())['universe']
    live_score=int(cfg.get('backtest_min_score',85));live_rr=float(cfg.get('min_risk_reward',2));years=int(cfg.get('backtest_years',3));cost=float(cfg.get('backtest_transaction_cost_bps',10));rows=[]
    spy=_norm(yf.download('SPY',period=f'{years}y',interval='1d',auto_adjust=False,progress=False,threads=False,timeout=25),'SPY')
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut={pool.submit(_one,s,years,spy,cost):s for s in universe}
        for f in as_completed(fut):
            try:rows.extend(f.result())
            except Exception as e:print('BT',fut[f],e)
    rows=sorted(rows,key=lambda x:x['timestamp']);dev,val,cut_date=_split(rows)
    sensitivity_dev=_sensitivity(dev,True);sensitivity_val=_sensitivity(val,True);val_ranked=_rank_validation(sensitivity_val)
    top_validated=[x for x in val_ranked if x['samples']>=30 and x['expectancy_r']>0 and x['profit_factor_r']>1][:10]
    live_dev=_view(dev,live_score,live_rr,True);live_val=_view(val,live_score,live_rr,True);live_all=_view(rows,live_score,live_rr,True)
    live_val_rows=[x for x in val if _eligible_row(x,live_score,live_rr,True)]
    out={'engine':ENGINE,'method':'Historical qualification-aware validation. Candidate-first score/RR sensitivity plus no-lookahead confirmation, SPY bullish-regime gate, SWING weekly alignment, conservative stop-first handling and transaction costs. These gates mirror important live qualification protections without using current benchmark data for past decisions.','collection_floor':{'score':COLLECT_MIN_SCORE,'min_rr':COLLECT_MIN_RR},'live_parameters':{'score':live_score,'min_rr':live_rr},'transaction_cost_bps':cost,'years_requested':years,'candidate_pool_samples':len(rows),'qualified_pool_samples':sum(bool(x.get('qualification_passed')) for x in rows),'split_date':cut_date,'candidate_pool_raw':_stats(rows),'qualified_pool':_stats([x for x in rows if x.get('qualification_passed')]),'development_pool_qualified':_stats([x for x in dev if x.get('qualification_passed')]),'validation_pool_qualified':_stats([x for x in val if x.get('qualification_passed')]),'live_parameters_overall':live_all,'live_parameters_development':live_dev,'live_parameters_validation':live_val,'live_validation_by_strategy':{n:_stats([x for x in live_val_rows if x['strategy']==n]) for n in ('DAY','SWING')},'live_validation_by_setup_type':{n:_stats([x for x in live_val_rows if x['setup_type']==n]) for n in ('BREAKOUT','PULLBACK')},'qualification_failure_counts':{},'development_sensitivity':sensitivity_dev,'validation_sensitivity':sensitivity_val,'top_validated_parameter_sets':top_validated,'validation_samples':live_val_rows[-1000:]}
    for x in val:
        if not x.get('qualification_passed'):
            for reason in x.get('qualification_reasons',[]):out['qualification_failure_counts'][reason]=out['qualification_failure_counts'].get(reason,0)+1
    (ROOT/'data/backtest.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    summary={k:v for k,v in out.items() if k not in ('development_sensitivity','validation_sensitivity','validation_samples')};print(json.dumps(summary,indent=2));return 0
