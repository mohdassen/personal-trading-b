from __future__ import annotations
import os,requests

def enabled():return bool(os.getenv('TELEGRAM_BOT_TOKEN') and os.getenv('TELEGRAM_CHAT_ID'))
def send(text):
    token=os.getenv('TELEGRAM_BOT_TOKEN');chat=os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:return False
    r=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat,'text':text,'parse_mode':'HTML','disable_web_page_preview':True},timeout=20);r.raise_for_status();return True

def _icon(action):return {'BUY_NOW':'✅','WAIT_FOR_ENTRY':'⏳','DO_NOT_CHASE':'🚫','NO_TRADE':'⛔','WATCH':'👀'}.get(action,'ℹ️')
def _why(s):
    p=s.get('precision',{});parts=[]
    rs=p.get('relative_strength',{}).get('vs_market',0)
    if rs>=2:parts.append('السهم أقوى من السوق')
    elif rs<=-2:parts.append('السهم أضعف من السوق')
    if p.get('weekly_aligned') and s.get('strategy')=='SWING':parts.append('الاتجاه الأسبوعي داعم')
    blockers=p.get('blockers',[])
    if blockers:parts.append(blockers[0])
    if not parts:parts.append('الشروط الأساسية متوافقة')
    return ' • '.join(parts[:2])
def signal_message(s):
    action=s.get('action','WATCH');decision=s.get('simple_decision_ar',action);next_step=s.get('simple_next_step','');grade=s.get('quality_grade','-')
    lines=[f"{_icon(action)} <b>{s['symbol']} — {decision}</b>",f"⭐ جودة الفرصة: <b>{grade}</b> | المخاطرة: <b>{s.get('risk_label','-')}</b>",f"🧭 السوق: <b>{s.get('market_regime_v2',s.get('market_regime','-'))}</b>",'',f"🎯 <b>ماذا أفعل؟</b> {next_step}",f"💡 السبب: {_why(s)}",'',f"💵 السعر الآن: <b>${s['price']:.2f}</b>",f"📍 الدخول: <b>${s['entry_low']:.2f} – ${s['entry_high']:.2f}</b>",f"🛑 الوقف: <b>${s['stop_loss']:.2f}</b>",f"🏁 الهدف 1: <b>${s['target1']:.2f}</b>",f"🚀 الهدف 2: ${s['target2']:.2f}",'',f"📦 الكمية: <b>{s['suggested_shares']} سهم</b>",f"💰 قيمة الصفقة: <b>${s['suggested_value']:.0f}</b>"]
    if s.get('event_risk') in ('HIGH','MEDIUM'):lines += ['','📰 يوجد حدث/خبر قد يؤثر على السهم.']
    lines += ['','📱 التنفيذ يدوي في Sahm.','⚠️ إذا تغير القرار لاحقًا سيصلك تنبيه إلغاء/تعديل.']
    return '\n'.join(lines)
