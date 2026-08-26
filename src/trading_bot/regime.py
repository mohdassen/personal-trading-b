from __future__ import annotations
from dataclasses import dataclass
from .market import quote_daily
from .indicators import add_daily
@dataclass
class MarketRegime:
    label:str; score_adjustment:int; spy_trend:str; qqq_trend:str; vix:float; notes:list[str]
def _trend(symbol):
    df=quote_daily(symbol)
    if len(df)<55:return 'UNKNOWN',0.0
    r=add_daily(df).iloc[-1]; c=float(r['Close']); e20=float(r['EMA20']); e50=float(r['EMA50'])
    if c>e20>e50:return 'BULLISH',c
    if c<e20<e50:return 'BEARISH',c
    return 'MIXED',c
def detect():
    spy,_=_trend('SPY'); qqq,_=_trend('QQQ')
    try:
        d=quote_daily('^VIX'); vix=float(d['Close'].iloc[-1]) if not d.empty else 20.0
    except Exception:vix=20.0
    notes=[f'SPY {spy}',f'QQQ {qqq}',f'VIX {vix:.1f}']
    if spy=='BULLISH' and qqq=='BULLISH' and vix<25:return MarketRegime('RISK_ON',8,spy,qqq,vix,notes)
    if spy=='BEARISH' and qqq=='BEARISH':return MarketRegime('RISK_OFF',-18,spy,qqq,vix,notes)
    if vix>=30:return MarketRegime('HIGH_VOLATILITY',-15,spy,qqq,vix,notes)
    return MarketRegime('MIXED',-3,spy,qqq,vix,notes)
