"""Fast paper-learning track beside frozen V5.1.

Purpose: collect real forward evidence faster without changing or promoting the
validated V5.1 strategy. PAPER ONLY. Never a live-trading signal.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone

import backtest_portfolio_momentum as pm
import v49_shadow as base
import v51_active_challenger as v51

ENGINE = "V5.1-Fast-Paper-Track"
MODE = "PAPER_ONLY_ACCELERATED_LEARNING"
STATE_PATH = base.ROOT / "data/v51_fast_paper.json"
SNAPSHOT_PATH = base.ROOT / "data/v51_fast_paper_snapshot.json"
MAX_NEW_PER_SESSION = 3
MAX_ACTIVE = 6
MIN_SCORE = 45


def _telegram(msg: str):
    if not base.telegram_enabled():
        return
    try:
        base.send(msg)
    except Exception as e:
        print(f"FAST_PAPER_TELEGRAM_ERROR: {e}")


def _init_state():
    return {
        "engine": ENGINE,
        "mode": MODE,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pending": [],
        "open": [],
        "closed": [],
        "rejected": [],
        "sessions": [],
        "sharia": {
            "status": "CONSERVATIVE_PRECHECK_ONLY",
            "certified": False,
            "note": "Sector/symbol pre-check only; not formal Sharia certification.",
        },
    }


def _paper_candidates(rows):
    groups = pm._group_map()
    sh = base._sharia_policy()
    out = []
    for r in rows:
        symbol = str(r["symbol"])
        group = groups.get(symbol, f"UNGROUPED:{symbol}")
        if group in sh["excluded_groups"] or symbol in sh["excluded_symbols"]:
            continue

        close = max(float(r["close"]), 0.01)
        atr = max(float(r["atr"]), 0.01)
        risk_pct = 2.5 * atr / close * 100.0

        # Hard safety gates stay intact. We relax only V5.1 qualification depth
        # to generate learning samples; this track can never authorize live use.
        hard_ok = bool(
            r.get("regime_ok")
            and close > float(r["sma200"])
            and float(r["sma50"]) > float(r["sma200"])
            and float(r["adv20"]) >= base.em.MIN_DOLLAR_VOLUME
            and 1.2 <= risk_pct <= pm.MAX_TRADE_RISK_PCT
            and float(r.get("spy_vol20", 9.0)) <= pm.MAX_SPY_VOL20_FOR_NEW_RISK
        )
        if not hard_ok:
            continue

        score = int(r.get("score", 0))
        if score < MIN_SCORE:
            continue

        quality = (
            score
            + 8.0 * float(r.get("mom12_rank", 0.0))
            + 5.0 * float(r.get("mom6_rank", 0.0))
            + 3.0 * float(r.get("leadership_breadth50", 0.0))
            - 8.0 * max(0.0, float(r.get("vol20", 0.0)) - 0.35)
        )
        out.append({
            "symbol": symbol,
            "group": group,
            "strategy": "FAST_PAPER",
            "setup_type": "V51_NEAR_QUALIFIED",
            "signal_date": r["date"],
            "timestamp": r["timestamp"],
            "score": score,
            "quality": round(quality, 4),
            "signal_close": round(close, 4),
            "atr": round(atr, 4),
            "estimated_risk_pct": round(risk_pct, 3),
            "mom12_1": round(float(r.get("mom12_1", 0.0)), 4),
            "mom6_1": round(float(r.get("mom6_1", 0.0)), 4),
            "mom12_rank": round(float(r.get("mom12_rank", 0.0)), 3),
            "mom6_rank": round(float(r.get("mom6_rank", 0.0)), 3),
            "spy_vol20": round(float(r.get("spy_vol20", 0.0)), 4),
            "full_v51_qualified": bool(r.get("base_ok") and score >= 60),
            "sharia_status": "PRECHECK_PASS",
        })
    return sorted(out, key=lambda x: (x["quality"], x["score"], x["mom12_rank"]), reverse=True)


def _select(candidates, state):
    active = list(state.get("pending", [])) + list(state.get("open", []))
    used_symbols = {x.get("symbol") for x in active}
    used_groups = Counter(x.get("group") for x in active)
    capacity = max(0, MAX_ACTIVE - len(active))
    limit = min(MAX_NEW_PER_SESSION, capacity)
    chosen = []
    for x in candidates:
        if len(chosen) >= limit:
            break
        if x["symbol"] in used_symbols or used_groups[x["group"]] >= 1:
            continue
        chosen.append(x)
        used_symbols.add(x["symbol"])
        used_groups[x["group"]] += 1
    return chosen


def _snapshot(state, completed=None):
    metrics = base._metrics(state.get("closed", []))
    data = {
        "engine": ENGINE,
        "mode": MODE,
        "completed_through": str(completed.date()) if completed is not None else state.get("completed_through"),
        "pending": len(state.get("pending", [])),
        "open": len(state.get("open", [])),
        "closed": len(state.get("closed", [])),
        "rejected": len(state.get("rejected", [])),
        "metrics": metrics,
        "sessions": state.get("sessions", [])[-20:],
        "sharia": state["sharia"],
        "note": "Accelerated paper learning only. Does not modify/fail/promote frozen V5.1.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if completed is not None:
        state["completed_through"] = str(completed.date())
    base.save_json(STATE_PATH, state)
    base.save_json(SNAPSHOT_PATH, data)
    return data


def main():
    clock_ny = base.now_ny()
    state = base.load_json(STATE_PATH, {}) or _init_state()

    bootstrap = os.getenv("FAST_PAPER_BOOTSTRAP", "0").lower() in ("1", "true", "yes")
    if not base._is_after_close(clock_ny) and not bootstrap:
        print("Fast paper: outside completed-session window; no action")
        _snapshot(state)
        return 0

    universe = list(base.load_yaml(base.ROOT / "config/universe.yml").get("universe", []))
    prices = base._download_prices(["SPY"] + universe, clock_ny)
    spy = prices["SPY"]
    completed = spy.index[-1]
    for s in universe:
        if completed not in prices[s].index:
            raise RuntimeError(f"Fast paper alignment failure: {s}")

    # First advance existing paper positions using the same conservative mechanics
    # as the frozen shadow track.
    events = []
    events += base._activate_pending(state, prices, completed)
    events += base._update_open(state, prices, completed)

    sf = base.em._spy_regime(spy)
    raw = []
    for s in universe:
        r = base._row_at(s, prices[s], sf, completed)
        if r is not None:
            raw.append(r)
    enriched = base.am._enrich_leadership(raw, prices)
    rows = v51.qualify(enriched)
    candidates = _paper_candidates(rows)

    # One decision per completed session; no duplicate paper entries.
    session_key = str(completed.date())
    already = any(x.get("session") == session_key for x in state.get("sessions", []))
    selected = [] if already else _select(candidates, state)
    if selected:
        state.setdefault("pending", []).extend(selected)

    if not already:
        state.setdefault("sessions", []).append({
            "session": session_key,
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "selected": [
                {"symbol": x["symbol"], "score": x["score"], "quality": x["quality"], "full_v51_qualified": x["full_v51_qualified"]}
                for x in selected
            ],
        })
        state["sessions"] = state["sessions"][-60:]

    # Telegram: one compact summary per new session, plus realized results.
    if not already:
        if selected:
            lines = ["🧪 <b>FAST PAPER TRACK — LEARNING ONLY</b>", "هذه صفقات افتراضية لتسريع التعلم، وليست توصيات شراء.", ""]
            for i, x in enumerate(selected, 1):
                lines.append(f"{i}) <b>{x['symbol']}</b> — Score {x['score']} — Close ${x['signal_close']:.2f}")
            lines += ["", "سيتم تسجيل Entry افتراضي على افتتاح الجلسة التالية إذا اجتاز Gap/Risk.", "🕌 Sharia pre-check محافظ فقط؛ ليس اعتمادًا شرعيًا رسميًا."]
            _telegram("\n".join(lines))
        else:
            _telegram("🧪 <b>FAST PAPER TRACK</b>\nلا توجد حتى فرصة Paper آمنة اليوم بعد بوابات المخاطر الأساسية.")

    for kind, x in events:
        if kind == "close":
            _telegram(f"📊 <b>FAST PAPER RESULT — {x['symbol']}</b>\n{x['outcome']} | <b>{x['r_multiple']:+.2f}R</b> ({x['return_pct']:+.2f}%)\nاختبار افتراضي فقط.")

    snap = _snapshot(state, completed)
    print(json.dumps(snap, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
