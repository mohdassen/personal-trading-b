from __future__ import annotations
def advice(position,current_price,vwap=None,ema21=None):
    entry=float(position['avg_price']);stop=float(position.get('stop',0) or 0);t1=float(position.get('target1',0) or 0);t2=float(position.get('target2',0) or 0);pnl=(current_price-entry)/entry*100 if entry else 0
    if stop and current_price<=stop:return {'action':'EXIT','reason':'Stop-loss reached','pnl_pct':round(pnl,2)}
    if t2 and current_price>=t2:return {'action':'EXIT','reason':'Target 2 reached','pnl_pct':round(pnl,2)}
    if t1 and current_price>=t1:return {'action':'TAKE_PARTIAL','reason':'Target 1 reached; consider selling 50% and raising stop','pnl_pct':round(pnl,2)}
    if vwap and ema21 and current_price<vwap and current_price<ema21:return {'action':'TIGHTEN/EXIT','reason':'Lost VWAP and EMA21','pnl_pct':round(pnl,2)}
    return {'action':'HOLD','reason':'No exit trigger','pnl_pct':round(pnl,2)}
