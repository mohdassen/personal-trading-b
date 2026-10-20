"""Append-only simulated signal fills, not broker executions or verified market fills."""
import json
from pathlib import Path
from datetime import datetime, timezone
STATE=Path("data/wide_market_ibs_forward.json")
LEDGER=Path("data/ibs_assumed_fills.jsonl")
CAPITAL=3000.0
MAX_POSITION=750.0
MAX_EXPOSURE=1500.0
COST=0.003

def record(state, ledger_path=LEDGER):
    events=[json.loads(x) for x in ledger_path.read_text().splitlines() if x.strip()] if ledger_path.exists() else []
    keys={(e["date"],e["symbol"],e["action"]) for e in events}
    cash=CAPITAL
    holdings={}
    realized=0.0
    for e in events:
        if e["action"]=="BUY":
            cash-=e["quantity"]*e["price"];holdings[e["symbol"]]=(e["quantity"],e["price"])
        else:
            qty,entry=holdings.pop(e["symbol"])
            proceeds=qty*e["price"]*(1-COST)
            cash+=proceeds;realized+=proceeds-qty*entry
    date=state["latest_downloaded_market_date"]
    # Paper entries use the signal-date close as a hypothetical proxy, never an actual fill.
    candidates=[]
    for symbol in state.get("pending_entries",[]):
        p=state["states"][symbol].get("pending_entry")
        if p and p["signal_date"]==date:
            candidates.append((-(p["lower_band"]-p["close"])/max(p["lower_band"],0.01),symbol,float(p["close"])))
    candidates.sort()
    additions=[]
    for _,symbol,price in candidates[:3]:
        if (date,symbol,"BUY") in keys or symbol in holdings or price<=0:continue
        exposure=sum(q*p for q,p in holdings.values())
        qty=int(min(MAX_POSITION,MAX_EXPOSURE-exposure,cash)//price)
        if qty<=0:continue
        e={"date":date,"symbol":symbol,"action":"BUY","quantity":qty,"price":price,"fill_type":"HYPOTHETICAL_SIGNAL_CLOSE_NOT_EXECUTABLE"}
        additions.append(e);keys.add((date,symbol,"BUY"));cash-=qty*price;holdings[symbol]=(qty,price)
    # A pending EXIT lacks a current executable price: do not fabricate a sale.
    ledger_path.parent.mkdir(parents=True,exist_ok=True)
    with ledger_path.open("a") as out:
        for e in additions:out.write(json.dumps(e,sort_keys=True)+"\n")
    return {"cash_usd":round(cash,2),"holdings":len(holdings),"realized_pnl_usd":round(realized,2),"new_hypothetical_buys":len(additions),"pending_exits_not_filled":len(state.get("pending_exits",[])),"warning":"Hypothetical signal-close entries only. No verified fills or mark-to-market."}

if __name__=="__main__":
    result=record(json.loads(STATE.read_text()))
    print(json.dumps(result,indent=2))
