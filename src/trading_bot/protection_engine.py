from __future__ import annotations
from datetime import datetime,timezone,timedelta

def _parse(ts):
    try:
        x=datetime.fromisoformat(ts)
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    except Exception:return None

def evaluate(paper_book,settings):
    closed=list((paper_book or {}).get('closed',[]));now=datetime.now(timezone.utc);reasons=[];locks=[]
    lookback=int(settings.get('protection_lookback_trades',12));recent=closed[-lookback:]
    stop_limit=int(settings.get('stoploss_guard_count',3));cooldown_h=float(settings.get('stoploss_guard_hours',6));stops=[x for x in recent if x.get('result')=='STOP']
    if len(stops)>=stop_limit:
        last=_parse(stops[-1].get('closed_at',''))
        if last and now-last<timedelta(hours=cooldown_h):
            reasons.append(f'Stoploss guard: {len(stops)} recent stops');locks.append('ALL')
    rs=[float(x.get('r_multiple',0)) for x in recent]
    curve=0.0;peak=0.0;dd=0.0
    for r in rs:
        curve+=r;peak=max(peak,curve);dd=max(dd,peak-curve)
    max_dd=float(settings.get('protection_max_drawdown_r',4.0))
    if len(recent)>=6 and dd>=max_dd:
        reasons.append(f'Max drawdown guard: {dd:.2f}R');locks.append('ALL')
    low_min=int(settings.get('low_profit_min_samples',8));low_exp=float(settings.get('low_profit_expectancy_r',-.10));disabled=[]
    for field,name in (('strategy','DAY'),('strategy','SWING'),('setup_type','BREAKOUT'),('setup_type','PULLBACK')):
        rows=[x for x in closed if x.get(field)==name][-20:]
        if len(rows)>=low_min:
            exp=sum(float(x.get('r_multiple',0)) for x in rows)/len(rows)
            if exp<=low_exp:
                disabled.append(name);reasons.append(f'{name} paused: expectancy {exp:.2f}R over {len(rows)} trades')
    return {'blocked':bool(locks),'locks':sorted(set(locks)),'disabled':sorted(set(disabled)),'reasons':reasons,'recent_trades':len(recent),'recent_drawdown_r':round(dd,2),'generated_at':now.isoformat()}
