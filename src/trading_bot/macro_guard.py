from __future__ import annotations
from datetime import datetime,timezone,timedelta

def assess(events,before_minutes=45,after_minutes=30):
    now=datetime.now(timezone.utc);active=[]
    for e in events or []:
        if not e.get('enabled',True):continue
        try:
            t=datetime.fromisoformat(str(e['timestamp']).replace('Z','+00:00'));t=t if t.tzinfo else t.replace(tzinfo=timezone.utc)
            if t-timedelta(minutes=before_minutes)<=now<=t+timedelta(minutes=after_minutes):active.append(e)
        except Exception:continue
    if not active:return {'blocked':False,'events':[],'reason':''}
    names=[str(x.get('name','High impact event')) for x in active]
    return {'blocked':True,'events':names,'reason':'حدث اقتصادي عالي التأثير ضمن نافذة الحماية'}
