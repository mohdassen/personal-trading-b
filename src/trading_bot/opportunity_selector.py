from __future__ import annotations
GRADE={'A+':4,'A':3,'B':2,'C':1}
ACTION={'BUY_NOW':3,'WAIT_FOR_ENTRY':2,'DO_NOT_CHASE':1}
def _group_for(symbol,groups):
    for name,symbols in (groups or {}).items():
        if symbol in symbols:return name
    return 'OTHER'
def _rank(x):
    return (GRADE.get(x.get('quality_grade','C'),0),ACTION.get(x.get('action',''),0),int(x.get('score',0)),bool(x.get('confirmation_passed',False)),float(x.get('precision',{}).get('relative_strength',{}).get('vs_market',0)))
def select(signals,groups,max_items=3):
    candidates=[x for x in signals if x.get('signal') in ('BUY','STRONG_BUY') and x.get('quality_grade') in ('A+','A','B') and x.get('action') in ('BUY_NOW','WAIT_FOR_ENTRY')]
    candidates=sorted(candidates,key=_rank,reverse=True);picked=[];used_groups=set();used_strategies={}
    for x in candidates:
        group=_group_for(x.get('symbol'),groups);strategy=x.get('strategy','')
        if group in used_groups or used_strategies.get(strategy,0)>=2:continue
        y=dict(x);y['opportunity_group']=group;y['opportunity_rank']=len(picked)+1
        picked.append(y);used_groups.add(group);used_strategies[strategy]=used_strategies.get(strategy,0)+1
        if len(picked)>=max_items:break
    if len(picked)<max_items:
        chosen={x['symbol'] for x in picked}
        for x in candidates:
            if x.get('symbol') in chosen:continue
            y=dict(x);y['opportunity_group']=_group_for(x.get('symbol'),groups);y['opportunity_rank']=len(picked)+1
            picked.append(y);chosen.add(x.get('symbol'))
            if len(picked)>=max_items:break
    return picked
