"""Conservative sizing guard for manual US-equity trading.

No orders, no strategy modification. A stop must be supplied by a separately
validated trading plan; the frozen IBS strategy does not define one.
"""
from math import floor, isfinite

CAPITAL_USD = 1000.0
MAX_POSITION_USD = 250.0
MAX_TRADE_RISK_USD = 5.0
MAX_TOTAL_EXPOSURE_USD = 500.0
MAX_DAILY_LOSS_USD = 15.0

def size_trade(entry, stop, *, equity=CAPITAL_USD, open_exposure=0.0,
               realized_daily_pnl=0.0, available_cash=None):
    vals=(entry,stop,equity,open_exposure,realized_daily_pnl)
    if not all(isinstance(v,(int,float)) and isfinite(v) for v in vals):
        raise ValueError("finite numeric values required")
    if entry<=0 or stop<=0 or stop>=entry or equity<=0 or open_exposure<0:
        raise ValueError("invalid long trade / stop configuration")
    if realized_daily_pnl<=-MAX_DAILY_LOSS_USD:
        return {"allowed":False,"reason":"daily loss limit reached","shares":0}
    if available_cash is None:available_cash=equity-open_exposure
    if not isfinite(available_cash) or available_cash<0:
        raise ValueError("invalid available cash")
    cap=min(MAX_POSITION_USD,MAX_TOTAL_EXPOSURE_USD-open_exposure,available_cash)
    qty=max(0,min(floor(cap/entry),floor(MAX_TRADE_RISK_USD/(entry-stop))))
    if qty==0:return {"allowed":False,"reason":"risk or cash constraints","shares":0}
    return {"allowed":True,"shares":qty,"notional_usd":round(qty*entry,2),
            "planned_stop_risk_usd":round(qty*(entry-stop),2),
            "warning":"Stops are not guaranteed fills; gaps and fees can exceed planned loss. Manual execution only."}

if __name__=="__main__":
    assert size_trade(100,98)["shares"]==2
    assert size_trade(100,98,open_exposure=500)["allowed"] is False
    assert size_trade(100,98,realized_daily_pnl=-15)["allowed"] is False
    try:size_trade(100,101)
    except ValueError:pass
    else:raise AssertionError("invalid stop accepted")
    print("Risk guard self-tests PASS")
