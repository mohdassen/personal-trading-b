"""Retrospective cash-constrained replay of CLOSED paper trades only.
No open-position marks or true portfolio drawdown; closed-trade selection has lookahead bias.
No broker calls, no orders. Exit proceeds use recorded net returns after modeled costs.
"""
import json
from collections import defaultdict
from pathlib import Path

CAPITAL=3000.0
POSITION_CAP=750.0
EXPOSURE_CAP=1500.0
state=json.loads(Path("data/wide_market_ibs_forward.json").read_text())
trades=[t for s in state["states"].values() for t in s.get("trades",[])]
events=defaultdict(lambda:{"entry":[],"exit":[]})
for t in trades:
    events[t["entry_date"]]["entry"].append(t)
    events[t["exit_date"]]["exit"].append(t)
cash=CAPITAL
active={}
closed=[]
skipped=0
peak=CAPITAL
max_realized_only_dd=0.0
for day in sorted(events):
    for t in sorted(events[day]["exit"],key=lambda x:x["symbol"]):
        key=(t["symbol"],t["entry_date"])
        if key not in active:continue
        basis=active.pop(key)
        pnl=basis*t["net_return"]
        cash+=basis+pnl
        closed.append(pnl)
    exposure=sum(active.values())
    equity=cash+exposure
    peak=max(peak,equity)
    max_realized_only_dd=max(max_realized_only_dd,1-equity/peak)
    for t in sorted(events[day]["entry"],key=lambda x:x["symbol"]):
        price=float(t["entry_price"])
        exposure=sum(active.values())
        budget=min(POSITION_CAP,EXPOSURE_CAP-exposure,cash,0.25*(cash+exposure))
        qty=max(0,int(budget//price)) if price>0 else 0
        if not qty:
            skipped+=1
            continue
        basis=qty*price
        cash-=basis
        active[(t["symbol"],t["entry_date"])]=basis
profit=sum(x for x in closed if x>0)
loss=-sum(x for x in closed if x<0)
result={
 "initial_cash_usd":CAPITAL,
 "cash_usd":round(cash,2),
 "open_positions_at_cost_usd":round(sum(active.values()),2),
 "equity_at_cost_usd":round(cash+sum(active.values()),2),
 "realized_net_pnl_usd":round(sum(closed),2),
 "replayed_closed_trades":len(closed),
 "skipped_entries":skipped,
 "win_rate":round(sum(x>0 for x in closed)/len(closed),4) if closed else None,
 "profit_factor":round(profit/loss,3) if loss else None,
 "realized_only_drawdown_proxy_pct":round(100*max_realized_only_dd,2),
 "warning":"RETROSPECTIVE CLOSED TRADES ONLY: lookahead bias; omits live open positions, interim prices and true drawdown. NOT a forward portfolio or real-money proof."
}
print(json.dumps(result,indent=2))
