"""Append-only assumed-fill paper portfolio. No real orders."""
import json
from pathlib import Path

STATE=Path("data/wide_market_ibs_forward.json")
LEDGER=Path("data/ibs_assumed_fills.jsonl")
CLOSED=Path("data/wide_market_ibs_closed_trades.jsonl")
SNAPSHOT=Path("data/ibs_assumed_portfolio_snapshot.json")
CAPITAL=3000.0
MAX_POSITION=750.0
MAX_EXPOSURE=1500.0
COST=0.003

def read_jsonl(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []

def record(state, ledger_path=LEDGER, closed_path=CLOSED, snapshot_path=SNAPSHOT):
    events=read_jsonl(ledger_path)
    closed=read_jsonl(closed_path)
    date=state["latest_downloaded_market_date"]
    keys={(e["date"],e["symbol"],e["action"]) for e in events}
    cash=CAPITAL
    holdings={}
    realized=0.0
    for e in events:
        symbol=e["symbol"]
        if e["action"]=="BUY":
            if symbol in holdings:raise ValueError("Duplicate open buy: "+symbol)
            cash-=e["quantity"]*e["price"]
            holdings[symbol]=(e["quantity"],e["price"],e["date"])
        elif e["action"]=="SELL":
            qty,entry,_=holdings.pop(symbol)
            if qty!=e["quantity"]:raise ValueError("Sell quantity mismatch: "+symbol)
            proceeds=qty*e["price"]*(1-COST)
            cash+=proceeds
            realized+=proceeds-qty*entry
    additions=[]
    # Complete only exits whose strategy ledger explicitly records an exit price and date.
    for t in sorted(closed,key=lambda x:(x["exit_date"],x["symbol"])):
        symbol=t["symbol"]
        if symbol not in holdings or t["exit_date"]>date:continue
        qty,entry,signal_date=holdings[symbol]
        if t["signal_date"]!=signal_date or (t["exit_date"],symbol,"SELL") in keys:continue
        price=float(t["exit_price"])
        if price<=0:continue
        proceeds=qty*price*(1-COST)
        sale={"action":"SELL","date":t["exit_date"],"symbol":symbol,"quantity":qty,
              "price":price,"entry_date":signal_date,"realized_pnl_usd":round(proceeds-qty*entry,2),
              "fill_type":"HYPOTHETICAL_STRATEGY_EXIT_NOT_BROKER_FILL"}
        additions.append(sale)
        keys.add((sale["date"],symbol,"SELL"))
        holdings.pop(symbol)
        cash+=proceeds
        realized+=proceeds-qty*entry
    candidates=[]
    for symbol in state.get("pending_entries",[]):
        p=state["states"][symbol].get("pending_entry")
        if p and p["signal_date"]==date:
            price=float(p["close"])
            if price>0:candidates.append((-(p["lower_band"]-price)/max(p["lower_band"],0.01),symbol,price))
    candidates.sort()
    for _,symbol,price in candidates[:3]:
        if (date,symbol,"BUY") in keys or symbol in holdings:continue
        exposure=sum(q*p for q,p,_ in holdings.values())
        qty=int(min(MAX_POSITION,MAX_EXPOSURE-exposure,cash)//price)
        if qty<=0:continue
        buy={"date":date,"symbol":symbol,"action":"BUY","quantity":qty,"price":price,
             "fill_type":"HYPOTHETICAL_SIGNAL_CLOSE_NOT_EXECUTABLE"}
        additions.append(buy)
        keys.add((date,symbol,"BUY"))
        cash-=qty*price
        holdings[symbol]=(qty,price,date)
    ledger_path.parent.mkdir(parents=True,exist_ok=True)
    with ledger_path.open("a") as out:
        for e in additions:out.write(json.dumps(e,sort_keys=True)+"\n")
    all_events=events+additions
    snapshot={"market_date":date,"starting_capital_usd":CAPITAL,"cash_usd":round(cash,2),
              "realized_pnl_usd":round(realized,2),
              "open_positions":{s:{"quantity":q,"assumed_entry_price":p,"signal_date":d} for s,(q,p,d) in holdings.items()},
              "paper_buys":sum(e["action"]=="BUY" for e in all_events),
              "paper_sells":sum(e["action"]=="SELL" for e in all_events),
              "new_events":len(additions),"portfolio_nav_usd":None,
              "warning":"Assumed fills only. NAV and unrealized P/L unavailable without independently verified market prices."}
    # Same-session signal close is a research proxy, NOT a verified live quote.
    marks={}
    for symbol,(qty,entry,signal_date) in holdings.items():
        p=state.get("states",{}).get(symbol,{}).get("pending_entry")
        if p and p.get("signal_date")==date and float(p.get("close",0))>0:
            price=float(p["close"])
            marks[symbol]={"price":round(price,4),"quantity":qty,
                           "market_value_usd":round(qty*price,2),
                           "unrealized_pnl_usd":round(qty*(price-entry),2),
                           "source":"same_session_signal_close_proxy","as_of":date}
    if len(marks)==len(holdings):
        snapshot["portfolio_nav_usd"]=round(cash+sum(x["market_value_usd"] for x in marks.values()),2)
        snapshot["unrealized_pnl_usd"]=round(sum(x["unrealized_pnl_usd"] for x in marks.values()),2)
        snapshot["total_pnl_usd"]=round(snapshot["portfolio_nav_usd"]-CAPITAL,2)
        snapshot["valuation_status"]="INDICATIVE_SAME_SESSION_CLOSE_NOT_LIVE"
    else:
        snapshot["unrealized_pnl_usd"]=None
        snapshot["total_pnl_usd"]=None
        snapshot["valuation_status"]="INCOMPLETE_MARKS"
    snapshot["marks"]=marks
    snapshot["warning"]="Indicative signal-close proxy only; not live or independently verified quotes, fills or broker NAV."
    snapshot_path.parent.mkdir(parents=True,exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot,indent=2)+"\n")
    return snapshot

if __name__=="__main__":
    print(json.dumps(record(json.loads(STATE.read_text())),indent=2))
