from __future__ import annotations
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from jinja2 import Template

TPL=r"""<!doctype html><html lang="ar" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300"><title>مساعد التداول الشخصي</title>
<style>
:root{--b:#07101e;--p:#101a2c;--q:#0b1424;--t:#edf3ff;--m:#98a7c4;--l:#263754;--g:#22c55e;--a:#f59e0b;--r:#ef4444;--c:#38bdf8}
*{box-sizing:border-box}body{margin:0;background:var(--b);color:var(--t);font-family:system-ui,-apple-system,Segoe UI,Tahoma,Arial}
.w{max-width:1200px;margin:auto;padding:16px}.h{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:end}.m{color:var(--m)}
.kpis,.grid{display:grid;gap:12px}.kpis{grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin:14px 0}.grid{grid-template-columns:repeat(auto-fit,minmax(330px,1fr))}
.k,.c{background:var(--p);border:1px solid var(--l);border-radius:16px;padding:14px}.k b{font-size:1.25rem;display:block;margin-top:6px}
.top{display:flex;justify-content:space-between;gap:10px}.score{font-size:1.3rem;font-weight:900}.STRONG_BUY,.BUY{color:var(--g)}.WATCH{color:var(--a)}.BLOCKED{color:var(--r)}.WAIT{color:var(--m)}
.lv{display:grid;grid-template-columns:1fr 1fr;gap:7px}.lv div{background:var(--q);padding:9px;border-radius:9px}.small{font-size:.86rem;line-height:1.65}
.action{margin:10px 0;padding:12px;border:1px solid var(--c);border-radius:10px;background:#08243a;color:#dff6ff;font-size:1.02rem}
.buy{border-color:var(--g)}.wait{border-color:var(--a)}.no{border-color:var(--r)}
</style></head><body><div class="w">
<div class="h"><div><h1>مساعد التداول الشخصي</h1><div class="m">الهدف: قرار واضح وبسيط — اشترِ، انتظر، أو لا تدخل</div></div><div class="m">آخر تحديث: {{ updated }}</div></div>
<div class="kpis">
<div class="k">حالة السوق<b>{{ regime.label }}</b></div>
<div class="k">أفضل فرصة<b>{{ best.symbol if best else '-' }} {{ best.score if best else 0 }}/100</b></div>
<div class="k">الكاش المتاح<b>${{ '%.0f'|format(portfolio.cash) }}</b></div>
<div class="k">صفقات مفتوحة<b>{{ portfolio.positions|length }}</b></div>
<div class="k">نسبة نجاح الصفقات<b>{{ performance.win_rate }}%</b></div>
</div>
<div class="grid">{% for s in signals %}
<div class="c">
<div class="top"><b>{{ s.symbol }}</b><span class="{{ s.signal }}"><b>{{ s.simple_decision_ar }} · {{ s.score }}/100</b></span></div>
<h2>${{ '%.2f'|format(s.price) }}</h2>
<div class="action {% if s.action == 'BUY_NOW' %}buy{% elif s.action in ['WAIT_FOR_ENTRY','WATCH','DO_NOT_CHASE'] %}wait{% else %}no{% endif %}"><b>القرار: {{ s.simple_decision_ar }}</b><br>{{ s.simple_next_step }}</div>
<div class="lv">
<div>منطقة الدخول<b><br>${{ '%.2f'|format(s.entry_low) }}–${{ '%.2f'|format(s.entry_high) }}</b></div>
<div>وقف الخسارة<b><br>${{ '%.2f'|format(s.stop_loss) }}</b></div>
<div>الهدف الأول<b><br>${{ '%.2f'|format(s.target1) }}</b></div>
<div>الهدف الثاني<b><br>${{ '%.2f'|format(s.target2) }}</b></div>
<div>الكمية المقترحة<b><br>{{ s.suggested_shares }} سهم</b></div>
<div>قيمة الصفقة<b><br>${{ '%.0f'|format(s.suggested_value) }}</b></div>
<div>المخاطرة<b><br>{{ s.risk_label }}</b></div>
<div>قوة الفرصة<b><br>{{ s.confidence_label }}</b></div>
</div>
<p class="small m">التحليل الفني والأخبار والمخاطر تُحسب في الخلفية. لا تحتاج لفهم المؤشرات لاتخاذ القرار المعروض.</p>
</div>{% endfor %}</div>
<p class="m small">هذه أداة دعم قرار وليست ضمانًا للربح. التزم بمنطقة الدخول ووقف الخسارة، ولا تدخل إذا تغير السعر بشكل كبير.</p>
</div></body></html>"""

def render(signals,output,timezone,regime,portfolio,performance):
    signals=sorted(signals,key=lambda x:x["score"],reverse=True)
    best=signals[0] if signals else None
    updated=datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
    html=Template(TPL).render(
        signals=signals,updated=updated,regime=regime,
        portfolio=portfolio,performance=performance,best=best
    )
    p=Path(output); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(html,encoding="utf-8")
