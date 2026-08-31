from __future__ import annotations
from dataclasses import dataclass,asdict
import pandas as pd
from .indicators import add_intraday,add_daily

@dataclass
class Setup:
    symbol:str; strategy:str; raw_score:int; price:float; entry_low:float; entry_high:float; stop_loss:float; target1:float; target2:float; rr1:float; rr2:float; atr:float; rsi:float; vol_ratio:float; momentum:float; reasons:list[str]; warnings:list[str]; setup_type:str
    def to_dict(self):return asdict(self)

def safe(v,d=0.0):
    try:return d if pd.isna(v) else float(v)
    except Exception:return d

def _live_daily_bar_is_partial(df):
    if df is None or df.empty:return False
    try:
        now=pd.Timestamp.now(tz='America/New_York')
        last=pd.Timestamp(df.index[-1])
        minutes=now.hour*60+now.minute
        return now.weekday()<5 and last.date()==now.date() and minutes<970
    except Exception:return False

def _swing_volume_inputs(x):
    raw=safe(x.iloc[-1]['VOL_RATIO'],1.0)
    if not _live_daily_bar_is_partial(x):return raw,raw,False
    if len(x)<23:return 1.0,raw,True
    baseline=safe(x['Volume'].iloc[-22:-2].mean(),0.0)
    completed=safe(x['Volume'].iloc[-2],0.0)
    stable=completed/baseline if baseline>0 else 1.0
    return stable,raw,True

def _close_location(row):
    hi=safe(row.get('High'),0.0);lo=safe(row.get('Low'),0.0);close=safe(row.get('Close'),0.0)
    rng=max(hi-lo,.01)
    return max(0.0,min(1.0,(close-lo)/rng))

def _breakout_quality(price,level,atr,vol_ratio,momentum,rsi,close_location,strategy):
    if level<=0 or atr<=0 or price<=level:return False,[]
    extension=(price-level)/atr
    reasons=[]
    if strategy=='DAY':
        checks=[
            (0.03<=extension<=0.30,'breakout distance controlled'),
            (vol_ratio>=1.50,'strong relative volume'),
            (0.35<=momentum<=2.00,'healthy 1h momentum'),
            (52<=rsi<=68,'healthy RSI'),
            (close_location>=0.62,'close near bar high'),
        ]
        required=4
    else:
        checks=[
            (0.03<=extension<=0.40,'breakout distance controlled'),
            (vol_ratio>=1.20,'strong breakout volume'),
            (0.75<=momentum<=8.00,'healthy 5d momentum'),
            (50<=rsi<=67,'healthy daily RSI'),
            (close_location>=0.58,'close near daily high'),
        ]
        required=4
    passed=[label for ok,label in checks if ok]
    return len(passed)>=required,passed

def _trade_levels(df,entry_ref,entry_high,a,strategy):
    target_look=20 if strategy=='DAY' else 30
    target_range=df.iloc[-target_look:-1] if len(df)>target_look else df.iloc[:-1]
    resistance=safe(target_range['High'].max(),entry_high+2*a)
    if strategy=='DAY':
        support=safe(target_range['Low'].min(),entry_ref-a)
        atr_stop=entry_ref-1.25*a;structure_stop=support-.10*a;max_depth=max(1.9*a,entry_ref*.018)
    else:
        risk_look=10
        risk_range=df.iloc[-risk_look:-1] if len(df)>risk_look else df.iloc[:-1]
        support=safe(risk_range['Low'].min(),entry_ref-a)
        atr_stop=entry_ref-1.60*a;structure_stop=support-.15*a;max_depth=max(2.5*a,entry_ref*.05)
    stop=max(entry_ref-max_depth,min(atr_stop,structure_stop if structure_stop<entry_ref else atr_stop))
    worst_risk=max(entry_high-stop,.01)
    raw_t1=entry_high+2*worst_risk;raw_t2=entry_high+3*worst_risk
    t1=resistance if entry_high<resistance<raw_t1 else raw_t1
    t2=max(raw_t2,t1)
    return stop,t1,t2,(t1-entry_high)/worst_risk,(t2-entry_high)/worst_risk

