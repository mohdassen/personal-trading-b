from __future__ import annotations
GRADE={'A+':4,'A':3,'B':2,'C':1}
ACTION={'BUY_NOW':5,'WAIT_FOR_ENTRY':4,'DO_NOT_CHASE':3,'WATCH':2,'NO_TRADE':0}
def _group_for(symbol,groups):
    for name,symbols in (groups or {}).items():
        if symbol in symbols:return name
    return 'OTHER'
def _rank(x):
    return (GRADE.get(x.get('quality_grade','C'),0),ACTION.get(x.get('action',''),0),int(x.get('score',0)),bool(x.get('confirmation_passed',False)),float(x.get('precision',{}).get('relative_strength',{}).get('vs_market',0)))
def _diverse(candidates,groups,max_items):
    candidates=sorted(candidates,key=_rank,reverse=True);picked=[];used_groups=set();used_strategies={}
    for x in candidates:
        group=_group_for(x.get('symbol'),groups);strategy=x.get('strategy','')
        if group in used_groups or used_strategies.get(strategy,0)>=2:continue
        y=dict(x);y['opportunity_group']=group;y['opportunity_rank']=len(picked)+1
        picked.append(y);used_groups.add(group);used_strategies[strategy]=used_strategies.get(strategy,0)+1
        if len(picked)>=max_items:return picked
    chosen={x['symbol'] for x in picked}
    for x in candidates:
        if x.get('symbol') in chosen:continue
        y=dict(x);y['opportunity_group']=_group_for(x.get('symbol'),groups);y['opportunity_rank']=len(picked)+1
        picked.append(y);chosen.add(x.get('symbol'))
        if len(picked)>=max_items:break
    return picked
def select(signals,groups,max_items=3):
    """Strict actionable list. Paper trading and BUY alerts use only this list."""
    candidates=[x for x in signals if x.get('signal') in ('BUY','STRONG_BUY') and x.get('quality_grade') in ('A+','A','B') and x.get('action') in ('BUY_NOW','WAIT_FOR_ENTRY')]
    return _diverse(candidates,groups,max_items)
def watchlist(signals,groups,max_items=5):
    """High-quality names worth watching even when entry timing blocks a trade."""
    candidates=[]
    for x in signals:
        if int(x.get('score',0))<87:continue
        if x.get('event_risk')=='HIGH' or x.get('validation_blocked'):continue
        if x.get('action') not in ('WATCH','DO_NOT_CHASE','WAIT_FOR_ENTRY'):continue
        rs=float(x.get('precision',{}).get('relative_strength',{}).get('vs_market',0))
        market=x.get('market_regime_v2')
        if rs<1 or market=='RISK_OFF':continue
        y=dict(x)
        p=float(y.get('price',0));hi=float(y.get('entry_high',p));atr=max(float(y.get('atr',1)),.01)
        y['distance_to_entry_pct']=round((p-hi)/p*100,2) if p else 0
        y['distance_to_entry_atr']=round((p-hi)/atr,2)
        candidates.append(y)
    return _diverse(candidates,groups,max_items)
