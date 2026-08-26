from __future__ import annotations
import json, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml
from .market import quote_daily, quote_intraday, health_snapshot, reset_health
from .strategies import intraday_setup, swing_setup
from .regime import detect
from .decision import finalize
from .portfolio import PortfolioStore
from .performance import save as save_performance
from .position_manager import advice
from .dashboard import render
from .telegram import enabled as telegram_enabled, send, signal_message
ROOT=Path(__file__).resolve().parents[2]

def load_yaml(p): return yaml.safe_load(Path(p).read_text(encoding="utf-8"))
def load_json(p,d):
    try:return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:return d
def write_json(p,d): Path(p).write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding="utf-8")
def env_num(n,d):
    v=os.getenv(n); return float(v) if v and v.strip() else float(d)
def market_open():
    n=datetime.now(ZoneInfo("America/New_York"))
    return n.weekday()<5 and 570<=n.hour*60+n.minute<=960

def scan_symbol(symbol,s,regime,portfolio,equity):
    daily=quote_daily(symbol)
    if len(daily)<55:return []
    price=float(daily["Close"].iloc[-1]); av=float(daily["Volume"].tail(20).mean())
    if price<float(s.get("min_price",5)) or av<float(s.get("min_avg_daily_volume",1_000_000)):return []
    out=[]
    sw=swing_setup(symbol,daily)
    if sw: out.append(finalize(sw,regime,s,portfolio,equity))
    try:
        intr=quote_intraday(symbol,s.get("intraday_period","5d"),s.get("intraday_interval","15m"))
        day=intraday_setup(symbol,intr)
        if day: out.append(finalize(day,regime,s,portfolio,equity))
    except Exception as e: print(f"{symbol} intraday warning: {e}")
    return out

def should_alert(sig,state,cooldown):
    k=f"{sig['symbol']}:{sig['strategy']}"; p=state.get(k)
    if not p:return True
    try:
        t=datetime.fromisoformat(p["sent_at"])
        if t.tzinfo is None:t=t.replace(tzinfo=timezone.utc)
        age=(datetime.now(timezone.utc)-t).total_seconds()/3600
    except Exception:return True
    old=float(p.get("price",sig["price"]))
    move=abs(float(sig["price"])-old)/max(old,.01)*100
    return age>=cooldown or int(sig["score"])>=int(p.get("score",0))+8 or move>=1

def run():
    s=load_yaml(ROOT/"config/settings.yml")["settings"]
    universe=load_yaml(ROOT/"config/universe.yml")["universe"]
    if os.getenv("FORCE_RUN")!="1" and not market_open():
        print("US market closed; scheduled scan skipped."); return 0
    reset_health()
    equity=env_num("ACCOUNT_EQUITY_USD",s.get("account_equity_usd",10000))
    s["risk_per_trade_pct"]=env_num("RISK_PER_TRADE_PCT",s.get("risk_per_trade_pct",.5))
    s["max_position_pct"]=env_num("MAX_POSITION_PCT",s.get("max_position_pct",15))
    store=PortfolioStore(ROOT/"data"); portfolio=store.portfolio()
    try: regime=detect()
    except Exception as e:
        print("DATA_ERROR regime:",e)
        if telegram_enabled():
            try: send("⛔ <b>DATA ERROR</b>\nتعذر تحميل بيانات السوق. لا توجد إشارات تداول.")
            except Exception: pass
        return 2
    signals=[]
    with ThreadPoolExecutor(max_workers=int(s.get("max_intraday_workers",6))) as pool:
        fut={pool.submit(scan_symbol,x,s,regime,portfolio,equity):x for x in universe}
        for f in as_completed(fut):
            try: signals.extend(f.result())
            except Exception as e: print("ERROR",fut[f],e)
    h=health_snapshot(); print("Market-data health:",h)
    total=h["success"]+h["failures"]
    if h["success"]<8 or (total and h["success"]/total<.35):
        reason=f"unreliable market data: {h['success']}/{total} successful requests"
        write_json(ROOT/"data/data_health.json",{"status":"DATA_ERROR","reason":reason,"health":h,
            "timestamp":datetime.now(timezone.utc).isoformat()})
        if telegram_enabled():
            try: send("⛔ <b>DATA ERROR — NO TRADING SIGNALS</b>\n"+reason)
            except Exception: pass
        print("DATA_ERROR:",reason); return 2
    write_json(ROOT/"data/data_health.json",{"status":"OK","health":h,
        "timestamp":datetime.now(timezone.utc).isoformat()})
    signals=sorted(signals,key=lambda x:x["score"],reverse=True)
    final=[]; seen={}
    for x in signals:
        if x["symbol"] not in seen:
            final.append(x); seen[x["symbol"]]=x
        elif x["score"]>=90 and seen[x["symbol"]]["strategy"]!=x["strategy"]: final.append(x)
    final=final[:30]; write_json(ROOT/"data/signals.json",final)
    hist=load_json(ROOT/"data/signal_history.json",[]); now=datetime.now(timezone.utc).isoformat()
    for x in final: hist.append({"timestamp":now,"symbol":x["symbol"],"strategy":x["strategy"],
        "signal":x["signal"],"score":x["score"],"price":x["price"],"stop_loss":x["stop_loss"],
        "target1":x["target1"],"target2":x["target2"]})
    write_json(ROOT/"data/signal_history.json",hist[-10000:])
    perf=save_performance(ROOT/"data")
    updates=[]
    for p in portfolio.get("positions",[]):
        try:
            d=quote_intraday(p["symbol"],"1d","15m")
            if not d.empty:
                cp=float(d["Close"].iloc[-1]); updates.append({"symbol":p["symbol"],"current_price":cp,**advice(p,cp)})
        except Exception as e: print("Position monitor",p["symbol"],e)
    write_json(ROOT/"data/position_advice.json",updates)
    render(final,ROOT/"docs/index.html",s.get("timezone","Asia/Riyadh"),regime,portfolio,perf)
    state=load_json(ROOT/"data/alert_state.json",{}); cooldown=float(s.get("alert_cooldown_hours",4))
    if telegram_enabled():
        for x in [z for z in final if z["signal"] in ("STRONG_BUY","BUY")][:int(s.get("top_n_alerts",3))]:
            if should_alert(x,state,cooldown):
                try:
                    send(signal_message(x)); state[f"{x['symbol']}:{x['strategy']}"]={
                        "sent_at":datetime.now(timezone.utc).isoformat(),"score":x["score"],"price":x["price"]}
                except Exception as e: print("Telegram",e)
        for u in updates:
            if u["action"] in ("EXIT","TAKE_PARTIAL","TIGHTEN/EXIT"):
                try: send(f"🔔 <b>{u['symbol']} — {u['action']}</b>\n{u['reason']}\nP/L: {u['pnl_pct']}%")
                except Exception: pass
        write_json(ROOT/"data/alert_state.json",state)
    print(f"Completed: {len(final)} ranked setups."); return 0

if __name__=="__main__": raise SystemExit(run())
