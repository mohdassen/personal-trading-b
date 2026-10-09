"""Read-only portfolio stress test for the frozen IBS ledger.

Capital model: equal allocation to all concurrently open positions, 100% cash
when flat, no leverage, positions marked at recorded entry/exit prices only.
This is an *approximation*, not a tradable daily NAV or true drawdown.
"""
import json
from collections import defaultdict
from pathlib import Path

state=json.loads(Path("data/wide_market_ibs_forward.json").read_text())
trades=[t for s in state["states"].values() for t in s["trades"]]
events=defaultdict(list)
for t in trades:
    events[t["entry_date"]].append(("open",t))
    events[t["exit_date"]].append(("close",t))
capital=1.0
active={}
daily=[]
max_concurrent=0
for day in sorted(events):
    # Existing positions exit at open, then new positions enter at open.
    exits=[t for kind,t in events[day] if kind=="close"]
    entries=[t for kind,t in events[day] if kind=="open"]
    if exits:
        # Allocate equally across positions existing just before exit.
        n=max(len(active),1)
        realized=sum(t["net_return"]/n for t in exits)
        capital*=1+realized
        for t in exits:active.pop((t["symbol"],t["entry_date"]),None)
    for t in entries:active[(t["symbol"],t["entry_date"])]=t
    max_concurrent=max(max_concurrent,len(active))
    daily.append({"date":day,"capital_proxy":round(capital,6),"open_positions":len(active)})
print(json.dumps({"closed_trades":len(trades),"max_concurrent_closed_trade_positions":max_concurrent,
 "capital_proxy":round(capital,6),"events":len(daily),
 "warning":"Proxy excludes open positions and interim marks, and cannot establish true portfolio P/L or max drawdown."},indent=2))
