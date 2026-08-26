from __future__ import annotations
import math
def size_position(price,stop,equity,risk_pct,max_position_pct,available_cash):
    rps=max(price-stop,.01); risk_dollars=max(equity*(risk_pct/100),0); by_risk=math.floor(risk_dollars/rps)
    cap=min(equity*(max_position_pct/100),available_cash); by_cash=math.floor(cap/price) if price>0 else 0; shares=max(0,min(by_risk,by_cash))
    return {'shares':shares,'position_value':round(shares*price,2),'risk_dollars':round(shares*rps,2),'risk_pct_equity':round(shares*rps/max(equity,.01)*100,3)}
def portfolio_gate(portfolio,symbol,proposed_value,equity,max_total_exposure_pct,max_open_positions):
    pos=portfolio.get('positions',[])
    if any(p['symbol']==symbol for p in pos):return False,'Already held'
    if len(pos)>=max_open_positions:return False,'Max open positions reached'
    exp=sum(float(p.get('qty',0))*float(p.get('avg_price',0)) for p in pos)
    if exp+proposed_value>equity*(max_total_exposure_pct/100):return False,'Portfolio exposure limit'
    return True,'OK'
