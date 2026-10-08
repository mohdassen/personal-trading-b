"""Read-only paper-signal monitor. Never places trades."""
import json
from pathlib import Path
from datetime import date,datetime,timezone
import yfinance as yf

state=json.loads(Path("data/wide_market_ibs_forward.json").read_text())
approved=json.loads(Path("config/sharia_approved.json").read_text())
valid=set()
for symbol,record in approved.items():
    if not isinstance(record,dict) or not record.get("approved") or not record.get("source"): continue
    try:
        age=(date.today()-date.fromisoformat(record["review_date"])).days
        if 0<=age<=90: valid.add(symbol)
    except (ValueError,KeyError,TypeError): pass
signals=[("BUY",s) for s in state.get("pending_entries",[]) if s in valid]
signals +=[("EXIT",s) for s in state.get("pending_exits",[]) if s in valid]
print("Verified symbols:",len(valid),"Eligible pending signals:",len(signals))
for side,symbol in signals:
    bars=yf.Ticker(symbol).history(period="2d",interval="5m",prepost=True)
    if bars.empty:
        print(side,symbol,"no recent quote");continue
    ts=bars.index[-1].to_pydatetime()
    if ts.tzinfo is None: ts=ts.replace(tzinfo=timezone.utc)
    age=(datetime.now(timezone.utc)-ts.astimezone(timezone.utc)).total_seconds()
    if not 0<=age<=3600:
        print(side,symbol,"stale quote");continue
    print(side,symbol,"reference price",round(float(bars.iloc[-1]["Close"]),2),"timestamp",ts.isoformat())
