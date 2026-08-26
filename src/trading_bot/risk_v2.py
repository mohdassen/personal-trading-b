from __future__ import annotations
from datetime import datetime, timezone

def quality_risk_multiplier(grade,market_label):
    base={'A+':1.0,'A':0.85,'B':0.60,'C':0.0}.get(grade,0.5)
    if market_label in ('RISK_OFF','CHOPPY'):base*=0.5
    elif market_label=='CAUTIOUS_RISK_ON':base*=0.8
    return round(max(0,min(1,base)),2)

def portfolio_correlation_gate(portfolio,symbol_group,max_same_group=2):
    groups=[str(p.get('group','OTHER')) for p in portfolio.get('positions',[])]
    same=sum(1 for g in groups if g==symbol_group)
    if same>=max_same_group:return False,'Too much exposure to same market group'
    return True,'OK'

def daily_loss_guard(trades,equity,max_daily_loss_pct=1.5):
    today=datetime.now(timezone.utc).date().isoformat();pnl=0.0
    for t in trades or []:
        ts=str(t.get('timestamp') or t.get('date') or '')
        if ts.startswith(today):pnl+=float(t.get('realized_pnl',0) or 0)
    pct=pnl/max(float(equity),.01)*100
    if pct<=-abs(max_daily_loss_pct):return False,round(pct,2)
    return True,round(pct,2)

def scale_sizing(sizing,multiplier):
    m=max(0,min(1,float(multiplier)));shares=int(sizing.get('shares',0)*m)
    if shares<=0:return {**sizing,'shares':0,'position_value':0.0,'risk_dollars':0.0,'risk_pct_equity':0.0}
    original=max(int(sizing.get('shares',0)),1);ratio=shares/original
    return {**sizing,'shares':shares,'position_value':round(float(sizing.get('position_value',0))*ratio,2),'risk_dollars':round(float(sizing.get('risk_dollars',0))*ratio,2),'risk_pct_equity':round(float(sizing.get('risk_pct_equity',0))*ratio,3)}
