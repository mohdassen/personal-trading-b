from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import math
import pandas as pd
import yfinance as yf

OUT = Path("data/ibs_parallel_forward_accelerator.json")
EPOCH = pd.Timestamp("2026-09-18")
DECISION_DEADLINE = pd.Timestamp("2026-11-06")
COST_BPS = 30.0
RULE_VERSION = "IBS-FROZEN-2026-09-18-r1"

# Declared before any post-epoch outcomes are observed.
# QQQ is the original candidate; the remaining symbols are the prospective generalization basket.
SYMBOLS = [
    "QQQ","TSLA","NOW","PANW","CRWD","SNOW","DDOG",
    "NET","SHOP","UBER","ABNB","BKNG","MCD"
]


def _download(symbol: str) -> pd.DataFrame:
    end = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    x = yf.download(symbol, start="2021-01-01", end=end, interval="1d", auto_adjust=True,
                    progress=False, threads=False, timeout=30)
    if x.empty:
        return x
    if isinstance(x.columns, pd.MultiIndex):
        x.columns = x.columns.get_level_values(0)
    x = x[["Open","High","Low","Close"]].dropna().copy()
    idx = pd.DatetimeIndex(x.index)
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    x.index = idx.normalize()
    return x.sort_index()


def _prep(x: pd.DataFrame) -> pd.DataFrame:
    y = x.copy()
    y["range25"] = (y["High"] - y["Low"]).rolling(25).mean()
    y["high10"] = y["High"].rolling(10).max()
    y["lower_band"] = y["high10"] - 2.5*y["range25"]
    y["ibs"] = (y["Close"] - y["Low"]) / (y["High"] - y["Low"]).replace(0, pd.NA)
    y["sma300"] = y["Close"].rolling(300).mean()
    y["prev_high"] = y["High"].shift(1)
    return y


