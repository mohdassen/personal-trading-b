from __future__ import annotations
from .event_risk import assess
from .risk import size_position,portfolio_gate
def finalize(setup,regime,settings,portfolio,equity):
    event=assess(setup.symbol,int(settings.get('earnings_block_days',2)))
    score=max(0,min(100,setup.raw_score+regime.score_adjustment+event.score_adjustment))
    strong=int(settings.get('strong_buy_score',90)); buy=int(settings.get('buy_score',82)); watch=int(settings.get('watch_score',70)); rr=float(settings.get('min_risk_reward',2))
    if event.level=='HIGH':signal='BLOCKED'
    elif score>=strong and setup.rr1>=rr:signal='STRONG_BUY'
    elif score>=buy and setup.rr1>=rr:signal='BUY'
    elif score>=watch:signal='WATCH'
    else:signal='WAIT'
    cash=float(portfolio.get('cash',equity)); sizing=size_position(setup.price,setup.stop_loss,equity,float(settings.get('risk_per_trade_pct',.5)),float(settings.get('max_position_pct',15)),cash)
    gate,reason=portfolio_gate(portfolio,setup.symbol,sizing['position_value'],equity,float(settings.get('max_total_exposure_pct',60)),int(settings.get('max_open_positions',5)))
    if signal in ('BUY','STRONG_BUY') and not gate:signal='BLOCKED'
    return {**setup.to_dict(),'score':score,'signal':signal,'market_regime':regime.label,'event_risk':event.level,'event_notes':event.notes,'market_notes':regime.notes,
            'suggested_shares':sizing['shares'],'suggested_value':sizing['position_value'],'risk_dollars':sizing['risk_dollars'],'risk_pct_equity':sizing['risk_pct_equity'],'portfolio_gate':reason}
