"""Read-only paper-signal monitor. Never places trades."""
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import date,datetime,timezone
import yfinance as yf

state=json.loads(Path("data/wide_market_ibs_forward.json").read_text())
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
    messages.append(f"{side} {symbol} | indicative quote USD {price} | {ts.isoformat()}")

if messages:
    token=os.environ.get("TELEGRAM_BOT_TOKEN")
    chat=os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        msg="PAPER WATCH - NOT LIVE TRADING ORDERS\n"+"\n".join(messages)
        data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
        request=urllib.request.Request("https://api.telegram.org/bot"+token+"/sendMessage",data=data)
        with urllib.request.urlopen(request,timeout=20) as response:
            print("Telegram HTTP",response.status)
    else: print("Telegram credentials unavailable")
