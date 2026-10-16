"""Read-only paper-signal monitor. Never places trades."""
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import yfinance as yf

state=json.loads(Path("data/wide_market_ibs_forward.json").read_text())
market_day=state.get("latest_downloaded_market_date")
if not market_day:
    raise SystemExit("BLOCKED: no dated market snapshot")
ny=datetime.now(ZoneInfo("America/New_York"))
last_day=datetime.fromisoformat(market_day).date()
# Allow Friday signals over weekends, but never older than three calendar days.
if (ny.date()-last_day).days>3 or last_day>ny.date():
    raise SystemExit(f"BLOCKED: stale market snapshot {market_day}")
valid=set(state.get("states",{}))
signals=[("BUY",s) for s in state.get("pending_entries",[]) if s in valid]
signals +=[("EXIT",s) for s in state.get("pending_exits",[]) if s in valid]
print("Monitored symbols:",len(valid),"Pending paper signals:",len(signals))
messages=[]
for side,symbol in signals:
    bars=yf.Ticker(symbol).history(period="2d",interval="5m",prepost=True)
    if bars.empty:
        print(side,symbol,"no recent quote");continue
    ts=bars.index[-1].to_pydatetime()
    if ts.tzinfo is None: ts=ts.replace(tzinfo=timezone.utc)
    age=(datetime.now(timezone.utc)-ts.astimezone(timezone.utc)).total_seconds()
    if not 0<=age<=3600:
        print(side,symbol,"stale quote");continue
    price=round(float(bars.iloc[-1]["Close"]),2)
    print(side,symbol,"reference price",price,"timestamp",ts.isoformat())
    detail=state["states"].get(symbol,{})
    pending=detail.get("pending_entry") or {}
    position=detail.get("position") or {}
    signal_date=pending.get("signal_date") if side=="BUY" else (detail.get("pending_exit") or {}).get("signal_date")
    context=f" | signal close USD {pending['close']}" if side=="BUY" and pending.get("close") is not None else ""
    context+=f" | paper entry USD {position['entry_price']:.2f}" if side=="EXIT" and position.get("entry_price") is not None else ""
    messages.append(f"{side} {symbol} | quote USD {price} | signal {signal_date} | {ts.isoformat()}{context} | reference only, no target or stop defined")

if messages:
    token=os.environ.get("TELEGRAM_BOT_TOKEN")
    chat=os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        msg="PAPER WATCH - NOT LIVE TRADING ORDERS\n"+"\n".join(messages)
        data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
        request=urllib.request.Request("https://api.telegram.org/bot"+token+"/sendMessage",data=data)
        with urllib.request.urlopen(request,timeout=20) as response:
            if response.status!=200: raise RuntimeError(f"Telegram HTTP {response.status}")
            payload=json.load(response)
            if not payload.get("ok"): raise RuntimeError("Telegram rejected alert")
            print("Telegram confirmed message id",payload.get("result",{}).get("message_id"))
            Path(".alert-sent-marker").write_text(str(payload.get("result",{}).get("message_id","sent")))
    else: raise SystemExit("BLOCKED: Telegram credentials unavailable")
