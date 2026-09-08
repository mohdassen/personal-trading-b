"""V7 adaptive decision engine: catalyst + strategy router + multi-timeframe confirmation.

Research/PAPER only. No broker integration and no live order placement.
V6.1 remains unchanged as the comparison baseline.
"""
from __future__ import annotations

import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import v6_discovery as base
import v6_session_discovery as v61
from src.trading_bot.indicators import add_daily, add_intraday
from src.trading_bot.market import quote_daily, quote_intraday, recent_news
from src.trading_bot.regime import detect as detect_regime
from src.trading_bot.telegram import enabled as telegram_enabled, send

ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / "data/v7_decision_snapshot.json"
PAPER_STATE = ROOT / "data/v7_paper_state.json"
ALERT_STATE = ROOT / "data/v7_alert_state.json"
ENGINE = "V7-Catalyst-Router-MTF"
MODE = "RESEARCH_PAPER_ONLY"
MAX_ENRICH = 18
SLIPPAGE_BPS = 10

POSITIVE_TERMS = {
    "beats": "EARNINGS_BEAT", "beat estimates": "EARNINGS_BEAT", "raises guidance": "GUIDANCE_UP",
    "raised guidance": "GUIDANCE_UP", "approval": "APPROVAL", "approved": "APPROVAL",
    "fda": "REGULATORY", "contract": "CONTRACT", "selected": "CONTRACT", "award": "CONTRACT",
    "partnership": "PARTNERSHIP", "partner": "PARTNERSHIP", "deal": "DEAL", "agreement": "DEAL",
    "upgrade": "ANALYST_UPGRADE", "price target raised": "ANALYST_UPGRADE", "buyback": "BUYBACK",
    "launches": "PRODUCT", "launch": "PRODUCT", "record revenue": "EARNINGS_BEAT",
}
NEGATIVE_TERMS = {
    "misses": "EARNINGS_MISS", "missed estimates": "EARNINGS_MISS", "cuts guidance": "GUIDANCE_DOWN",
    "cut guidance": "GUIDANCE_DOWN", "downgrade": "ANALYST_DOWNGRADE", "offering": "DILUTION",
    "dilution": "DILUTION", "investigation": "INVESTIGATION", "lawsuit": "LITIGATION",
    "recall": "RECALL", "warning": "WARNING", "delay": "DELAY",
}


def _f(v, default=0.0):
    try:
        x = float(v)
        return default if math.isnan(x) else x
    except Exception:
        return default


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _market_window_open():
    now = datetime.now(ZoneInfo("America/New_York"))
    minutes = now.hour * 60 + now.minute
    return now.weekday() < 5 and 575 <= minutes <= 965


def _headline(item):
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    if title:
        return str(title)
    content = item.get("content")
    if isinstance(content, dict) and content.get("title"):
        return str(content["title"])
    return None


def classify_catalyst(items):
    titles, categories = [], []
    positive = negative = 0
    for item in items or []:
        title = _headline(item)
        if not title or title in titles:
            continue
        titles.append(title)
        low = title.lower()
        for term, category in POSITIVE_TERMS.items():
            if term in low:
                positive += 1
                categories.append(category)
                break
        for term, category in NEGATIVE_TERMS.items():
            if term in low:
                negative += 1
                categories.append(category)
                break
        if len(titles) >= 5:
            break
    score = min(5, positive * 2) - min(10, negative * 4)
    sentiment = "POSITIVE" if score > 0 else "NEGATIVE" if score < 0 else "NEUTRAL_OR_UNKNOWN"
    return {
        "score": int(max(-10, min(5, score))),
        "sentiment": sentiment,
        "categories": list(dict.fromkeys(categories))[:4],
        "headlines": titles[:3],
        "positive_hits": positive,
        "negative_hits": negative,
    }


