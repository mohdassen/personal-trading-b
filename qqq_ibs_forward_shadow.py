from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import yfinance as yf

OUT = Path("data/qqq_ibs_forward_shadow.json")
EPOCH = pd.Timestamp("2026-09-18")
STRATEGY = "QQQ_IBS_LOWER_BAND"
FROZEN_RULE_VERSION = "2026-09-18-r1"
ASSUMED_ROUNDTRIP_COST_BPS = 30.0


def download() -> pd.DataFrame:
    end = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    x = yf.download("QQQ", start="2021-01-01", end=end, interval="1d", auto_adjust=True,
                    progress=False, threads=False, timeout=30)
    if isinstance(x.columns, pd.MultiIndex):
        x.columns = x.columns.get_level_values(0)
    x = x[["Open", "High", "Low", "Close"]].dropna().copy()
    idx = pd.DatetimeIndex(x.index)
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    x.index = idx.normalize()
    return x.sort_index()


def prep(x: pd.DataFrame) -> pd.DataFrame:
    y = x.copy()
    y["range25"] = (y["High"] - y["Low"]).rolling(25).mean()
    y["high10"] = y["High"].rolling(10).max()
    y["lower_band"] = y["high10"] - 2.5 * y["range25"]
    y["ibs"] = (y["Close"] - y["Low"]) / (y["High"] - y["Low"]).replace(0, pd.NA)
    y["sma300"] = y["Close"].rolling(300).mean()
    y["prev_high"] = y["High"].shift(1)
    return y


def replay(x: pd.DataFrame) -> dict:
    pending_entry = None
    pending_exit = None
    position = None
    trades = []
    last_decision = "NO_SIGNAL"

    for i in range(len(x)):
        date = x.index[i]
        row = x.iloc[i]
        if date < EPOCH:
            continue

        exited_today = False

        # Orders are shadow fills only. A decision made at prior close fills at today's open.
        if pending_exit is not None and position is not None:
            exit_price = float(row["Open"])
            gross = exit_price / position["entry_price"] - 1.0
            net = gross - ASSUMED_ROUNDTRIP_COST_BPS / 10000.0
            trades.append({
                "signal_date": position["signal_date"],
                "entry_date": position["entry_date"],
                "entry_price": round(position["entry_price"], 6),
                "exit_signal_date": pending_exit["signal_date"],
                "exit_date": str(date.date()),
                "exit_price": round(exit_price, 6),
                "gross_return": round(gross, 6),
                "net_return_30bps": round(net, 6),
            })
            position = None
            pending_exit = None
            exited_today = True
            last_decision = "EXIT_FILLED_SHADOW"

        if pending_entry is not None and position is None and not exited_today:
            position = {
                "signal_date": pending_entry["signal_date"],
                "entry_date": str(date.date()),
                "entry_price": float(row["Open"]),
            }
            pending_entry = None
            last_decision = "ENTRY_FILLED_SHADOW"

        # At today's close, generate tomorrow's exit decision if a position is open.
        if position is not None and pending_exit is None:
            exit_signal = (
                pd.notna(row["prev_high"]) and float(row["Close"]) > float(row["prev_high"])
            ) or (
                pd.notna(row["sma300"]) and float(row["Close"]) < float(row["sma300"])
            )
            if exit_signal:
                pending_exit = {"signal_date": str(date.date())}
                last_decision = "EXIT_NEXT_OPEN"

        # Match the research engine: do not generate a new entry on an exit-fill day.
        if position is None and pending_entry is None and pending_exit is None and not exited_today:
            entry_signal = (
                pd.notna(row["lower_band"])
                and pd.notna(row["ibs"])
                and float(row["Close"]) < float(row["lower_band"])
                and float(row["ibs"]) < 0.30
            )
            if entry_signal:
                pending_entry = {
                    "signal_date": str(date.date()),
                    "signal_close": round(float(row["Close"]), 6),
                    "lower_band": round(float(row["lower_band"]), 6),
                    "ibs": round(float(row["ibs"]), 6),
                }
                last_decision = "BUY_NEXT_OPEN"

    realized = [t["net_return_30bps"] for t in trades]
    wins = [r for r in realized if r > 0]
    losses = [r for r in realized if r < 0]
    pf = (sum(wins) / abs(sum(losses))) if losses else (999.0 if wins else None)

    latest = x.iloc[-1]
    return {
        "strategy": STRATEGY,
        "frozen_rule_version": FROZEN_RULE_VERSION,
        "epoch": str(EPOCH.date()),
        "mode": "FORWARD_SHADOW_ONLY_NO_ORDERS",
        "as_of": str(x.index[-1].date()),
        "last_decision": last_decision,
        "pending_entry": pending_entry,
        "pending_exit": pending_exit,
        "open_position": position,
        "realized_trade_count": len(trades),
        "realized_win_rate": round(len(wins) / len(realized), 4) if realized else None,
        "realized_avg_net_return_30bps": round(sum(realized) / len(realized), 6) if realized else None,
        "realized_profit_factor": round(pf, 3) if pf is not None else None,
        "realized_trades": trades,
        "latest_bar": {
            "date": str(x.index[-1].date()),
            "open": round(float(latest["Open"]), 6),
            "high": round(float(latest["High"]), 6),
            "low": round(float(latest["Low"]), 6),
            "close": round(float(latest["Close"]), 6),
            "lower_band": round(float(latest["lower_band"]), 6) if pd.notna(latest["lower_band"]) else None,
            "ibs": round(float(latest["ibs"]), 6) if pd.notna(latest["ibs"]) else None,
            "sma300": round(float(latest["sma300"]), 6) if pd.notna(latest["sma300"]) else None,
        },
        "rules": {
            "entry": "after close: Close < 10-day high - 2.5 * 25-day average range AND IBS < 0.30; shadow fill next open",
            "exit": "after close: Close > prior-day High OR Close < SMA300; shadow fill next open",
            "cost_assumption_bps_roundtrip": ASSUMED_ROUNDTRIP_COST_BPS,
        },
        "promotion_gate": {
            "minimum_closed_forward_trades": 30,
            "minimum_profit_factor": 1.20,
            "minimum_avg_net_return": 0.0,
            "maximum_allowed_drawdown_note": "must be reviewed from chronological equity curve before any real-money approval",
        },
        "live_execution": "LOCKED",
    }


def main():
    x = prep(download())
    if x.empty or x.index[-1] < EPOCH:
        result = {
            "strategy": STRATEGY,
            "frozen_rule_version": FROZEN_RULE_VERSION,
            "epoch": str(EPOCH.date()),
            "mode": "FORWARD_SHADOW_ONLY_NO_ORDERS",
            "status": "WAITING_FOR_FIRST_POST_EPOCH_COMPLETED_DAILY_BAR",
            "live_execution": "LOCKED",
        }
    else:
        result = replay(x)
    # Never let a stale upstream response roll the forward clock backward.
    if OUT.exists() and result.get("as_of"):
        try:
            previous = json.loads(OUT.read_text(encoding="utf-8"))
            prev_as_of = previous.get("as_of")
            if prev_as_of and pd.Timestamp(prev_as_of) > pd.Timestamp(result["as_of"]):
                print(json.dumps({"status":"STALE_SOURCE_IGNORED","download_as_of":result["as_of"],"kept_as_of":prev_as_of}, indent=2))
                return
        except Exception:
            pass
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
