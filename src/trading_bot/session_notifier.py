from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
def _load(path,default):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:return default
def _save(path,data):Path(path).write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
def market_clock():
    n=datetime.now(ZoneInfo('America/New_York'));m=n.hour*60+n.minute
    return n,n.weekday()<5 and 570<=m<=960
def closed_summary(data_dir,engine):
    d=Path(data_dir);n,is_open=market_clock();state=_load(d/'session_notify_state.json',{})
    if is_open or n.weekday()>=5 or n.hour<16:return None
    day=n.date().isoformat()
    if state.get('close_date')==day:return None
    paper=_load(d/'paper_validation.json',{});book=_load(d/'paper_trades.json',{'open':[]});protection=_load(d/'protection_state.json',{});watch=_load(d/'strong_watchlist.json',[])
    msg='\n'.join([f'🌙 <b>ملخص إغلاق السوق — {engine}</b>',f"صفقات Paper المكتملة: <b>{paper.get('samples',0)}</b>",f"صفقات Paper المفتوحة: <b>{len(book.get('open',[]))}</b>",f"فرص قوية بقيت تحت المراقبة: <b>{len(watch)}</b>",f"Expectancy: <b>{paper.get('expectancy_r',0)}R</b> | Profit Factor: <b>{paper.get('profit_factor',0)}</b>",f"الحماية: <b>{'مفعلة' if protection.get('blocked') else 'طبيعية'}</b>",'لا يلزم أي إجراء الآن.'])
    state['close_date']=day;_save(d/'session_notify_state.json',state);return msg
def opening_summary(data_dir,engine,opportunities,protection,watch=None):
    d=Path(data_dir);n,is_open=market_clock();state=_load(d/'session_notify_state.json',{});watch=watch or []
    if not is_open:return None
    day=n.date().isoformat()
    if state.get('open_date')==day:return None
    paper=_load(d/'paper_validation.json',{});lines=[f'🔔 <b>جلسة السوق بدأت — {engine}</b>',f"جاهزة/قريبة للدخول: <b>{len(opportunities)}</b>",f"فرص قوية تحت المراقبة: <b>{len(watch)}</b>",f"حالة التعلم: <b>{paper.get('status','LEARNING')}</b> — {paper.get('samples',0)} صفقة مكتملة",f"الحماية: <b>{'مفعلة' if protection.get('blocked') else 'طبيعية'}</b>"]
    if watch:lines.append('👀 '+', '.join(f"{x['symbol']} ({x['score']})" for x in watch[:5]))
    lines.append('سأرسل توصية دخول فقط عندما تتحقق شروط المخاطرة والتوقيت.')
    state['open_date']=day;_save(d/'session_notify_state.json',state);return '\n'.join(lines)