def intraday_setup(symbol,df):
    if len(df)<55:return None
    x=add_intraday(df);r=x.iloc[-1];p=safe(r['Close']);e9,e21,e50=map(safe,[r['EMA9'],r['EMA21'],r['EMA50']]);rs=safe(r['RSI14'],50);a=safe(r['ATR14'],p*.02);vw=safe(r['VWAP'],p);vr=safe(r['VOL_RATIO'],1);mom=safe(r['RET_1H'],0);h20=safe(r['HIGH20'],p);cl=_close_location(r)
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
    breakout,breakout_reasons=_breakout_quality(p,h20,a,vr,mom,rs,cl,'DAY')
    if breakout:
        sc+=10;reasons.append('Quality 20-bar breakout');reasons.extend(breakout_reasons);setup_type='BREAKOUT';entry_ref=h20
    else:
        if p>h20 and h20>0:warnings.append('Breakout rejected: weak confirmation or excessive extension')
        setup_type='PULLBACK';entry_ref=min(p,max(vw,e21))
    entry_low=entry_ref-.08*a;entry_high=entry_ref+.12*a
    stop,t1,t2,rr1,rr2=_trade_levels(x,entry_ref,entry_high,a,'DAY')
    return Setup(symbol,'DAY',min(100,sc),p,entry_low,entry_high,stop,t1,t2,rr1,rr2,a,rs,vr,mom,reasons,warnings,setup_type)

def swing_setup(symbol,df):
    if len(df)<55:return None
    x=add_daily(df);r=x.iloc[-1];p=safe(r['Close']);e20,e50,e200=map(safe,[r['EMA20'],r['EMA50'],r['EMA200']]);rs=safe(r['RSI14'],50);a=safe(r['ATR14'],p*.025);vr,breakout_vr,partial_volume=_swing_volume_inputs(x);mom=safe(r['RET_5D'],0);h20=safe(r['HIGH20'],p);cl=_close_location(r)
    sc=0;reasons=[];warnings=[]
    if p>e20>e50 and (e200==0 or e50>e200):sc+=30;reasons.append('Daily trend bullish')
    elif p>e20>e50:sc+=24;reasons.append('Price above EMA20/50')
    else:warnings.append('Daily trend not fully aligned')
    if 50<=rs<=65:sc+=18;reasons.append(f'Daily RSI {rs:.1f}')
    elif 45<=rs<50:sc+=8
    elif rs>72:warnings.append('Daily RSI overbought')
    volume_label='Previous completed-day volume' if partial_volume else 'Daily volume'
    if vr>=1.5:sc+=16;reasons.append(f'{volume_label} {vr:.2f}x')
    elif vr>=1.1:sc+=9;reasons.append(f'{volume_label} {vr:.2f}x')
    if partial_volume:warnings.append('Current daily volume is partial; SWING score uses last completed session')
    if mom>=2:sc+=14;reasons.append(f'5-day momentum +{mom:.2f}%')
    elif mom>0:sc+=7
    breakout,breakout_reasons=_breakout_quality(p,h20,a,breakout_vr,mom,rs,cl,'SWING')
    if breakout:
        sc+=12;reasons.append('Quality 20-day breakout');reasons.extend(breakout_reasons);setup_type='BREAKOUT';entry_ref=h20
    else:
        if p>h20 and h20>0:warnings.append('Breakout rejected: weak confirmation or excessive extension')
        setup_type='PULLBACK';entry_ref=min(p,e20 if e20>0 else p)
    if p>e20:sc+=10
    entry_low=entry_ref-.15*a;entry_high=entry_ref+.20*a
    stop,t1,t2,rr1,rr2=_trade_levels(x,entry_ref,entry_high,a,'SWING')
    return Setup(symbol,'SWING',min(100,sc),p,entry_low,entry_high,stop,t1,t2,rr1,rr2,a,rs,vr,mom,reasons,warnings,setup_type)
