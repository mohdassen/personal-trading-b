from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from .market import quote_daily

def _load(path, default):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:return default

def _write(path,data):Path(path).write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')

def _key(x):return f"{x.get('timestamp','')}:{x.get('symbol')}:{x.get('strategy')}"

def evaluate(data_dir,horizon_days=5,min_score=85):
    d=Path(data_dir); hist=_load(d/'signal_history.json',[]); old=_load(d/'signal_outcomes.json',[]); known={x.get('key') for x in old}
    out=list(old); now=datetime.now(timezone.utc)
    for s in hist:
        if s.get('signal') not in ('BUY','STRONG_BUY') or int(s.get('score',0))<int(min_score):continue
        key=_key(s)
        if key in known:continue
        try:
            ts=datetime.fromisoformat(s['timestamp'])
            if ts.tzinfo is None:ts=ts.replace(tzinfo=timezone.utc)
            age=(now-ts).total_seconds()/86400
            if age<1:continue
            df=quote_daily(s['symbol'])
            future=df[df.index>=ts]
            if future.empty:continue
            future=future.head(max(1,int(horizon_days)))
            entry=float(s['price']); stop=float(s['stop_loss']); t1=float(s['target1']); t2=float(s['target2'])
            result='OPEN'; exit_price=float(future['Close'].iloc[-1]); bars=0
            # Conservative ordering: if stop and target are touched in same daily bar, count STOP first.
            for i,(_,r) in enumerate(future.iterrows(),1):
                lo=float(r['Low']); hi=float(r['High'])
                if lo<=stop:result='STOP';exit_price=stop;bars=i;break
                if hi>=t2:result='TARGET2';exit_price=t2;bars=i;break
                if hi>=t1:result='TARGET1';exit_price=t1;bars=i;break
            if result=='OPEN' and age>=horizon_days:
                result='TIME_EXIT';bars=len(future)
            if result=='OPEN':continue
            ret=(exit_price-entry)/entry*100
            out.append({'key':key,'timestamp':s['timestamp'],'symbol':s['symbol'],'strategy':s['strategy'],'signal':s['signal'],'score':s['score'],'entry':entry,'result':result,'return_pct':round(ret,2),'bars':bars,'evaluated_at':now.isoformat()})
            known.add(key)
        except Exception as e:print('Signal tracker',s.get('symbol'),e)
    _write(d/'signal_outcomes.json',out[-10000:])
    return summarize(out)

def summarize(rows):
    resolved=[x for x in rows if x.get('result') in ('STOP','TARGET1','TARGET2','TIME_EXIT')]
    wins=[x for x in resolved if float(x.get('return_pct',0))>0]; losses=[x for x in resolved if float(x.get('return_pct',0))<=0]
    by_strategy={}
    for name in ('DAY','SWING'):
        z=[x for x in resolved if x.get('strategy')==name]; w=[x for x in z if float(x.get('return_pct',0))>0]
        by_strategy[name]={'samples':len(z),'win_rate':round(len(w)/len(z)*100,1) if z else 0.0,'avg_return_pct':round(sum(float(x.get('return_pct',0)) for x in z)/len(z),2) if z else 0.0}
    return {'samples':len(resolved),'wins':len(wins),'losses':len(losses),'win_rate':round(len(wins)/len(resolved)*100,1) if resolved else 0.0,'avg_return_pct':round(sum(float(x.get('return_pct',0)) for x in resolved)/len(resolved),2) if resolved else 0.0,'by_strategy':by_strategy}
