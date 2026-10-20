"""Read-only daily IBS research report. No broker orders or unvalidated stop levels."""
import json
from pathlib import Path

STATE = Path("data/wide_market_ibs_forward.json")
REPORT = Path("data/ibs_signal_review.json")

def build(data):
    states=data["states"]
    buys=[s for s in data.get("pending_entries",[]) if states.get(s,{}).get("pending_entry")]
    buys.sort(key=lambda s:(-(states[s]["pending_entry"]["lower_band"]-states[s]["pending_entry"]["close"])/max(states[s]["pending_entry"]["lower_band"],0.01),s))
    trades=[t for v in states.values() for t in v.get("trades",[])]
    trades.sort(key=lambda t:(t["exit_date"],t["symbol"],t["entry_date"]))
    wins=sum(t["net_return"]>0 for t in trades)
    gains=sum(max(0,t["net_return"]) for t in trades)
    losses=-sum(min(0,t["net_return"]) for t in trades)
    recent=trades[-30:]
    report={"market_date":data["latest_downloaded_market_date"],"mode":"PAPER_RESEARCH_ONLY","closed_trades":len(trades),"wins":wins,"win_rate":round(wins/len(trades),4) if trades else None,"profit_factor":round(gains/losses,3) if losses else None,"recent_30_win_rate":round(sum(t["net_return"]>0 for t in recent)/len(recent),4) if recent else None,"recent_30_avg_net_return":round(sum(t["net_return"] for t in recent)/len(recent),6) if recent else None,"selected_symbols":buys[:3],"no_live_execution":True}
    lines=[f"📈 إشارات IBS | {report['market_date']}","🟢 فرص شراء للمراقبة فقط"]
    if not buys:lines.append("لا توجد فرص شراء اليوم")
    for s in buys[:3]:
        p=states[s]["pending_entry"]
        lines.extend(["",f"📌 {s}",f"السعر المرجعي: ${float(p['close']):.2f}","الدخول: غير محدد","الخروج: غير محدد","احتمال الربح: غير محسوب"])
    exits=data.get("pending_exits",[])
    lines.extend(["","🔴 إشارات خروج تجريبية"])
    if not exits:lines.append("لا توجد")
    for s in exits:
        pos=states.get(s,{}).get("position") or {}
        entry=pos.get("entry_price")
        lines.append(f"• {s} | سعر الدخول التجريبي: ${float(entry):.2f}" if entry is not None else f"• {s}")
    lines.extend(["",f"📒 نتائج سابقة: {len(trades)} صفقة مغلقة",f"نسبة النجاح التاريخية: {report['win_rate']:.1%}" if trades else "نسبة النجاح: غير متاحة","⚠️ السعر المرجعي هو إغلاق الجلسة، وليس السعر الحالي.","🔒 محاكاة فقط؛ لا أوامر تداول حقيقية."])
    return report,"\n".join(lines)

def main():
    d=json.loads(STATE.read_text())
    if d.get("live_execution")!="LOCKED":raise SystemExit("Live execution must remain locked")
    report,message=build(d)
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n")
    print(message)

if __name__=="__main__":main()
