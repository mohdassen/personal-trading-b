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
    lines=["📊 IBS | DAILY PAPER WATCH",f"📅 Market session: {report['market_date']}","🔒 RESEARCH ONLY — no live orders","━━━━━━━━━━━━━━━━","🟢 TOP 3 BUY WATCHLIST (not executable)"]
    if not buys:lines.append("No qualified IBS buy signals.")
    for n,s in enumerate(buys[:3],1):
        p=states[s]["pending_entry"];price=float(p["close"]);qty=int(750//price) if price>0 else 0
        lines.extend([f"{n}. {s} | Reference close: ${price:.2f}",f"   IBS: {p['ibs']:.2f} | Theoretical max: {qty} shares", "   Entry: NOT VALIDATED | Stop: NOT VALIDATED","   Target: NOT VALIDATED | Action: WATCH ONLY"])
    exits=data.get("pending_exits",[])
    lines.extend(["━━━━━━━━━━━━━━━━",f"🟠 PAPER EXIT WATCH: {len(exits)}"])
    for s in exits[:8]:lines.append(f"• {s} — check actual holdings before any sell")
    if len(exits)>8:lines.append(f"… and {len(exits)-8} more in saved state")
    lines.extend(["━━━━━━━━━━━━━━━━",f"📒 CLOSED PAPER TRADES: {len(trades)}",f"Win rate: {report['win_rate']:.1%}" if trades else "Win rate: N/A",f"Profit factor: {report['profit_factor']}" if report["profit_factor"] is not None else "Profit factor: N/A",f"Recent 30 win rate: {report['recent_30_win_rate']:.1%}" if recent else "Recent 30 win rate: N/A","⚠️ These are strategy-level shadow outcomes, not a cash-portfolio P/L.","📌 Learning: monitor rolling outcomes; no automatic rule changes or lookahead.","⚠️ No validated stop/entry/target; do not place orders based on this alert."])
    return report,"\n".join(lines)

def main():
    d=json.loads(STATE.read_text())
    if d.get("live_execution")!="LOCKED":raise SystemExit("Live execution must remain locked")
    report,message=build(d)
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n")
    print(message)

if __name__=="__main__":main()
