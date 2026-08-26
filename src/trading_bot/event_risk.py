from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .market import earnings_date,recent_news
CATS={
 'LEGAL':({'investigation','lawsuit','fraud','probe','sec charges','criminal'},-12),
 'GUIDANCE':({'cuts guidance','lowers guidance','weak outlook','withdraws guidance'},-10),
 'RESULTS':({'misses estimates','missed estimates','earnings miss','revenue miss'},-8),
 'PRODUCT':({'recall','safety issue','ban','suspension'},-10),
 'ANALYST_NEG':({'downgrade','price target cut'},-4),
 'POSITIVE':({'raises guidance','beats estimates','record revenue','approval','contract win','upgrade'},3),
}
@dataclass
class EventRisk:
    level:str;score_adjustment:int;earnings_days:int|None;notes:list[str];categories:list[str]
def assess(symbol,block_days=2):
    notes=[];categories=[];adj=0;edays=None
    try:
        e=earnings_date(symbol)
        if e is not None:
            now=pd.Timestamp.now(tz='UTC');e=e.tz_localize('UTC') if e.tzinfo is None else e.tz_convert('UTC');edays=int((e.normalize()-now.normalize()).days)
            if 0<=edays<=block_days:notes.append(f'Earnings in {edays} day(s)');categories.append('EARNINGS');adj-=30
            elif 0<=edays<=7:notes.append(f'Earnings in {edays} day(s)');categories.append('EARNINGS_SOON');adj-=6
    except Exception:pass
    try:
        titles=[]
        for item in recent_news(symbol,10):
            c=item.get('content',item);t=str(c.get('title','')).lower()
            if t:titles.append(t)
        positive=0
        for name,(words,weight) in CATS.items():
            hits=sum(any(w in t for w in words) for t in titles)
            if not hits:continue
            categories.append(name)
            if weight<0:adj+=max(weight*hits,-18);notes.append(f'{name}: {hits} headline(s)')
            else:positive+=min(weight*hits,6)
        if not any(c in categories for c in ('LEGAL','GUIDANCE','RESULTS','PRODUCT')):adj+=positive
    except Exception:pass
    adj=max(-40,min(8,adj));level='HIGH' if adj<=-20 else 'MEDIUM' if adj<0 else 'LOW'
    return EventRisk(level,adj,edays,notes,categories)
