"""Read-only daily strategy exposure inventory; NOT broker holdings or NAV."""
import json
from pathlib import Path

def main():
    d=json.loads(Path("data/wide_market_ibs_forward.json").read_text())
    assert d["live_execution"]=="LOCKED"
    positions=[{"symbol":symbol,"entry_price":p["entry_price"],"entry_date":p["entry_date"]}
        for symbol,s in sorted(d["states"].items()) if (p:=s.get("position"))]
    report={"date":d["latest_downloaded_market_date"],"mode":"PAPER_ONLY",
        "capital_usd":3000,"daily_loss_limit_usd":100,
        "strategy_positions":positions,"count":len(positions),
        "portfolio_cash_usd":None,"portfolio_nav_usd":None,
        "max_drawdown_pct":None,
        "warning":"Strategy positions are not cash-account fills; NAV and true drawdown remain unverified."}
    out=Path("data/ibs_portfolio_daily_snapshot.json")
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k!="strategy_positions"},indent=2))

if __name__=="__main__":main()
