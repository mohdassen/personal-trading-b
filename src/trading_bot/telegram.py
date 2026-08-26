from __future__ import annotations
import os,requests
def enabled():return bool(os.getenv('TELEGRAM_BOT_TOKEN') and os.getenv('TELEGRAM_CHAT_ID'))
def send(text):
    token=os.getenv('TELEGRAM_BOT_TOKEN');chat=os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:return False
    r=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat,'text':text,'parse_mode':'HTML','disable_web_page_preview':True},timeout=20);r.raise_for_status();return True
def signal_message(s):
    icon={'STRONG_BUY':'🔥','BUY':'🟢','WATCH':'🟡','BLOCKED':'⛔'}.get(s['signal'],'⚪');reasons='\n'.join(f'✓ {x}' for x in s.get('reasons',[])[:5]);ev='\n'.join(f'⚠ {x}' for x in s.get('event_notes',[])[:3])
    return '\n'.join([f"{icon} <b>{s['symbol']} — {s['signal']} {s['score']}/100</b>",f"Strategy: {s['strategy']} | Market: {s['market_regime']} | Event risk: {s['event_risk']}",f"السعر: <b>${s['price']:.2f}</b>",f"منطقة الدخول: ${s['entry_low']:.2f} - ${s['entry_high']:.2f}",f"وقف الخسارة: ${s['stop_loss']:.2f}",f"الهدف 1: ${s['target1']:.2f} | الهدف 2: ${s['target2']:.2f}",f"الكمية المقترحة: <b>{s['suggested_shares']} سهم</b> (~${s['suggested_value']:.0f})",f"مخاطرة الصفقة: ${s['risk_dollars']:.2f} ({s['risk_pct_equity']:.2f}%)",'',reasons,ev,'','📱 التنفيذ يدويًا في Sahm. لا يوجد ضمان للربح.'])
