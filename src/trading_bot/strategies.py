from __future__ import annotations
from dataclasses import dataclass,asdict
import pandas as pd
from .indicators import add_intraday,add_daily
@dataclass
class Setup:
    symbol:str; strategy:str; raw_score:int; price:float; entry_low:float; entry_high:float; stop_loss:float; target1:float; target2:float; rr1:float; rr2:float; atr:float; rsi:float; vol_ratio:float; momentum:float; reasons:list[str]; warnings:list[str]
    def to_dict(self):return asdict(self)
def safe(v,d=0.0):
    try:return d if pd.isna(v) else float(v)
    except Exception:return d

def _levels(df,p,a,strategy):
    look=20 if strategy=='DAY' else 30
    r=df.iloc[-look:-1] if len(df)>look else df.iloc[:-1]
    support=safe(r['Low'].min(),p-a); resistance=safe(r['High'].max(),p+2*a)
    if strategy=='DAY':
        vol_stop=p-1.4*a; structure_stop=support-.15*a; floor=p-max(2.0*a,p*.02)
    else:
        vol_stop=p-1.7*a; structure_stop=support-.20*a; floor=p-max(2.8*a,p*.06)
    stop=max(floor,min(vol_stop,structure_stop if structure_stop<p else vol_stop))
    risk=max(p-stop,.01)
    raw_t1=p+2*risk; raw_t2=p+3*risk
    # Do not pretend a target is attractive when obvious resistance is directly in front.
    t1=raw_t1 if resistance<=p or resistance>=p+1.5*risk else resistance
    t2=max(t1,raw_t2)
    rr1=(t1-p)/risk; rr2=(t2-p)/risk
    return stop,t1,t2,rr1,rr2,support,resistance

def intraday_setup(symbol,df):
    if len(df)<55:return None
    x=add_intraday(df);r=x.iloc[-1];p=safe(r['Close']);e9,e21,e50=map(safe,[r['EMA9'],r['EMA21'],r['EMA50']]);rs=safe(r['RSI14'],50);a=safe(r['ATR14'],p*.02);vw=safe(r['VWAP'],p);vr=safe(r['VOL_RATIO'],1);mom=safe(r['RET_1H'],0);h20=safe(r['HIGH20'],p)
    sc=0;reasons=[];warnings=[]
    if e9>e21>e50:sc+=25;reasons.append('EMA 9/21/50 bullish alignment')
    elif e9>e21:sc+=16;reasons.append('Short-term EMA momentum')
    else:warnings.append('EMA momentum not confirmed')
    if p>vw:sc+=18;reasons.append('Price above VWAP')
    else:warnings.append('Below VWAP')
    if 52<=rs<=68:sc+=17;reasons.append(f'RSI momentum {rs:.1f}')
    elif 45<=rs<52:sc+=8
    elif rs>72:warnings.append('RSI overextended')
    if vr>=1.8:sc+=18;reasons.append(f'Relative volume {vr:.2f}x')
    elif vr>=1.2:sc+=11;reasons.append(f'Volume confirmation {vr:.2f}x')
    if mom>=.8:sc+=12;reasons.append(f'1h momentum +{mom:.2f}%')
    elif mom>0:sc+=6
    breakout=p>h20 and h20>0
    if breakout and vr>=1.2:sc+=10;reasons.append('Confirmed 20-bar breakout')
    elif breakout:warnings.append('Breakout without enough volume confirmation')
    stop,t1,t2,rr1,rr2,_,_= _levels(x,p,a,'DAY')
    return Setup(symbol,'DAY',min(100,sc),p,p-.10*a,p+.06*a,stop,t1,t2,rr1,rr2,a,rs,vr,mom,reasons,warnings)

def swing_setup(symbol,df):
    if len(df)<55:return None
    x=add_daily(df);r=x.iloc[-1];p=safe(r['Close']);e20,e50,e200=map(safe,[r['EMA20'],r['EMA50'],r['EMA200']]);rs=safe(r['RSI14'],50);a=safe(r['ATR14'],p*.025);vr=safe(r['VOL_RATIO'],1);mom=safe(r['RET_5D'],0);h20=safe(r['HIGH20'],p)
    sc=0;reasons=[];warnings=[]
    if p>e20>e50 and (e200==0 or e50>e200):sc+=30;reasons.append('Daily trend bullish')
    elif p>e20>e50:sc+=24;reasons.append('Price above EMA20/50')
    else:warnings.append('Daily trend not fully aligned')
    if 50<=rs<=65:sc+=18;reasons.append(f'Daily RSI {rs:.1f}')
    elif 45<=rs<50:sc+=8
    elif rs>72:warnings.append('Daily RSI overbought')
    if vr>=1.5:sc+=16;reasons.append(f'Daily volume {vr:.2f}x')
    elif vr>=1.1:sc+=9
    if mom>=2:sc+=14;reasons.append(f'5-day momentum +{mom:.2f}%')
    elif mom>0:sc+=7
    breakout=p>h20 and h20>0
    if breakout and vr>=1.1:sc+=12;reasons.append('Confirmed 20-day breakout')
    elif breakout:warnings.append('Breakout lacks volume confirmation')
    if p>e20:sc+=10
    stop,t1,t2,rr1,rr2,_,_= _levels(x,p,a,'SWING')
    return Setup(symbol,'SWING',min(100,sc),p,p-.16*a,p+.08*a,stop,t1,t2,rr1,rr2,a,rs,vr,mom,reasons,warnings)
