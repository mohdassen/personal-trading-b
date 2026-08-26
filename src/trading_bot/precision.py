from __future__ import annotations
from functools import lru_cache
import math
import pandas as pd
from .market import quote_daily
from .indicators import add_daily

SECTOR_ETF={
 'TECH':'XLK','SEMIS':'SMH','CYBER':'CIBR','FINANCE':'XLF','HEALTH':'XLV','ENERGY':'XLE',
 'INDUSTRIAL':'XLI','CONSUMER':'XLY','COMM':'XLC','TRAVEL':'PEJ','CRYPTO':'BITQ','OTHER':'SPY'
}

def _ret(df,n):
    if df is None or len(df)<=n:return 0.0
    a=float(df['Close'].iloc[-1]);b=float(df['Close'].iloc[-1-n]);return (a/b-1)*100 if b else 0.0

@lru_cache(maxsize=32)
def _bench(symbol):
    try:return quote_daily(symbol)
    except Exception:return pd.DataFrame()

def weekly_alignment(daily):
    if daily is None or len(daily)<60:return False
    w=daily.resample('W-FRI').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
    if len(w)<12:return False
    e8=w['Close'].ewm(span=8,adjust=False).mean();e20=w['Close'].ewm(span=20,adjust=False).mean()
    return bool(float(w['Close'].iloc[-1])>float(e8.iloc[-1])>float(e20.iloc[-1]))

def relative_strength(daily,group='OTHER'):
    spy=_bench('SPY'); etf=_bench(SECTOR_ETF.get(group,'SPY'))
    r20=_ret(daily,20); spy20=_ret(spy,20); sec20=_ret(etf,20)
    edge_market=r20-spy20; edge_sector=r20-sec20
    score=0
    if edge_market>5:score+=5
    elif edge_market>2:score+=3
    elif edge_market<-3:score-=4
    if edge_sector>4:score+=4
    elif edge_sector>1:score+=2
    elif edge_sector<-3:score-=3
    return {'stock_20d':round(r20,2),'vs_market':round(edge_market,2),'vs_sector':round(edge_sector,2),'score':score}

def market_v2(regime):
    # Existing regime remains source of truth; enrich with small-cap participation.
    try:
        iwm=add_daily(_bench('IWM')).iloc[-1]; c=float(iwm['Close']);e20=float(iwm['EMA20']);e50=float(iwm['EMA50'])
        iwm_trend='BULLISH' if c>e20>e50 else 'BEARISH' if c<e20<e50 else 'MIXED'
    except Exception:iwm_trend='UNKNOWN'
    if regime.label=='RISK_ON' and iwm_trend=='BULLISH':label='STRONG_RISK_ON';adj=3
    elif regime.label in ('RISK_OFF','HIGH_VOLATILITY'):label='RISK_OFF';adj=-5
    elif regime.label=='RISK_ON':label='CAUTIOUS_RISK_ON';adj=0
    else:label='CHOPPY';adj=-2
    return {'label':label,'adjustment':adj,'iwm_trend':iwm_trend}

def entry_quality(setup):
    p=float(setup.price); lo=float(setup.entry_low); hi=float(setup.entry_high); atr=max(float(setup.atr),.01)
    width=max(hi-lo,.01); pos=(p-lo)/width
    extension=max(0.0,(p-hi)/atr)
    score=0; blockers=[]; notes=[]
    if lo<=p<=hi:
        if .15<=pos<=.70:score+=5;notes.append('السعر داخل منطقة دخول مريحة')
        else:score+=1;notes.append('السعر داخل المنطقة لكن قريب من طرفها')
    elif p>hi:
        score-=6;blockers.append('السعر أعلى من منطقة الدخول')
    else:
        score-=2;notes.append('السعر لم يصل لمنطقة الدخول بعد')
    if extension>.5:score-=5;blockers.append('السعر ممتد بعد حركة سريعة')
    if setup.strategy=='DAY' and setup.momentum>2.0:score-=4;blockers.append('اندفاع الساعة الأخيرة مرتفع')
    if setup.strategy=='SWING' and setup.momentum>10:score-=4;blockers.append('السهم ارتفع كثيرًا خلال خمسة أيام')
    return {'score':score,'position':round(pos,2),'extension_atr':round(extension,2),'blockers':blockers,'notes':notes}

def structure_quality(setup,daily):
    if daily is None or len(daily)<25:return {'score':0,'support':None,'resistance':None,'room_to_resistance_pct':None,'blockers':[]}
    recent=daily.iloc[-21:-1]; support=float(recent['Low'].min()); resistance=float(recent['High'].max());p=float(setup.price)
    room=(resistance-p)/p*100 if p else 0
    risk=(p-float(setup.stop_loss))/p*100 if p else 0
    blockers=[];score=0
    if resistance>p and room<max(risk*1.4,1.0):score-=6;blockers.append('مقاومة قريبة تقلل مساحة الربح')
    elif resistance>p:score+=2
    if float(setup.stop_loss)<support*.97:score-=2
    return {'score':score,'support':round(support,2),'resistance':round(resistance,2),'room_to_resistance_pct':round(room,2),'blockers':blockers}

def precision_context(setup,daily,regime,group='OTHER'):
    rs=relative_strength(daily,group); entry=entry_quality(setup); structure=structure_quality(setup,daily); mv2=market_v2(regime)
    weekly=weekly_alignment(daily) if setup.strategy=='SWING' else True
    tf_score=3 if weekly else -5
    total=rs['score']+entry['score']+structure['score']+mv2['adjustment']+tf_score
    blockers=list(entry['blockers'])+list(structure['blockers'])
    if setup.strategy=='SWING' and not weekly:blockers.append('الاتجاه الأسبوعي غير مؤكد')
    return {'adjustment':max(-15,min(12,int(total))),'relative_strength':rs,'entry_quality':entry,'structure':structure,'market_v2':mv2,'weekly_aligned':weekly,'blockers':blockers,'group':group}

def opportunity_grade(score,context):
    blockers=len(context.get('blockers',[])); rs=context.get('relative_strength',{}).get('vs_market',0)
    if score>=92 and blockers==0 and rs>=1:return 'A+'
    if score>=87 and blockers<=1:return 'A'
    if score>=82 and blockers<=1:return 'B'
    return 'C'
