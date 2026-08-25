from __future__ import annotations
import os
import requests


def enabled() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_message(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=20)
    resp.raise_for_status()
    return True


def format_signal(s: dict) -> str:
    icon = "🟢" if s["signal"] == "BUY" else "🟡" if s["signal"] == "WATCH" else "⚪️"
    reasons = "\n".join(f"✓ {r}" for r in s.get("reasons", [])[:5])
    return (
        f"{icon} <b>{s['symbol']} — {s['signal']} {s['score']}/100</b>\n"
        f"السعر: ${s['price']:.2f}\n"
        f"الدخول: ${s['entry_low']:.2f} - ${s['entry_high']:.2f}\n"
        f"وقف الخسارة: ${s['stop_loss']:.2f}\n"
        f"الهدف 1: ${s['target1']:.2f}\n"
        f"الهدف 2: ${s['target2']:.2f}\n"
        f"الكمية المقترحة: {s['suggested_shares']} سهم\n\n"
        f"{reasons}\n\n"
        "التنفيذ يدوي في منصة سهم. ليست توصية مالية."
    )
