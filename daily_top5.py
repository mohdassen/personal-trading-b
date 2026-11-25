"""Independent research-only daily picks; no orders."""
import json, os, math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from wide_market_ibs_forward import SYMBOLS, dl

COST=.003
def analyze(x):
    c=x.Close
    atr=(x.High-x.Low).rolling(14).mean()
    signal=(c>c.rolling(50).mean()) & (c>c.rolling(20).mean()) & (c.pct_change(5)>0) & (x.Volume>x.Volume.rolling(20).mean()) & ((c*x.Volume).rolling(20).mean()>=20e6) & (c>=5)
    # Historical next-session open to close, after estimated costs.
    returns=(x.Close.shift(-1)/x.Open.shift(-1)-1-COST)
    hist=returns[signal & returns.notna()].tail(150)
    n=len(hist)
    if not bool(signal.iloc[-1]) or n<40:return None
    wins=int((hist>0).sum()); p=wins/n; z=1.96
    lower=(p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)
    if lower<.50 or hist.mean()<=0:return None
    price=float(c.iloc[-1]); risk=float(atr.iloc[-1])
    return dict(reference_close=round(price,2),entry_reference=round(price,2),target_reference=round(price+risk,2),stop_reference=round(price-risk,2),historical_win_rate=round(p,4),lower_confidence=round(lower,4),sample=n,mean_net_return=round(float(hist.mean()),6),date=str(x.index[-1].date()))

def main():
    rows=[]; dates=[]; errors=0
    for s in SYMBOLS:
        try:
            x=dl(s)
            if len(x)<100:raise ValueError("insufficient bars")
            dates.append(str(x.index[-1].date()))
            r=analyze(x)
            if r:rows.append(dict(symbol=s,**r))
        except Exception as e:
            errors+=1; print(s,type(e).__name__,flush=True)
    if not dates or errors/len(SYMBOLS)>.2:raise RuntimeError("data coverage failure")
    date=max(dates)
    if (datetime.now(ZoneInfo("America/New_York")).date()-datetime.fromisoformat(date).date()).days>4:raise RuntimeError("stale data")
    picks=sorted((r for r in rows if r["date"]==date),key=lambda r:(r["lower_confidence"],r["mean_net_return"]),reverse=True)[:5]
    report=dict(as_of=date,mode="RESEARCH_ONLY",picks=picks,errors=errors,universe=len(SYMBOLS))
    out=Path("data/daily_top5_latest.json");out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(report,indent=2))
    lines=[f"فرص الأسهم الأمريكية | إغلاق {date} | تجريبي"]
    for r in picks:
        lines.append(f'{r["symbol"]} | إغلاق ${r["reference_close"]} | دخول مرجعي ${r["entry_reference"]} | هدف ${r["target_reference"]} | وقف ${r["stop_reference"]} | نجاح تاريخي {r["historical_win_rate"]:.0%} (n={r["sample"]})')
    if not picks:lines.append("لا توجد فرص تستوفي الشروط اليوم.")
    lines.append("الأسعار مرجعية وليست لحظية. نسبة النجاح تخص عائد افتتاح إلى إغلاق اليوم التالي بعد التكلفة، وليست احتمال بلوغ الهدف. الهدف والوقف تقديريان غير مختبرين.")
    token=os.environ["TELEGRAM_BOT_TOKEN"]; chat=os.environ["TELEGRAM_CHAT_ID"]
    try:
        response=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",data={"chat_id":chat,"text":"\\n".join(lines).replace("\\n","\n")},timeout=20)
        if response.status_code!=200 or not response.json().get("ok"):raise RuntimeError("Telegram rejected message")
    except Exception:
        raise RuntimeError("Telegram delivery failed") from None
    print("Telegram API accepted message; picks:",len(picks))
if __name__=="__main__":main()