def _replay_symbol(symbol: str, x: pd.DataFrame) -> dict:
    pending_entry = None
    pending_exit = None
    position = None
    trades = []
    last_decision = "NO_SIGNAL"

    for i in range(len(x)):
        date = x.index[i]
        if date < EPOCH:
            continue
        row = x.iloc[i]
        exited_today = False

        if pending_exit is not None and position is not None:
            exit_price = float(row["Open"])
            gross = exit_price / position["entry_price"] - 1.0
            net = gross - COST_BPS/10000.0
            trades.append({
                "symbol": symbol,
                "signal_date": position["signal_date"],
                "entry_date": position["entry_date"],
                "exit_signal_date": pending_exit["signal_date"],
                "exit_date": str(date.date()),
                "gross_return": round(gross, 6),
                "net_return": round(net, 6),
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

        if position is not None and pending_exit is None:
            exit_signal = (
                pd.notna(row["prev_high"]) and float(row["Close"]) > float(row["prev_high"])
            ) or (
                pd.notna(row["sma300"]) and float(row["Close"]) < float(row["sma300"])
            )
            if exit_signal:
                pending_exit = {"signal_date": str(date.date())}
                last_decision = "EXIT_NEXT_OPEN"

        if position is None and pending_entry is None and pending_exit is None and not exited_today:
            entry_signal = (
                pd.notna(row["lower_band"]) and
                pd.notna(row["ibs"]) and
                float(row["Close"]) < float(row["lower_band"]) and
                float(row["ibs"]) < 0.30
            )
            if entry_signal:
                pending_entry = {
                    "signal_date": str(date.date()),
                    "close": round(float(row["Close"]), 6),
                    "lower_band": round(float(row["lower_band"]), 6),
                    "ibs": round(float(row["ibs"]), 6),
                }
                last_decision = "BUY_NEXT_OPEN"

    return {
        "symbol": symbol,
        "last_decision": last_decision,
        "pending_entry": pending_entry,
        "pending_exit": pending_exit,
        "open_position": position,
        "trades": trades,
    }


def _stats(trades: list[dict]) -> dict:
    r = [float(t["net_return"]) for t in trades]
    if not r:
        return {"trades":0,"avg_net_return":None,"win_rate":None,"profit_factor":None}
    w = [x for x in r if x > 0]
    l = [x for x in r if x < 0]
    pf = sum(w)/abs(sum(l)) if l else 999.0
    return {
        "trades": len(r),
        "avg_net_return": round(sum(r)/len(r), 6),
        "win_rate": round(len(w)/len(r), 4),
        "profit_factor": round(pf, 3),
    }


def _cluster_stats(trades: list[dict]) -> dict:
    by_signal = defaultdict(list)
    for t in trades:
        by_signal[t["signal_date"]].append(float(t["net_return"]))
    clusters = []
    for d, vals in sorted(by_signal.items()):
        clusters.append({"signal_date":d,"symbols":len(vals),"cluster_return":sum(vals)/len(vals)})
    rs = [c["cluster_return"] for c in clusters]
    if not rs:
        return {"unique_signal_clusters":0,"avg_cluster_return":None,"cluster_profit_factor":None,
                "max_cluster_drawdown":None,"clusters":[]}
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    pf = sum(wins)/abs(sum(losses)) if losses else 999.0
    wealth = 1.0
    peak = 1.0
    mdd = 0.0
    for r in rs:
        wealth *= 1+r
        peak = max(peak, wealth)
        mdd = max(mdd, 1-wealth/peak)
    return {
        "unique_signal_clusters": len(rs),
        "avg_cluster_return": round(sum(rs)/len(rs), 6),
        "cluster_profit_factor": round(pf, 3),
        "max_cluster_drawdown": round(mdd, 4),
        "clusters": clusters,
    }


def main():
    states = []
    all_trades = []
    represented = set()
    latest_date = None

    for symbol in SYMBOLS:
        raw = _download(symbol)
        if raw.empty or len(raw) < 320:
            states.append({"symbol":symbol,"status":"INSUFFICIENT_DATA"})
            continue
        latest_date = max(latest_date or raw.index[-1], raw.index[-1])
        state = _replay_symbol(symbol, _prep(raw))
        states.append(state)
        all_trades.extend(state["trades"])
        if state["trades"]:
            represented.add(symbol)

    overall = _stats(all_trades)
    cluster = _cluster_stats(all_trades)
    as_of = latest_date or pd.Timestamp.utcnow().tz_localize(None).normalize()

    # Locked promotion gate, declared before post-epoch outcomes.
    evidence_gate = (
        overall["trades"] >= 20 and
        cluster["unique_signal_clusters"] >= 12 and
        len(represented) >= 8 and
        overall["profit_factor"] is not None and overall["profit_factor"] >= 1.25 and
        overall["avg_net_return"] is not None and overall["avg_net_return"] >= 0.001 and
        cluster["cluster_profit_factor"] is not None and cluster["cluster_profit_factor"] >= 1.20 and
        cluster["max_cluster_drawdown"] is not None and cluster["max_cluster_drawdown"] <= 0.12
    )

    if evidence_gate:
        status = "FORWARD_GENERALIZATION_GATE_PASSED"
    elif as_of >= DECISION_DEADLINE:
        status = "TIMEBOX_EXPIRED_NO_PROOF"
    else:
        status = "COLLECTING_FORWARD_EVIDENCE"

    result = {
        "strategy":"IBS_LOWER_BAND_PARALLEL",
        "rule_version":RULE_VERSION,
        "epoch":str(EPOCH.date()),
        "decision_deadline":str(DECISION_DEADLINE.date()),
        "mode":"FORWARD_SHADOW_ONLY_NO_ORDERS",
        "live_execution":"LOCKED",
        "status":status,
        "as_of":str(as_of.date()),
        "symbols":SYMBOLS,
        "cost_assumption_roundtrip_bps":COST_BPS,
        "promotion_gate":{
            "minimum_closed_trades":20,
            "minimum_unique_signal_clusters":12,
            "minimum_symbols_represented":8,
            "minimum_profit_factor":1.25,
            "minimum_avg_net_return":0.001,
            "minimum_cluster_profit_factor":1.20,
            "maximum_cluster_drawdown":0.12,
            "deadline":str(DECISION_DEADLINE.date()),
        },
        "overall":overall,
        "unique_symbols_with_closed_trades":len(represented),
        "cluster_evidence":cluster,
        "states":states,
        "decision_rule":"Pass only if every locked gate is met before the deadline. Otherwise reject/replace; no waiting extension.",
    }

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
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps({
        "status":status,
        "as_of":result["as_of"],
        "overall":overall,
        "unique_symbols_with_closed_trades":len(represented),
        "cluster_evidence":{k:v for k,v in cluster.items() if k!="clusters"}
    },indent=2))


if __name__=="__main__":
    main()
