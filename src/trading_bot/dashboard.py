from __future__ import annotations
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from jinja2 import Template

TEMPLATE = r'''<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Personal US Trading Bot</title>
<style>
:root{--bg:#0b1020;--panel:#11182a;--text:#eef2ff;--muted:#9aa4bf;--line:#24304b;--good:#22c55e;--warn:#f59e0b;--idle:#94a3b8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Tahoma,Arial,sans-serif}.wrap{max-width:1100px;margin:auto;padding:20px}.head{display:flex;justify-content:space-between;gap:12px;align-items:end;flex-wrap:wrap}.sub{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-top:18px}.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px}.top{display:flex;justify-content:space-between;align-items:center}.score{font-size:1.55rem;font-weight:800}.sig{font-weight:800}.BUY{color:var(--good)}.WATCH{color:var(--warn)}.WAIT{color:var(--idle)}.price{font-size:1.35rem;margin:10px 0}.levels{display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:.94rem}.levels div{background:#0d1425;padding:8px;border-radius:10px}.reason{color:#d6ddf2;font-size:.9rem;line-height:1.7}.warn{color:#fbbf24}.footer{color:var(--muted);font-size:.82rem;margin:24px 0}.badge{padding:4px 8px;border-radius:999px;background:#0d1425;border:1px solid var(--line)}
</style></head>
<body><div class="wrap"><div class="head"><div><h1>بوت التداول الشخصي — السوق الأمريكي</h1><div class="sub">تحليل آلي، والتنفيذ يدوي عبر منصة سهم</div></div><div class="sub">آخر تحديث: {{ updated }}</div></div>
<div class="grid">
{% for s in signals %}
<div class="card"><div class="top"><div><b>{{ s.symbol }}</b> <span class="sig {{ s.signal }}">{{ s.signal }}</span></div><div class="score">{{ s.score }}/100</div></div>
<div class="price">${{ '%.2f'|format(s.price) }}</div>
<div class="levels"><div>الدخول<br><b>${{ '%.2f'|format(s.entry_low) }} - ${{ '%.2f'|format(s.entry_high) }}</b></div><div>وقف الخسارة<br><b>${{ '%.2f'|format(s.stop_loss) }}</b></div><div>الهدف 1<br><b>${{ '%.2f'|format(s.target1) }}</b></div><div>الهدف 2<br><b>${{ '%.2f'|format(s.target2) }}</b></div><div>RSI<br><b>{{ s.rsi }}</b></div><div>الحجم<br><b>{{ s.vol_ratio }}x</b></div><div>الكمية المقترحة<br><b>{{ s.suggested_shares }}</b></div><div>قيمة تقريبية<br><b>${{ '%.2f'|format(s.suggested_value) }}</b></div></div>
{% if s.reasons %}<p class="reason">{% for r in s.reasons %}✓ {{ r }}<br>{% endfor %}</p>{% endif %}
{% if s.warnings %}<p class="reason warn">{% for w in s.warnings %}⚠ {{ w }}<br>{% endfor %}</p>{% endif %}
</div>{% endfor %}
</div><div class="footer">هذه الأداة للمساعدة في التحليل فقط وليست توصية استثمارية. بيانات Yahoo Finance قد تكون متأخرة أو غير مناسبة للتنفيذ اللحظي. تأكد من السعر داخل Sahm قبل أي أمر.</div></div></body></html>'''


def render(signals: list[dict], output: str, timezone: str = "Asia/Riyadh") -> None:
    signals = sorted(signals, key=lambda x: x["score"], reverse=True)
    updated = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
    html = Template(TEMPLATE).render(signals=signals, updated=updated)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(html, encoding="utf-8")
