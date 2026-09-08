"""V6 multi-strategy discovery engine.

Research/paper only. It does not place orders and does not modify V5.1.
The goal is to discover session leaders first, then rank several setup families
instead of forcing every stock through one all-or-nothing rule set.
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yaml

from src.trading_bot.indicators import add_daily, add_intraday
from src.trading_bot.market import quote_daily, quote_intraday
from src.trading_bot.telegram import enabled as telegram_enabled, send

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data/v6_discovery.json"
ENGINE = "V6-Multi-Strategy-Discovery"
MODE = "RESEARCH_PAPER_ONLY"


def load_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _f(v, default=0.0):
    try:
        x = float(v)
        return default if math.isnan(x) else x
    except Exception:
        return default


def group_for(symbol, groups):
    for name, symbols in groups.items():
        if symbol in symbols:
            return name
    return "DYNAMIC_OTHER"


def sharia_precheck(symbol, group, sector=""):
    excluded_groups = set((load_yaml(ROOT / "config/sharia_shadow.yml").get("policy") or {}).get("excluded_groups", []))
    s = str(sector or "").lower()
    if group in excluded_groups or "financial" in s or "bank" in s or "insurance" in s or "credit" in s:
        return "PRECHECK_FAIL"
    return "PRECHECK_PASS"


def yahoo_session_leaders(limit=45):
    """Best-effort free discovery. Failure never blocks the seeded universe."""
    url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
    headers = {"User-Agent": "Mozilla/5.0 PersonalTradingAssistant"}
    found = {}
    for screen in ("day_gainers", "most_actives"):
        try:
            r = requests.get(url, params={"count": limit, "scrIds": screen}, headers=headers, timeout=15)
            r.raise_for_status()
            result = ((r.json().get("finance") or {}).get("result") or [])
            quotes = (result[0].get("quotes") or []) if result else []
            for q in quotes:
                symbol = str(q.get("symbol") or "").upper().strip()
                if not symbol or q.get("quoteType") not in (None, "EQUITY"):
                    continue
                price = _f(q.get("regularMarketPrice"))
                volume = _f(q.get("regularMarketVolume"))
                cap = _f(q.get("marketCap"))
                if price < 5 or volume < 1_000_000 or (cap and cap < 500_000_000):
                    continue
                found[symbol] = {
                    "symbol": symbol,
                    "source": screen,
                    "sector": str(q.get("sector") or ""),
                    "screen_change_pct": _f(q.get("regularMarketChangePercent")),
                    "screen_volume": int(volume),
                }
        except Exception as exc:
            print(f"V6 screener warning {screen}: {exc}")
    return list(found.values())


def previous_close(daily, intraday):
    if len(daily) < 2:
        return _f(daily["Close"].iloc[-1])
    session_date = pd.Timestamp(intraday.index[-1]).tz_convert("America/New_York").date()
    last = pd.Timestamp(daily.index[-1])
    if last.tzinfo is not None:
        last_date = last.tz_convert("America/New_York").date()
    else:
        last_date = last.date()
    return _f(daily["Close"].iloc[-2] if last_date == session_date else daily["Close"].iloc[-1])


def _strategy_scores(m):
    scores = {}

    sc = 0
    sc += 20 if m["day_change_pct"] >= 2 else 10 if m["day_change_pct"] >= 1 else 0
    sc += 20 if m["rvol"] >= 2 else 12 if m["rvol"] >= 1.3 else 0
    sc += 15 if m["above_vwap"] else 0
    sc += 15 if m["ema_stack"] else 8 if m["ema_fast"] else 0
    sc += 10 if m["ret1h"] >= 0.5 else 5 if m["ret1h"] > 0 else 0
    sc += 10 if 52 <= m["rsi_i"] <= 72 else 4 if 47 <= m["rsi_i"] < 52 else 0
    sc += 10 if m["daily_trend"] else 0
    if m["vwap_extension_atr"] > 2.0:
        sc -= 15
    if m["day_change_pct"] > 12:
        sc -= 10
    scores["MOMENTUM_LEADER"] = max(0, min(100, sc))

    sc = 0
    sc += 22 if m["above_20d_high"] else 12 if m["distance_20d_high_pct"] >= -1.0 else 0
    sc += 20 if m["rvol"] >= 1.8 else 12 if m["rvol"] >= 1.2 else 0
    sc += 15 if m["daily_trend"] else 0
    sc += 10 if m["ret1h"] > 0 else 0
    sc += 10 if m["close_location"] >= 0.70 else 5 if m["close_location"] >= 0.55 else 0
    sc += 10 if m["day_change_pct"] >= 1 else 0
    sc += 10 if 50 <= m["rsi_i"] <= 72 else 0
    scores["BREAKOUT"] = max(0, min(100, sc))

    sc = 0
    sc += 22 if m["daily_trend"] else 0
    sc += 15 if 0.5 <= m["day_change_pct"] <= 6 else 5 if m["day_change_pct"] > 0 else 0
    sc += 20 if m["above_vwap"] and 0 <= m["vwap_extension_atr"] <= 0.55 else 8 if m["above_vwap"] else 0
    sc += 12 if m["ema_fast"] else 0
    sc += 12 if 45 <= m["rsi_i"] <= 65 else 0
    sc += 10 if m["rvol"] >= 1.1 else 0
    sc += 9 if -0.5 <= m["ret1h"] <= 1.0 else 0
    scores["VWAP_PULLBACK"] = max(0, min(100, sc))

    sc = 0
    sc += 25 if m["daily_trend"] else 8 if m["price_above_ema20"] else 0
    sc += 20 if m["ret20d"] >= 8 else 12 if m["ret20d"] >= 4 else 0
    sc += 15 if 0 < m["ret5d"] <= 8 else 6 if m["ret5d"] > 0 else 0
    sc += 15 if m["distance_20d_high_pct"] >= -3 else 7 if m["distance_20d_high_pct"] >= -6 else 0
    sc += 10 if 50 <= m["rsi_d"] <= 68 else 0
    sc += 10 if m["daily_rvol"] >= 1.2 else 4 if m["daily_rvol"] >= 1.0 else 0
    sc += 5 if m["day_change_pct"] >= 0 else 0
    scores["SWING_CONTINUATION"] = max(0, min(100, sc))
    return scores


def scan(symbol, groups, dynamic_meta):
    daily = quote_daily(symbol)
    intr = quote_intraday(symbol, "5d", "15m")
    if len(daily) < 55 or len(intr) < 30:
        return None
    di = add_daily(daily)
    ii = add_intraday(intr)
    d = di.iloc[-1]
    i = ii.iloc[-1]
    price = _f(i["Close"])
    prev = previous_close(daily, intr)
    atr_i = max(_f(i["ATR14"], price * 0.015), price * 0.002)
    atr_d = max(_f(d["ATR14"], price * 0.025), price * 0.005)
    vwap = _f(i["VWAP"], price)
    high20 = _f(d["HIGH20"], price)
    day = intr.copy()
    dates = day.index.tz_convert("America/New_York").date
    day = day[pd.Series(dates, index=day.index) == dates[-1]]
    day_high = _f(day["High"].max(), price)
    day_low = _f(day["Low"].min(), price)
    close_loc = (price - day_low) / max(day_high - day_low, 0.01)
    ret20 = (_f(daily["Close"].iloc[-1]) / _f(daily["Close"].iloc[-21], 1) - 1) * 100 if len(daily) >= 21 else 0.0
    ema20, ema50, ema200 = _f(d["EMA20"]), _f(d["EMA50"]), _f(d["EMA200"])
    group = group_for(symbol, groups)
    meta = dynamic_meta.get(symbol, {})
    sharia = sharia_precheck(symbol, group, meta.get("sector", ""))
    metrics = {
        "price": price,
        "prev_close": prev,
        "day_change_pct": (price / prev - 1) * 100 if prev else 0.0,
        "rvol": _f(i["VOL_RATIO"], 1.0),
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
    }
    scores = _strategy_scores(metrics)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    setup, score = ranked[0]
    runner_up, runner_score = ranked[1]
    swing = setup == "SWING_CONTINUATION"
    risk = max((1.50 * atr_d if swing else 1.25 * atr_i), price * (0.025 if swing else 0.012))
    risk = min(risk, price * (0.08 if swing else 0.05))
    stop = price - risk
    t1 = price + (1.8 if swing else 1.6) * risk
    t2 = price + (3.0 if swing else 2.5) * risk
    extended = metrics["vwap_extension_atr"] > 2.0 or metrics["day_change_pct"] > 15
    status = "PAPER_ENTRY" if score >= 72 and not extended else "ARMED" if score >= 64 else "OBSERVE"
    return {
        "symbol": symbol,
        "group": group,
        "source": meta.get("source", "seed_universe"),
        "sector": meta.get("sector", ""),
        "sharia_status": sharia,
        "setup": setup,
        "score": int(score),
        "runner_up_setup": runner_up,
        "runner_up_score": int(runner_score),
        "status": status,
        "entry": round(price, 2),
        "stop": round(stop, 2),
        "target1": round(t1, 2),
        "target2": round(t2, 2),
        "risk_pct": round(risk / price * 100, 2),
        "day_change_pct": round(metrics["day_change_pct"], 2),
        "relative_volume": round(metrics["rvol"], 2),
        "ret1h_pct": round(metrics["ret1h"], 2),
        "ret5d_pct": round(metrics["ret5d"], 2),
        "ret20d_pct": round(metrics["ret20d"], 2),
        "distance_20d_high_pct": round(metrics["distance_20d_high_pct"], 2),
        "above_vwap": metrics["above_vwap"],
        "daily_trend": metrics["daily_trend"],
        "extended": extended,
    }


def _pick_diverse(rows, n=3):
    picked, groups = [], set()
    for x in rows:
        if x["status"] != "PAPER_ENTRY" or x["sharia_status"] != "PRECHECK_PASS":
            continue
        if x["group"] in groups and len(picked) < 2:
            continue
        picked.append(x)
        groups.add(x["group"])
        if len(picked) >= n:
            break
    return picked


def telegram_message(out):
    picks = out["paper_picks"]
    lines = [
        "🧪 <b>V6 DISCOVERY — Paper only</b>",
        f"الممسوحة: <b>{out['scanned']}</b> | صفقات Paper: <b>{len(picks)}</b>",
        "تعلم واختبار فقط — ليست أوامر شراء حقيقية.",
    ]
    for idx, x in enumerate(picks, 1):
        lines += [
            "",
            f"{idx}) <b>{x['symbol']}</b> — {x['setup']} — {x['score']}/100",
            f"اليوم {x['day_change_pct']:+.2f}% | RVOL {x['relative_volume']:.2f}x | 1h {x['ret1h_pct']:+.2f}%",
            f"Entry ${x['entry']:.2f} | Stop ${x['stop']:.2f} | T1 ${x['target1']:.2f} | T2 ${x['target2']:.2f}",
        ]
    if not picks:
        armed = [x for x in out["ranked"] if x["status"] == "ARMED" and x["sharia_status"] == "PRECHECK_PASS"][:3]
        if armed:
            lines += ["", "👀 الأقرب للتفعيل: " + ", ".join(f"{x['symbol']}({x['score']})" for x in armed)]
    lines += ["", "🕌 الفلتر الشرعي الحالي Pre-check محافظ فقط وليس اعتمادًا شرعيًا رسميًا."]
    return "\n".join(lines)


def main():
    cfg = load_yaml(ROOT / "config/universe.yml")
    groups = (load_yaml(ROOT / "config/groups.yml").get("groups") or {})
    seed = list(dict.fromkeys(cfg.get("universe", [])))
    leaders = yahoo_session_leaders()
    dynamic_meta = {x["symbol"]: x for x in leaders}
    # Scan all seeded names plus a bounded set of liquid session leaders. Unknown
    # dynamic names remain subject to the same conservative sector pre-check.
    universe = list(dict.fromkeys(seed + [x["symbol"] for x in leaders]))[:150]
    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(scan, s, groups, dynamic_meta): s for s in universe}
        for fut in as_completed(futs):
            try:
                x = fut.result()
                if x:
                    rows.append(x)
            except Exception as exc:
                errors.append(f"{futs[fut]}: {exc}")
    rows.sort(key=lambda x: (x["status"] == "PAPER_ENTRY", x["score"], x["relative_volume"], x["day_change_pct"]), reverse=True)
    picks = _pick_diverse(rows, 3)
    out = {
        "engine": ENGINE,
        "mode": MODE,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "seed_universe": len(seed),
        "session_leaders_discovered": len(leaders),
        "universe_considered": len(universe),
        "scanned": len(rows),
        "errors": errors[:20],
        "paper_picks": picks,
        "ranked": rows[:20],
        "design": {
            "strategy_families": ["MOMENTUM_LEADER", "BREAKOUT", "VWAP_PULLBACK", "SWING_CONTINUATION"],
            "selection": "rank first, then confirm; not one universal hard gate",
            "dynamic_discovery": "Yahoo day_gainers + most_actives best-effort, free; seed universe fallback",
            "live_execution": False,
        },
        "sharia": {
            "status": "CONSERVATIVE_PRECHECK_ONLY",
            "certified": False,
            "note": "Excludes known conventional financial groups/sectors; full AAOIFI ratio screening remains required before real-money use.",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if telegram_enabled():
        try:
            send(telegram_message(out))
        except Exception as exc:
            print(f"V6 Telegram warning: {exc}")
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