def router_weights(regime_label):
    label = str(regime_label or "MIXED").upper()
    if label == "RISK_ON":
        return {"SESSION_LEADER": 1.07, "MOMENTUM_LEADER": 1.06, "BREAKOUT": 1.05, "VWAP_PULLBACK": 1.02, "SWING_CONTINUATION": 1.02}
    if label in ("RISK_OFF", "HIGH_VOLATILITY"):
        return {"SESSION_LEADER": 0.90, "MOMENTUM_LEADER": 0.88, "BREAKOUT": 0.88, "VWAP_PULLBACK": 0.96, "SWING_CONTINUATION": 0.94}
    return {"SESSION_LEADER": 1.00, "MOMENTUM_LEADER": 0.98, "BREAKOUT": 0.97, "VWAP_PULLBACK": 1.07, "SWING_CONTINUATION": 1.05}


def route_strategy(strategy_scores, regime_label):
    weights = router_weights(regime_label)
    adjusted = {k: max(0, min(100, round(_f(v) * weights.get(k, 1.0)))) for k, v in strategy_scores.items()}
    ranked = sorted(adjusted.items(), key=lambda x: (x[1], _f(strategy_scores.get(x[0]))), reverse=True)
    setup, routed = ranked[0]
    return {
        "setup": setup,
        "routed_score": int(routed),
        "raw_score": int(_f(strategy_scores.get(setup))),
        "weights": weights,
        "adjusted_scores": adjusted,
    }


def mtf_confirmation(setup, intraday_positive, hourly_positive, hourly_bull, daily_positive):
    setup = str(setup)
    checks = {
        "15m": bool(intraday_positive),
        "60m": bool(hourly_positive),
        "daily": bool(daily_positive),
    }
    if setup == "SWING_CONTINUATION":
        passed = hourly_positive and daily_positive
        strong = hourly_bull and daily_positive
    elif setup == "VWAP_PULLBACK":
        passed = intraday_positive and hourly_positive
        strong = intraday_positive and hourly_bull and daily_positive
    else:
        passed = intraday_positive and (hourly_positive or daily_positive)
        strong = intraday_positive and hourly_bull
    status = "PASS_STRONG" if strong else "PASS" if passed else "FAIL"
    adjustment = 6 if strong else 2 if passed else -10
    return {"status": status, "adjustment": adjustment, "checks": checks}


def _latest_valid(df):
    if df is None or df.empty:
        raise RuntimeError("empty frame")
    valid = df[(df["Close"].notna()) & (df["Volume"].fillna(0) >= 0)]
    return valid.iloc[-1] if not valid.empty else df.iloc[-1]


def build_metrics(symbol):
    daily = quote_daily(symbol)
    intr = quote_intraday(symbol, "5d", "15m")
    hourly = quote_intraday(symbol, "1mo", "60m")
    if len(daily) < 55 or len(intr) < 30 or len(hourly) < 25:
        raise RuntimeError("insufficient multi-timeframe data")

    di, ii, hi = add_daily(daily), add_intraday(intr), add_intraday(hourly)
    d, i, h = di.iloc[-1], _latest_valid(ii), _latest_valid(hi)
    price = _f(i["Close"])
    prev = base.previous_close(daily, i.name)
    atr_i = max(_f(i["ATR14"], price * 0.015), price * 0.002)
    atr_d = max(_f(d["ATR14"], price * 0.025), price * 0.005)
    vwap = _f(i["VWAP"], price)
    high20 = _f(d["HIGH20"], price)
    dates = pd.Series(base._ny_dates(intr.index), index=intr.index)
    session_date = base._ny_dates(pd.DatetimeIndex([i.name]))[0]
    day = intr[(dates == session_date) & (intr.index <= i.name)]
    day_high = _f(day["High"].max(), price)
    day_low = _f(day["Low"].min(), price)
    close_loc = (price - day_low) / max(day_high - day_low, 0.01)
    ret20 = (_f(daily["Close"].iloc[-1]) / _f(daily["Close"].iloc[-21], 1) - 1) * 100 if len(daily) >= 21 else 0.0
    ema20, ema50, ema200 = _f(d["EMA20"]), _f(d["EMA50"]), _f(d["EMA200"])
    h_close, h9, h21, h50 = _f(h["Close"]), _f(h["EMA9"]), _f(h["EMA21"]), _f(h["EMA50"])
    metrics = {
        "price": price,
        "day_change_pct": (price / prev - 1) * 100 if prev else 0.0,
        "rvol": base._session_rvol(intr, i.name),
        "bar_rvol": _f(i["VOL_RATIO"], 1.0),
        "daily_rvol": _f(d["VOL_RATIO"], 1.0),
        "rsi_i": _f(i["RSI14"], 50),
        "rsi_d": _f(d["RSI14"], 50),
        "ret1h": _f(i["RET_1H"]),
        "ret5d": _f(d["RET_5D"]),
        "ret20d": ret20,
        "above_vwap": price >= vwap,
        "vwap_extension_atr": (price - vwap) / atr_i,
        "ema_stack": _f(i["EMA9"]) > _f(i["EMA21"]) > _f(i["EMA50"]),
        "ema_fast": _f(i["EMA9"]) >= _f(i["EMA21"]),
        "daily_trend": price > ema20 > ema50 and (ema200 <= 0 or ema50 > ema200),
        "price_above_ema20": price > ema20,
        "above_20d_high": price > high20,
        "distance_20d_high_pct": (price / high20 - 1) * 100 if high20 else 0.0,
        "close_location": close_loc,
        "atr_i": atr_i,
        "atr_d": atr_d,
        "intraday_positive": price >= vwap and _f(i["EMA9"]) >= _f(i["EMA21"]),
        "hourly_positive": h_close > h21 and h9 >= h21,
        "hourly_bull": h_close > h9 > h21 > h50,
    }
    metrics["strategy_scores"] = v61._scores_with_session_leader(metrics)
    return metrics


