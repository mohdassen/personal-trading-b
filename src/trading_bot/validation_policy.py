from __future__ import annotations

def _policy(stats):
    s=int((stats or {}).get('samples',0));pf=float((stats or {}).get('profit_factor_r',0));avg=float((stats or {}).get('avg_r',0))
    if s<20:return {'samples':s,'adjustment':0,'disabled':False,'reason':'insufficient validation sample'}
    if s>=30 and (pf<0.75 or avg<-.15):return {'samples':s,'adjustment':-12,'disabled':True,'reason':'validation materially negative'}
    if pf<0.90 or avg<-.05:return {'samples':s,'adjustment':-8,'disabled':False,'reason':'validation weak'}
    if pf<1.05 or avg<=0:return {'samples':s,'adjustment':-4,'disabled':False,'reason':'validation marginal'}
    if s>=30 and pf>=1.30 and avg>=.10:return {'samples':s,'adjustment':2,'disabled':False,'reason':'validation strong'}
    return {'samples':s,'adjustment':0,'disabled':False,'reason':'validation acceptable'}

def derive(backtest):
    strategies={};setups={}
    for name,stats in (backtest or {}).get('by_strategy',{}).items():strategies[name]=_policy(stats)
    for name,stats in (backtest or {}).get('by_setup_type',{}).items():setups[name]=_policy(stats)
    return {
        'strategies':strategies,
        'setup_types':setups,
        'disabled_strategies':[k for k,v in strategies.items() if v['disabled']],
        'disabled_setup_types':[k for k,v in setups.items() if v['disabled']],
        'strategy_adjustments':{k:v['adjustment'] for k,v in strategies.items()},
        'setup_adjustments':{k:v['adjustment'] for k,v in setups.items()},
    }
