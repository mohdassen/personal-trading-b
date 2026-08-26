from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .market import earnings_date,recent_news
NEG={'downgrade','investigation','lawsuit','fraud','recall','misses','missed','warning','cuts guidance','weak outlook','probe','sec'}
POS={'upgrade','beats','beat estimates','raises guidance','record revenue','approval','partnership','contract win'}
@dataclass
class EventRisk:
    level:str; score_adjustment:int; earnings_days:int|None; notes:list[str]
def assess(symbol,block_days=2):
    notes=[]; adj=0; edays=None
    try:
        e=earnings_date(symbol)
        if e is not None:
            now=pd.Timestamp.now(tz='UTC'); e=e.tz_localize('UTC') if e.tzinfo is None else e.tz_convert('UTC'); edays=int((e.normalize()-now.normalize()).days)
            if 0<=edays<=block_days:notes.append(f'Earnings in {edays} day(s)');adj-=25
    except Exception:pass
    try:
        titles=[]
        for item in recent_news(symbol):
            c=item.get('content',item); t=str(c.get('title','')).lower()
            if t:titles.append(t)
        neg=sum(any(k in t for k in NEG) for t in titles); pos=sum(any(k in t for k in POS) for t in titles)
        if neg:notes.append(f'{neg} negative-risk headline(s)');adj-=min(12,neg*4)
        if pos and not neg:notes.append(f'{pos} positive headline(s)');adj+=min(6,pos*2)
    except Exception:pass
    level='HIGH' if adj<=-20 else 'MEDIUM' if adj<0 else 'LOW'
    return EventRisk(level,adj,edays,notes)
