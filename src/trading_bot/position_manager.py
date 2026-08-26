from __future__ import annotations

def advice(position, current_price, vwap=None, ema21=None):
    entry=float(position["avg_price"])
    stop=float(position.get("stop",0) or 0)
    t1=float(position.get("target1",0) or 0)
    t2=float(position.get("target2",0) or 0)
    strategy=str(position.get("strategy","manual")).upper()

    pnl=(current_price-entry)/entry*100 if entry else 0

    if stop and current_price<=stop:
        return {
            "action":"EXIT_NOW",
            "reason":"Stop-loss reached",
            "pnl_pct":round(pnl,2),
            "new_stop":stop,
        }

    if t2 and current_price>=t2:
        return {
            "action":"EXIT_NOW",
            "reason":"Target 2 reached",
            "pnl_pct":round(pnl,2),
            "new_stop":current_price,
        }

    if t1 and current_price>=t1:
        # Protect the remaining half at least around breakeven.
        new_stop=max(entry, stop)
        return {
            "action":"TAKE_50_PERCENT",
            "reason":"Target 1 reached; take partial profit and protect remainder",
            "pnl_pct":round(pnl,2),
            "new_stop":round(new_stop,2),
        }

    # Profit protection before T1.
    if pnl >= 1.5:
        new_stop=max(stop, entry)
        return {
            "action":"HOLD_RAISE_STOP",
            "reason":"Trade is profitable; move stop to breakeven",
            "pnl_pct":round(pnl,2),
            "new_stop":round(new_stop,2),
        }

    if vwap and ema21 and current_price<vwap and current_price<ema21:
        return {
            "action":"TIGHTEN_OR_EXIT",
            "reason":"Price lost both VWAP and EMA21",
            "pnl_pct":round(pnl,2),
            "new_stop":round(max(stop,current_price*0.995),2),
        }

    # Day trades should not be carried automatically.
    if strategy=="DAY" and pnl < -0.75:
        return {
            "action":"REVIEW_EXIT",
            "reason":"Day-trade momentum is weak and loss is expanding",
            "pnl_pct":round(pnl,2),
            "new_stop":round(max(stop,current_price*0.995),2),
        }

    return {
        "action":"HOLD",
        "reason":"No exit trigger",
        "pnl_pct":round(pnl,2),
        "new_stop":round(stop,2),
    }
