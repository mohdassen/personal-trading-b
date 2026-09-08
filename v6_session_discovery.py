"""V6.1 session-leader overlay for V6 discovery.

Research/paper only. Adds a session-momentum family which can surface current
leaders even when long-horizon trend filters are not yet aligned. It also saves
session movers and best-effort news context for QA. No live orders.
"""
from __future__ import annotations

import json
from pathlib import Path

import v6_discovery as base
from src.trading_bot.market import recent_news
from src.trading_bot.telegram import enabled as real_telegram_enabled, send

ROOT = Path(__file__).resolve().parent

_original_scores = base._strategy_scores


def _scores_with_session_leader(m):
    scores = _original_scores(m)
    sc = 0
    change = float(m["day_change_pct"])
    rvol = float(m["rvol"])
    ret1h = float(m["ret1h"])
    rsi = float(m["rsi_i"])
    close_loc = float(m["close_location"])
    extension = float(m["vwap_extension_atr"])

    sc += 30 if 4 <= change <= 12 else 22 if 2 <= change < 4 else 12 if 1 <= change < 2 else 0
    sc += 25 if rvol >= 2.0 else 18 if rvol >= 1.4 else 10 if rvol >= 1.1 else 0
    sc += 15 if m["above_vwap"] else 0
    sc += 10 if close_loc >= 0.65 else 5 if close_loc >= 0.50 else 0
    sc += 10 if ret1h >= 0.50 else 6 if ret1h > 0 else 0
    sc += 5 if m["ema_fast"] else 0
    sc += 5 if 50 <= rsi <= 75 else 0
    if extension > 2.5:
        sc -= 12
    if change > 15:
        sc -= 15
    scores["SESSION_LEADER"] = max(0, min(100, sc))
    return scores


def _headline(item):
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    if title:
        return str(title)
    content = item.get("content")
    if isinstance(content, dict):
        title = content.get("title")
        if title:
            return str(title)
    return None


def main():
    base._strategy_scores = _scores_with_session_leader
    # Avoid duplicate Telegram from base; send one enriched V6.1 report below.
    base.telegram_enabled = lambda: False
    rc = base.main()
    if not base.OUT.exists():
        return rc

    out = json.loads(base.OUT.read_text(encoding="utf-8"))
    ranked = list(out.get("ranked", []))
    movers = sorted(ranked, key=lambda x: (float(x.get("day_change_pct", 0)), float(x.get("relative_volume", 0))), reverse=True)[:25]
    family_leaders = {}
    families = ["SESSION_LEADER", "MOMENTUM_LEADER", "BREAKOUT", "VWAP_PULLBACK", "SWING_CONTINUATION"]
    for family in families:
        family_leaders[family] = [x for x in ranked if x.get("setup") == family][:5]

    # News is context only for now, not a score input. This avoids optimizing on
    # unreliable headline availability while still exposing catalyst blindness.
    catalyst_context = []
    for x in movers:
        if x.get("sharia_status") != "PRECHECK_PASS" or float(x.get("day_change_pct", 0)) < 2:
            continue
        headlines = []
        try:
            for item in recent_news(x["symbol"], 5):
                h = _headline(item)
                if h and h not in headlines:
                    headlines.append(h)
                if len(headlines) >= 3:
                    break
        except Exception:
            pass
        catalyst_context.append({
            "symbol": x["symbol"],
            "day_change_pct": x.get("day_change_pct"),
            "relative_volume": x.get("relative_volume"),
            "headlines": headlines,
        })
        if len(catalyst_context) >= 10:
            break

    out["engine"] = "V6.1-Session-Multi-Strategy-Discovery"
    out["session_movers"] = movers
    out["family_leaders"] = family_leaders
    out["catalyst_context"] = catalyst_context
    out["design"]["strategy_families"] = families
    out["design"]["session_leader_rule"] = "day move + cumulative same-time RVOL + VWAP + close location + 1h momentum; independent of long-horizon trend"
    out["design"]["catalyst_news"] = "context-only until forward-calibrated; does not currently add score"
    base.OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({
        "engine": out["engine"],
        "scanned": out.get("scanned"),
        "paper_picks": [{k: x.get(k) for k in ("symbol", "setup", "score", "day_change_pct", "relative_volume", "entry", "stop", "target1")} for x in out.get("paper_picks", [])],
        "session_movers": [{k: x.get(k) for k in ("symbol", "setup", "score", "day_change_pct", "relative_volume", "sharia_status")} for x in movers[:10]],
        "catalyst_context": catalyst_context,
    }, indent=2, ensure_ascii=False))

    if real_telegram_enabled():
        try:
            msg = base.telegram_message(out).replace("V6 DISCOVERY", "V6.1 DISCOVERY")
            send(msg)
        except Exception as exc:
            print(f"V6.1 Telegram warning: {exc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
