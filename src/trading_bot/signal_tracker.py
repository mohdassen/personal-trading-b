from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
from .market import quote_daily,quote_intraday

def _load(path,default):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:return default
def _write(path,data):Path(path).write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
def _key(x):return f"{x.get('timestamp','')}:{x.get('symbol')}:{x.get('strategy')}"
def _future_data(s,ts,horizon_days):
    if s.get('strategy')=='DAY':
        df=quote_intraday(s['symbol'],'5d','15m');return df[df.index>ts].head(26)
    df=quote_daily(s['symbol']);return df[df.index>ts].head(max(1,int(horizon_days)))
def evaluate(data_dir,horizon_days=5,min_score=85):
    d=Path(data_dir);hist=_load(d/'signal_history.json',[]);old=_load(d/'signal_outcomes.json',[]);known={x.get('key') for x in old};out=list(old);now=datetime.now(timezone.utc)
    for s in hist:
        # Accuracy means an actionable recommendation, not a watch/wait signal.
        if s.get('signal') not in ('BUY','STRONG_BUY') or s.get('action')!='BUY_NOW' or int(s.get('score',0))<int(min_score):continue
        key=_key(s)
        if key in known:continue
        try:
            ts=datetime.fromisoformat(s['timestamp']);ts=ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc);age=(now-ts).total_seconds()/86400
            min_age=.30 if s.get('strategy')=='DAY' else 1
            if age<min_age:continue
            future=_future_data(s,ts,horizon_days)
            if future.empty:continue
            entry=float(s['price']);stop=float(s['stop_loss']);t1=float(s['target1']);t2=float(s['target2']);risk=max(entry-stop,.01);result='OPEN';exit_price=float(future['Close'].iloc[-1]);bars=0
            for i,(_,r) in enumerate(future.iterrows(),1):
                lo=float(r['Low']);hi=float(r['High'])
                if lo<=stop:result='STOP';exit_price=stop;bars=i;break
                if hi>=t2:result='TARGET2';exit_price=t2;bars=i;break
                if hi>=t1:result='TARGET1';exit_price=t1;bars=i;break
            expired=(s.get('strategy')=='DAY' and age>=.8) or (s.get('strategy')!='DAY' and age>=horizon_days)
            if result=='OPEN' and expired:result='TIME_EXIT';bars=len(future)
            if result=='OPEN':continue
            ret=(exit_price-entry)/entry*100;r_mult=(exit_price-entry)/risk
            out.append({'key':key,'timestamp':s['timestamp'],'engine':s.get('engine'),'symbol':s['symbol'],'strategy':s['strategy'],'setup_type':s.get('setup_type'),'market':s.get('market'),'grade':s.get('grade'),'signal':s['signal'],'score':s['score'],'entry':entry,'result':result,'return_pct':round(ret,2),'r_multiple':round(r_mult,2),'bars':bars,'evaluated_at':now.isoformat()});known.add(key)
        except Exception as e:print('Signal tracker',s.get('symbol'),e)
    _write(d/'signal_outcomes.json',out[-10000:]);return summarize(out)
def _stats(rows):
    if not rows:return {'samples':0,'win_rate':0.0,'avg_return_pct':0.0,'avg_r':0.0,'target_rate':0.0,'stop_rate':0.0}
    wins=[x for x in rows if float(x.get('r_multiple',x.get('return_pct',0)))>0];targets=[x for x in rows if str(x.get('result','')).startswith('TARGET')];stops=[x for x in rows if x.get('result')=='STOP']
    return {'samples':len(rows),'win_rate':round(len(wins)/len(rows)*100,1),'avg_return_pct':round(sum(float(x.get('return_pct',0)) for x in rows)/len(rows),2),'avg_r':round(sum(float(x.get('r_multiple',0)) for x in rows)/len(rows),2),'target_rate':round(len(targets)/len(rows)*100,1),'stop_rate':round(len(stops)/len(rows)*100,1)}
def summarize(rows):
    r=[x for x in rows if x.get('result') in ('STOP','TARGET1','TARGET2','TIME_EXIT')];out=_stats(r);out['wins']=sum(float(x.get('r_multiple',x.get('return_pct',0)))>0 for x in r);out['losses']=len(r)-out['wins'];out['by_strategy']={n:_stats([x for x in r if x.get('strategy')==n]) for n in ('DAY','SWING')};out['by_setup_type']={n:_stats([x for x in r if x.get('setup_type')==n]) for n in ('BREAKOUT','PULLBACK')};out['by_market']={n:_stats([x for x in r if x.get('market')==n]) for n in sorted({x.get('market') for x in r if x.get('market')})};out['by_grade']={n:_stats([x for x in r if x.get('grade')==n]) for n in ('A+','A','B','C')};return out
