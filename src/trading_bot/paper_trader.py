from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
from .market import quote_daily,quote_intraday

def _load(p,d):
    try:return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception:return d

def _write(p,d):Path(p).write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding='utf-8')

def _now():return datetime.now(timezone.utc).isoformat()

def _key(x):return f"{x.get('symbol')}:{x.get('strategy')}:{x.get('setup_type','NA')}"

def sync(data_dir,signals,max_open=5):
    d=Path(data_dir);book=_load(d/'paper_trades.json',{'starting_equity':10000.0,'closed':[],'open':[]})
    open_keys={x['key'] for x in book['open']}
    for s in signals:
        if len(book['open'])>=max_open:break
        if s.get('action')!='BUY_NOW' or s.get('signal') not in ('BUY','STRONG_BUY'):continue
        key=_key(s)
        if key in open_keys:continue
        entry=float(s.get('planned_entry_price') or s['price']);stop=float(s['stop_loss']);t1=float(s['target1']);t2=float(s['target2'])
        risk=max(entry-stop,.01)
        book['open'].append({'id':f"{key}:{_now()}",'key':key,'symbol':s['symbol'],'strategy':s['strategy'],'setup_type':s.get('setup_type','NA'),'market':s.get('market_regime_v2'),'grade':s.get('quality_grade'),'score':s['score'],'entry':entry,'stop':stop,'target1':t1,'target2':t2,'risk_per_share':risk,'opened_at':_now(),'engine':s.get('engine','V4-Precision')})
        open_keys.add(key)
    still=[]
    for t in book['open']:
        try:
            df=quote_intraday(t['symbol'],'5d','15m') if t['strategy']=='DAY' else quote_daily(t['symbol'])
            if df.empty:still.append(t);continue
            opened=datetime.fromisoformat(t['opened_at']);idx=df.index
            if getattr(idx,'tz',None) is None:opened=opened.replace(tzinfo=None)
            future=df[idx>=opened]
            if future.empty:still.append(t);continue
            result=None;exit_price=None
            for _,r in future.iterrows():
                lo=float(r['Low']);hi=float(r['High'])
                if lo<=t['stop']:result='STOP';exit_price=t['stop'];break
                if hi>=t['target2']:result='TARGET2';exit_price=t['target2'];break
                if hi>=t['target1']:result='TARGET1';exit_price=t['target1'];break
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(t['opened_at'])).total_seconds()/3600
            expiry=6.5 if t['strategy']=='DAY' else 24*5
            if result is None and age>=expiry:result='TIME_EXIT';exit_price=float(future['Close'].iloc[-1])
            if result is None:still.append(t);continue
            r_mult=(exit_price-t['entry'])/t['risk_per_share']
            book['closed'].append({**t,'closed_at':_now(),'result':result,'exit':round(exit_price,4),'r_multiple':round(r_mult,3),'return_pct':round((exit_price-t['entry'])/t['entry']*100,3)})
        except Exception as e:
            print('Paper trader',t['symbol'],e);still.append(t)
    book['open']=still;book['updated_at']=_now();_write(d/'paper_trades.json',book)
    stats=summarize(book['closed']);_write(d/'paper_validation.json',stats);return stats

def _bucket(rows,field,value):return [x for x in rows if x.get(field)==value]
def _stats(rows):
    n=len(rows);wins=[x for x in rows if float(x.get('r_multiple',0))>0];rs=[float(x.get('r_multiple',0)) for x in rows]
    gp=sum(x for x in rs if x>0);gl=abs(sum(x for x in rs if x<0))
    return {'samples':n,'win_rate':round(len(wins)/n*100,1) if n else 0.0,'expectancy_r':round(sum(rs)/n,3) if n else 0.0,'profit_factor':round(gp/gl,2) if gl else (99.0 if gp else 0.0),'total_r':round(sum(rs),2)}
def summarize(rows):
    base=_stats(rows);base['by_strategy']={k:_stats(_bucket(rows,'strategy',k)) for k in ('DAY','SWING')};base['by_setup']={k:_stats(_bucket(rows,'setup_type',k)) for k in ('BREAKOUT','PULLBACK')}
    markets=sorted({x.get('market') for x in rows if x.get('market')});base['by_market']={k:_stats(_bucket(rows,'market',k)) for k in markets}
    # R-based equity curve and drawdown, independent of account size.
    curve=0.0;peak=0.0;max_dd=0.0
    for x in rows:
        curve+=float(x.get('r_multiple',0));peak=max(peak,curve);max_dd=max(max_dd,peak-curve)
    base['max_drawdown_r']=round(max_dd,2);base['status']='LEARNING' if base['samples']<30 else 'VALIDATING' if base['samples']<100 else 'MEASURED';base['updated_at']=_now();return base
