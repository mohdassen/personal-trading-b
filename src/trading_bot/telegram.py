from __future__ import annotations
import os, requests

def enabled():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))

def send(text):
    token=os.getenv("TELEGRAM_BOT_TOKEN")
    chat=os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    r=requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id":chat,
            "text":text,
            "parse_mode":"HTML",
            "disable_web_page_preview":True
        },
        timeout=20
    )
    r.raise_for_status()
    return True

def _decision_icon(action):
    return {
        "BUY_NOW":"✅",
        "WAIT_FOR_ENTRY":"⏳",
        "DO_NOT_CHASE":"🚫",
        "NO_TRADE":"⛔",
        "WATCH":"👀"
    }.get(action,"ℹ️")

def signal_message(s):
    action=s.get("action","WATCH")
    icon=_decision_icon(action)
    decision=s.get("simple_decision_ar",s.get("simple_decision_en",action))
    next_step=s.get("simple_next_step",s.get("instruction",""))
    risk=s.get("risk_label","متوسطة")
    confidence=s.get("confidence_label","متوسطة")

    lines=[
        f"{icon} <b>{s['symbol']} — {decision}</b>",
        "",
        f"🎯 <b>ماذا أفعل؟</b> {next_step}",
        "",
        f"💵 السعر الآن: <b>${s['price']:.2f}</b>",
        f"📍 منطقة الدخول: <b>${s['entry_low']:.2f} – ${s['entry_high']:.2f}</b>",
        f"🛑 وقف الخسارة: <b>${s['stop_loss']:.2f}</b>",
        f"🏁 الهدف الأول: <b>${s['target1']:.2f}</b>",
        f"🚀 الهدف الثاني: ${s['target2']:.2f}",
        "",
        f"📦 الكمية المقترحة: <b>{s['suggested_shares']} سهم</b>",
        f"💰 قيمة الصفقة تقريبًا: <b>${s['suggested_value']:.0f}</b>",
        f"⚠️ مستوى المخاطرة: <b>{risk}</b>",
        f"⭐ قوة الفرصة: <b>{confidence}</b> ({s['score']}/100)",
    ]

    if s.get("event_risk") in ("HIGH","MEDIUM"):
        lines += ["", "📰 يوجد حدث/خبر قد يؤثر على السهم، لذلك التزم بالقرار والوقف المحدد."]

    lines += [
        "",
        "📱 التنفيذ يدوي في Sahm.",
        "ملاحظة: لا تشترِ إذا تغير السعر وأصبح خارج منطقة الدخول."
    ]
    return "\n".join(lines)
