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
    # Fixed 1 ATR bracket from actual next open; conservative stop-first
    # when both barriers occur in the same daily bar.
    o=x.Open.shift(-1); h=x.High.shift(-1); l=x.Low.shift(-1); close=x.Close.shift(-1)
    target=o+atr; stop=o-atr
    returns=(close/o-1).where(h<target,atr/o)
    returns=returns.where(l>stop,-atr/o)-COST
    returns=returns.where(o.notna() & atr.notna() & (o>atr))
    # Fixed chronological holdout, never used to tune the filter.
    split=int(len(x)*.65)
    train=returns.iloc[:max(0,split-1)][signal.iloc[:max(0,split-1)]].dropna()
    hist=returns.iloc[split:][signal.iloc[split:]].dropna()
    n=len(hist)
    if not bool(signal.iloc[-1]) or len(train)<40 or n<40:return None
    wins=int((hist>0).sum()); p=wins/n; z=1.96
    lower=(p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)
    losses=-float(hist[hist<0].sum()); pf=float(hist[hist>0].sum())/losses if losses else 999.
    if train.mean()<=0 or p<.75 or lower<.60 or pf<1.5 or hist.mean()<=0:return None
    price=float(c.iloc[-1]); risk=float(atr.iloc[-1])
    return dict(reference_close=round(price,2),entry_reference=round(price,2),target_reference=round(price+risk,2),stop_reference=round(price-risk,2),historical_win_rate=round(p,4),lower_confidence=round(lower,4),sample=n,profit_factor=round(pf,3),mean_net_return=round(float(hist.mean()),6),date=str(x.index[-1].date()))

def prior_evidence():
    # IBS outcomes are a different strategy: use only as a weak ranking tie-break,
    # never as the probability of the new next-session strategy.
    groups={}
    source=Path("data/wide_market_ibs_closed_trades.jsonl")
    if not source.exists():
        print("Prior IBS evidence unavailable")
        return groups
    for line in source.read_text().splitlines():
        if not line.strip():continue
        t=json.loads(line)
        groups.setdefault(t["symbol"],[]).append(t)
    return groups

def main():
    prior=prior_evidence()
    rows=[]; dates=[]; errors=0
    for s in SYMBOLS:
        try:
            x=dl(s)
            if len(x)<100:raise ValueError("insufficient bars")
            dates.append(str(x.index[-1].date()))
            r=analyze(x)
            if r:
                old=[float(t["net_return"]) for t in prior.get(s,[]) if str(t.get("exit_date","9999"))<r["date"] and "net_return" in t]
                r["prior_ibs_count"]=len(old)
                r["prior_ibs_shrunk_return"]=sum(old)/(len(old)+20)
                rows.append(dict(symbol=s,**r))
        except Exception as e:
            errors+=1; print(s,type(e).__name__,flush=True)
    if not dates or errors/len(SYMBOLS)>.2:raise RuntimeError("data coverage failure")
    date=max(dates)
    if (datetime.now(ZoneInfo("America/New_York")).date()-datetime.fromisoformat(date).date()).days>4:raise RuntimeError("stale data")
    picks=sorted((r for r in rows if r["date"]==date),key=lambda r:(r["lower_confidence"],r["mean_net_return"],r["prior_ibs_shrunk_return"]),reverse=True)[:5]
    report=dict(as_of=date,mode="RESEARCH_ONLY",picks=picks,errors=errors,universe=len(SYMBOLS),prior_ibs_trades=sum(map(len,prior.values())))
    out=Path("data/daily_top5_latest.json");out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(report,indent=2))
    lines=[f"فرص الأسهم الأمريكية | إغلاق {date} | تجريبي"]
    for r in picks:
        lines.append(f'{r["symbol"]} | إغلاق ${r["reference_close"]} | دخول مرجعي ${r["entry_reference"]} | هدف ${r["target_reference"]} | وقف ${r["stop_reference"]} | نجاح تاريخي {r["historical_win_rate"]:.0%} (n={r["sample"]}) | PF {r["profit_factor"]}')
    if not picks:lines.append("لا توجد فرص تستوفي الشروط اليوم.")
    lines.append("الأسعار مرجعية وليست لحظية. نسبة النجاح تاريخية على فترة اختبار زمنية، بعد التكلفة، وليست ضماناً. الهدف والوقف على أساس سعر الافتتاح الفعلي ± متوسط المدى؛ الأرقام المعروضة مرجعية.")
    token=os.environ["TELEGRAM_BOT_TOKEN"]; chat=os.environ["TELEGRAM_CHAT_ID"]
    try:
        response=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",data={"chat_id":chat,"text":"\\n".join(lines).replace("\\n","\n")},timeout=20)
        if response.status_code!=200 or not response.json().get("ok"):raise RuntimeError("Telegram rejected message")
    except Exception:
        raise RuntimeError("Telegram delivery failed") from None
    print("Telegram API accepted message; picks:",len(picks))
if __name__=="__main__":main()
