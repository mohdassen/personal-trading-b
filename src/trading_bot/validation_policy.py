from __future__ import annotations


def _policy(stats, qualification_aware=False):
    s=int((stats or {}).get('samples',0));pf=float((stats or {}).get('profit_factor_r',0));avg=float((stats or {}).get('avg_r',0))
    # V4.4 qualification-aware rows already passed historical market/confirmation
    # gates, so a strongly negative out-of-sample result is meaningful even with
    # a smaller sample. Use a conservative fail-safe without rewarding small wins.
    if qualification_aware:
        if s>=10 and pf<0.50 and avg<-.20:
            return {'samples':s,'adjustment':-12,'disabled':True,'reason':'qualification validation strongly negative'}
        if s>=15 and (pf<0.75 or avg<-.15):
            return {'samples':s,'adjustment':-12,'disabled':True,'reason':'qualification validation materially negative'}
        if s>=10 and (pf<0.90 or avg<-.05):
            return {'samples':s,'adjustment':-8,'disabled':False,'reason':'qualification validation weak'}
        if s<20:return {'samples':s,'adjustment':0,'disabled':False,'reason':'insufficient validation sample'}
    else:
        if s<20:return {'samples':s,'adjustment':0,'disabled':False,'reason':'insufficient validation sample'}
    if s>=30 and (pf<0.75 or avg<-.15):return {'samples':s,'adjustment':-12,'disabled':True,'reason':'validation materially negative'}
    if pf<0.90 or avg<-.05:return {'samples':s,'adjustment':-8,'disabled':False,'reason':'validation weak'}
    if pf<1.05 or avg<=0:return {'samples':s,'adjustment':-4,'disabled':False,'reason':'validation marginal'}
    if s>=30 and pf>=1.30 and avg>=.10:return {'samples':s,'adjustment':2,'disabled':False,'reason':'validation strong'}
    return {'samples':s,'adjustment':0,'disabled':False,'reason':'validation acceptable'}


def derive(backtest):
    backtest=backtest or {}
    qualification_aware=str(backtest.get('engine','')).startswith('V4.4')
    # V4.4+ publishes the actual out-of-sample live-parameter views under these
    # names. Fall back to legacy keys for older backtest files.
    strategy_stats=backtest.get('live_validation_by_strategy') or backtest.get('by_strategy',{})
    setup_stats=backtest.get('live_validation_by_setup_type') or backtest.get('by_setup_type',{})
    strategies={name:_policy(stats,qualification_aware) for name,stats in strategy_stats.items()}
    setups={name:_policy(stats,qualification_aware) for name,stats in setup_stats.items()}
    return {
        'source_engine':backtest.get('engine'),
        'qualification_aware':qualification_aware,
        'strategies':strategies,
        'setup_types':setups,
        'disabled_strategies':[k for k,v in strategies.items() if v['disabled']],
        'disabled_setup_types':[k for k,v in setups.items() if v['disabled']],
        'strategy_adjustments':{k:v['adjustment'] for k,v in strategies.items()},
        'setup_adjustments':{k:v['adjustment'] for k,v in setups.items()},
    }