def _threshold(setup, regime_label):
    base_thresholds = {"SESSION_LEADER": 78, "MOMENTUM_LEADER": 78, "BREAKOUT": 76, "VWAP_PULLBACK": 74, "SWING_CONTINUATION": 74}
    t = base_thresholds.get(setup, 76)
    if str(regime_label).upper() in ("RISK_OFF", "HIGH_VOLATILITY"):
        t += 5
    return t


def enrich_candidate(row, regime_label):
    symbol = row["symbol"]
    m = build_metrics(symbol)
    routed = route_strategy(m["strategy_scores"], regime_label)
    setup = routed["setup"]
    mtf = mtf_confirmation(setup, m["intraday_positive"], m["hourly_positive"], m["hourly_bull"], m["daily_trend"])
    try:
        catalyst = classify_catalyst(recent_news(symbol, 8))
    except Exception:
        catalyst = classify_catalyst([])
    final_score = max(0, min(100, routed["routed_score"] + mtf["adjustment"] + catalyst["score"]))
    extended = m["vwap_extension_atr"] > 2.5 or m["day_change_pct"] > 15
    negative_block = catalyst["score"] <= -6
    threshold = _threshold(setup, regime_label)
    eligible = row.get("sharia_status") == "PRECHECK_PASS" and mtf["status"] != "FAIL" and not extended and not negative_block
    status = "PAPER_ENTRY" if eligible and final_score >= threshold else "ARMED" if eligible and final_score >= threshold - 8 else "OBSERVE"

    swing = setup == "SWING_CONTINUATION"
    atr_ref = m["atr_d"] if swing else m["atr_i"]
    risk = max((1.50 * m["atr_d"] if swing else 1.25 * m["atr_i"]), m["price"] * (0.025 if swing else 0.012))
    risk = min(risk, m["price"] * (0.08 if swing else 0.05))
    entry = m["price"]
    zone = 0.15 * atr_ref if swing else 0.10 * atr_ref
    stop = entry - risk
    target1 = entry + (1.8 if swing else 1.6) * risk
    target2 = entry + (3.0 if swing else 2.5) * risk
    reason = [f"Router={setup} in {regime_label}", f"MTF={mtf['status']}"]
    if catalyst["sentiment"] != "NEUTRAL_OR_UNKNOWN":
        reason.append(f"Catalyst={catalyst['sentiment']}")
    return {
        **row,
        "engine": ENGINE,
        "setup": setup,
        "raw_strategy_scores": m["strategy_scores"],
        "router": routed,
        "mtf": mtf,
        "catalyst": catalyst,
        "score": int(final_score),
        "threshold": threshold,
        "status": status,
        "entry": round(entry, 2),
        "entry_low": round(entry - zone, 2),
        "entry_high": round(entry + zone, 2),
        "stop": round(stop, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "risk_pct": round(risk / entry * 100, 2),
        "atr_ref": round(atr_ref, 4),
        "day_change_pct": round(m["day_change_pct"], 2),
        "relative_volume": round(m["rvol"], 2),
        "ret1h_pct": round(m["ret1h"], 2),
        "daily_trend": bool(m["daily_trend"]),
        "extended": bool(extended),
        "decision_reason": reason,
    }


def _pick_diverse(rows, n=3):
    eligible = [x for x in rows if x.get("status") == "PAPER_ENTRY" and x.get("sharia_status") == "PRECHECK_PASS"]
    eligible.sort(key=lambda x: (x["score"], x["mtf"]["status"] == "PASS_STRONG", x["catalyst"]["score"], x["relative_volume"]), reverse=True)
    picked, groups = [], set()
    for x in eligible:
        if x.get("group") in groups:
            continue
        picked.append(x); groups.add(x.get("group"))
        if len(picked) >= n:
            return picked
    chosen = {x["symbol"] for x in picked}
    for x in eligible:
        if x["symbol"] in chosen:
            continue
        picked.append(x)
        if len(picked) >= n:
            break
    return picked


def paper_metrics(closed):
    rs = [_f(x.get("r")) for x in closed if x.get("r") is not None]
    if not rs:
        return {"samples": 0, "win_rate": 0.0, "expectancy_r": 0.0, "profit_factor": 0.0, "total_r": 0.0}
    wins = [x for x in rs if x > 0]; losses = [-x for x in rs if x < 0]
    pf = sum(wins) / sum(losses) if losses else (99.0 if wins else 0.0)
    return {"samples": len(rs), "win_rate": round(len(wins) / len(rs) * 100, 1), "expectancy_r": round(sum(rs) / len(rs), 3), "profit_factor": round(pf, 2), "total_r": round(sum(rs), 2)}


def _close_trade(pos, exit_price, reason, when):
    risk = max(_f(pos.get("entry")) - _f(pos.get("stop")), 0.01)
    r = (exit_price - _f(pos.get("entry"))) / risk
    return {**pos, "status": "CLOSED", "exit": round(exit_price, 4), "exit_reason": reason, "closed_at": str(when), "r": round(r, 3)}


def update_paper_state(state):
    pending, opened, closed, rejected = [], [], list(state.get("closed", [])), list(state.get("rejected", []))
    events = []
    for pos in state.get("pending", []):
        try:
            intr = quote_intraday(pos["symbol"], "5d", "15m")
            signal_at = pd.Timestamp(pos["signal_at"])
            if signal_at.tzinfo is None:
                signal_at = signal_at.tz_localize("UTC")
            bars = intr[intr.index > signal_at]
            if bars.empty:
                pending.append(pos); continue
            bar = bars.iloc[0]; entry = _f(bar["Open"]) * (1 + SLIPPAGE_BPS / 10000)
            if abs(entry - _f(pos["signal_entry"])) > 0.75 * max(_f(pos["atr_ref"]), 0.01):
                rejected.append({**pos, "status": "REJECTED", "reason": "ENTRY_GAP", "rejected_at": str(bar.name)}); continue
            risk = max(_f(pos["signal_entry"]) - _f(pos["signal_stop"]), 0.01)
            target_r = 1.8 if pos.get("setup") == "SWING_CONTINUATION" else 1.6
            active = {**pos, "status": "OPEN", "entry": round(entry, 4), "stop": round(entry - risk, 4), "target1": round(entry + target_r * risk, 4), "opened_at": str(bar.name), "last_processed": str(bar.name), "bars_held": 0}
            opened.append(active); events.append({"type": "OPEN", "symbol": pos["symbol"], "entry": active["entry"]})
        except Exception:
            pending.append(pos)

    for pos in state.get("open", []) + opened:
        try:
            intr = quote_intraday(pos["symbol"], "5d", "15m")
            last = pd.Timestamp(pos["last_processed"])
            if last.tzinfo is None:
                last = last.tz_localize("UTC")
            bars = intr[intr.index > last]
            done = None
            for idx, bar in bars.iterrows():
                pos["bars_held"] = int(pos.get("bars_held", 0)) + 1
                lo, hi = _f(bar["Low"]), _f(bar["High"])
                if lo <= _f(pos["stop"]):
                    done = _close_trade(pos, _f(pos["stop"]), "STOP", idx); break
                if hi >= _f(pos["target1"]):
                    done = _close_trade(pos, _f(pos["target1"]), "TARGET1", idx); break
                max_bars = 130 if pos.get("setup") == "SWING_CONTINUATION" else 26
                if pos["bars_held"] >= max_bars:
                    done = _close_trade(pos, _f(bar["Close"]), "TIME_EXIT", idx); break
                pos["last_processed"] = str(idx)
            if done:
                closed.append(done); events.append({"type": "CLOSE", "symbol": done["symbol"], "r": done["r"], "reason": done["exit_reason"]})
            else:
                if not bars.empty:
                    pos["last_processed"] = str(bars.index[-1])
                opened.append(pos) if pos not in opened else None
        except Exception:
            if pos not in opened:
                opened.append(pos)

    # De-duplicate open positions by symbol, keeping the latest record.
    dedup_open = {x["symbol"]: x for x in opened}
    state.update({"pending": pending, "open": list(dedup_open.values()), "closed": closed[-500:], "rejected": rejected[-500:]})
    state["metrics"] = paper_metrics(state["closed"])
    return state, events


def add_new_picks_to_paper(state, picks):
    active = {x["symbol"] for x in state.get("pending", []) + state.get("open", [])}
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    existing_today = sum(1 for x in state.get("pending", []) + state.get("open", []) + state.get("closed", []) if str(x.get("signal_day")) == today)
    for x in picks:
        if x["symbol"] in active or existing_today >= 3:
            continue
        state.setdefault("pending", []).append({
            "symbol": x["symbol"], "setup": x["setup"], "signal_at": now, "signal_day": today,
            "signal_entry": x["entry"], "signal_stop": x["stop"], "signal_target1": x["target1"],
            "atr_ref": x["atr_ref"], "score": x["score"], "mtf": x["mtf"]["status"],
            "catalyst": x["catalyst"]["sentiment"], "status": "PENDING_NEXT_BAR",
        })
        active.add(x["symbol"]); existing_today += 1
    return state


def _signature(picks):
    return "|".join(f"{x['symbol']}:{x['setup']}:{x['score']}" for x in picks)


def telegram_message(snapshot, closed_events):
    picks = snapshot.get("paper_picks", [])
    regime = snapshot.get("market_regime", "UNKNOWN")
    lines = [f"🧪 <b>{ENGINE}</b>", f"السوق: <b>{regime}</b> | Paper فقط — لا تنفيذ حقيقي"]
    for i, x in enumerate(picks, 1):
        catalyst = x["catalyst"]
        cat = catalyst["sentiment"] if catalyst["sentiment"] != "NEUTRAL_OR_UNKNOWN" else "no clear catalyst"
        lines += ["", f"{i}) <b>{x['symbol']}</b> — {x['setup']} — {x['score']}/100",
                  f"دخول تجريبي ${x['entry_low']:.2f}–${x['entry_high']:.2f} | Stop ${x['stop']:.2f}",
                  f"T1 ${x['target1']:.2f} | T2 ${x['target2']:.2f} | MTF {x['mtf']['status']} | {cat}"]
    for e in closed_events:
        if e.get("type") == "CLOSE":
            icon = "✅" if _f(e.get("r")) > 0 else "❌"
            lines += ["", f"{icon} Paper result: <b>{e['symbol']}</b> {e['r']:+.2f}R — {e['reason']}"]
    m = snapshot.get("paper_metrics", {})
    lines += ["", f"السجل: {m.get('samples',0)} مغلقة | Win {m.get('win_rate',0)}% | PF {m.get('profit_factor',0)} | Total {m.get('total_r',0):+.2f}R",
              "🕌 الفلتر الشرعي ما زال Conservative pre-check وليس اعتمادًا شرعيًا نهائيًا."]
    return "\n".join(lines)


def main():
    event_name = os.getenv("GITHUB_EVENT_NAME", "local")
    if event_name == "schedule" and not _market_window_open():
        print("V7 scheduled scan outside US session; skipped.")
        return 0

    # Stage 1: preserve V6.1 broad discovery, but suppress its Telegram output.
    base._strategy_scores = v61._scores_with_session_leader
    base.telegram_enabled = lambda: False
    rc = base.main()
    broad = _load(base.OUT, {})
    ranked = list(broad.get("ranked", []))

    try:
        regime = detect_regime()
        regime_label = regime.label
        regime_detail = {"label": regime.label, "vix": regime.vix, "spy": regime.spy_trend, "qqq": regime.qqq_trend}
    except Exception as exc:
        regime_label = "MIXED"
        regime_detail = {"label": "MIXED", "error": str(exc)}

    # Stage 2: expensive confirmation only for the best already-known Sharia-prechecked names.
    pool_candidates = [x for x in ranked if x.get("sharia_status") == "PRECHECK_PASS"][:MAX_ENRICH]
    enriched, errors = [], []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(enrich_candidate, x, regime_label): x["symbol"] for x in pool_candidates}
        for fut in as_completed(futures):
            try:
                enriched.append(fut.result())
            except Exception as exc:
                errors.append(f"{futures[fut]}: {exc}")
    enriched.sort(key=lambda x: (x["status"] == "PAPER_ENTRY", x["score"], x["relative_volume"]), reverse=True)
    picks = _pick_diverse(enriched, 3)

    state = _load(PAPER_STATE, {"engine": ENGINE, "mode": MODE, "pending": [], "open": [], "closed": [], "rejected": []})
    state, paper_events = update_paper_state(state)
    state = add_new_picks_to_paper(state, picks)
    state["engine"] = ENGINE; state["mode"] = MODE; state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(PAPER_STATE, state)

    snapshot = {
        "engine": ENGINE, "mode": MODE, "updated_at": datetime.now(timezone.utc).isoformat(),
        "market_regime": regime_label, "market_regime_detail": regime_detail,
        "broad_scanned": broad.get("scanned", 0), "enhanced_candidates": len(enriched), "enhancement_errors": errors[:20],
        "paper_picks": picks, "ranked_enhanced": enriched,
        "paper_metrics": state.get("metrics", paper_metrics(state.get("closed", []))),
        "paper_state_counts": {"pending": len(state.get("pending", [])), "open": len(state.get("open", [])), "closed": len(state.get("closed", [])), "rejected": len(state.get("rejected", []))},
        "design": {
            "stage1": "V6.1 broad dynamic discovery",
            "stage2": "top candidates only: 15m + 60m + daily confirmation + catalyst context",
            "strategy_router": "regime-dependent family weights; each family keeps its own threshold",
            "catalyst": "headline classification capped at +5/-10; strong negative catalyst blocks paper entry",
            "mtf": "15m/60m/daily confirmation required before PAPER_ENTRY",
            "paper_execution": f"next 15m bar open + {SLIPPAGE_BPS}bps slippage; conservative stop-first same-bar rule",
            "live_execution": False,
        },
        "sharia": broad.get("sharia", {}),
    }
    _save(SNAPSHOT, snapshot)

    alert = _load(ALERT_STATE, {})
    sig = _signature(picks)
    changed = sig != alert.get("signature")
    closed_events = [e for e in paper_events if e.get("type") == "CLOSE"]
    should_notify = event_name != "schedule" or changed or bool(closed_events)
    if should_notify and telegram_enabled():
        try:
            send(telegram_message(snapshot, closed_events))
            alert["sent_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            print("V7 Telegram warning:", exc)
    alert.update({"signature": sig, "updated_at": datetime.now(timezone.utc).isoformat(), "picks": [{"symbol": x["symbol"], "setup": x["setup"], "score": x["score"]} for x in picks]})
    _save(ALERT_STATE, alert)

    print(json.dumps({
        "engine": ENGINE, "regime": regime_label, "broad_scanned": snapshot["broad_scanned"],
        "enhanced": len(enriched), "paper_picks": alert["picks"], "paper": snapshot["paper_metrics"],
        "errors": errors[:5],
    }, indent=2, ensure_ascii=False))
    return rc if enriched else 2


if __name__ == "__main__":
    raise SystemExit(main())
