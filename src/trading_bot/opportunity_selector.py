from __future__ import annotations

def _group_for(symbol,groups):
    for name,symbols in (groups or {}).items():
        if symbol in symbols:return name
    return 'OTHER'

def select(signals,groups,max_items=3):
    candidates=[x for x in signals if x.get('signal') in ('BUY','STRONG_BUY') and x.get('action') in ('BUY_NOW','WAIT_FOR_ENTRY')]
    candidates=sorted(candidates,key=lambda x:(x.get('score',0),x.get('signal')=='STRONG_BUY',x.get('confirmation_passed',False)),reverse=True)
    picked=[];used_groups=set();used_strategies={}
    for x in candidates:
        group=_group_for(x.get('symbol'),groups)
        strategy=x.get('strategy','')
        if group in used_groups:continue
        if used_strategies.get(strategy,0)>=2:continue
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
