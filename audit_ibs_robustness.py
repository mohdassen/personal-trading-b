"""Independent, read-only robustness audit of the frozen IBS forward ledger.

No trade simulation, strategy tuning, order placement, or data modification.
Run: python audit_ibs_robustness.py
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

root=Path("data")
state=json.loads((root/"wide_market_ibs_forward.json").read_text())
trades=[t for s in state["states"].values() for t in s["trades"]]
lines=[json.loads(x) for x in (root/"wide_market_ibs_closed_trades.jsonl").read_text().splitlines() if x.strip()]
key=lambda t:(t["symbol"],t["signal_date"],t["entry_date"],t["exit_date"])
assert len(trades)==len(lines)==len({key(t) for t in trades})
assert {key(t) for t in trades}=={key(t) for t in lines}
for t in trades:
    assert abs(t["exit_price"]/t["entry_price"]-1-t["gross_return"])<0.00001
    assert abs(t["gross_return"]-state["cost_bps_roundtrip"]/10000-t["net_return"])<0.00001
    assert t["signal_date"]<t["entry_date"]<=t["exit_date"]
    assert t["exit_signal_date"]<t["exit_date"]
returns=[t["net_return"] for t in trades]
wins=[x for x in returns if x>0]
losses=[x for x in returns if x<0]
entry_days=Counter(t["entry_date"] for t in trades)
exit_days=Counter(t["exit_date"] for t in trades)
symbol_pnl=defaultdict(float)
for t in trades:symbol_pnl[t["symbol"]]+=t["net_return"]
sorted_r=sorted(returns,reverse=True)
def metrics(r):
    pos=sum(x for x in r if x>0)
    neg=-sum(x for x in r if x<0)
    return {"count":len(r),"mean_net":round(sum(r)/len(r),6) if r else None,
            "win_rate":round(sum(x>0 for x in r)/len(r),4) if r else None,
            "profit_factor":round(pos/neg,3) if neg else None}
by_entry=defaultdict(list)
for t in trades:by_entry[t["entry_date"]].append(t["net_return"])
equal_weight_day_returns=[sum(r)/len(r) for _,r in sorted(by_entry.items())]
report={
 "scope":"Ledger consistency and concentration only; NOT independently verified execution or tradable portfolio",
 "market_date":state["latest_downloaded_market_date"],
 "ledger_consistency":"PASS",
 "reported":state["stats"],
 "recomputed":metrics(returns),
 "unique_entry_days":len(entry_days),
 "entry_day_counts":dict(sorted(entry_days.items())),
 "largest_two_entry_days_fraction":round(sum(n for _,n in entry_days.most_common(2))/len(trades),4),
 "top_10_positive_contribution_fraction_of_net_sum":round(sum(sorted_r[:10])/sum(returns),4) if sum(returns) else None,
 "without_best_5":metrics(sorted_r[5:]),
 "without_best_10":metrics(sorted_r[10:]),
 "equal_weight_per_entry_day":metrics(equal_weight_day_returns),
 "unique_symbols":len(symbol_pnl),
 "caveats":["Trades sharing a day are correlated.","No independent point-in-time price verification.",
 "No portfolio sizing, capital constraints or true portfolio drawdown.",
 "No real fills, spread, market impact or broker reconciliation.",
 "Only a short forward period; no claim of future profitability."]
}
print(json.dumps(report,indent=2))
