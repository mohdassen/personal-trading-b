from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

import backtest_adaptive_momentum as am
import backtest_evidence_momentum as em
import backtest_portfolio_momentum as pm
from src.trading_bot.telegram import enabled as telegram_enabled, send

ROOT = Path(__file__).resolve().parent
ENGINE = "V4.9-Portfolio-Aware-Momentum"
MODE = "FORWARD_ONLY_SHADOW"
THRESHOLD = 55
COST_BPS_PER_SIDE = 10.0
MIN_FORWARD_TRADES = 20
MIN_FORWARD_DAYS = 60
STATE_PATH = ROOT / "data/v49_forward_shadow.json"
SNAPSHOT_PATH = ROOT / "data/v49_shadow_snapshot.json"
SHARIA_CONFIG = ROOT / "config/sharia_shadow.yml"
NY = ZoneInfo("America/New_York")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def now_utc():
    return datetime.now(timezone.utc)


def now_ny():
    return datetime.now(NY)


def strategy_fingerprint():
    defining = [
        ROOT / "backtest_portfolio_momentum.py",
        ROOT / "backtest_adaptive_momentum.py",
        ROOT / "backtest_evidence_momentum.py",
        ROOT / "v49_shadow.py",
        ROOT / "config/groups.yml",
        ROOT / "config/universe.yml",
        SHARIA_CONFIG,
    ]
    h = hashlib.sha256()
    for path in defining:
        h.update(str(path.relative_to(ROOT)).encode())
        h.update(path.read_bytes())
    h.update(f"threshold={THRESHOLD}|cost={COST_BPS_PER_SIDE}".encode())
    return h.hexdigest()[:20]


def generation_id(fp: str):
    return f"v49-{fp[:12]}"


def _normalize_index(df: pd.DataFrame):
    x = df.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(x.index))
    if idx.tz is not None:
        idx = idx.tz_convert(NY).tz_localize(None)
    x.index = idx.normalize()
    return x[~x.index.duplicated(keep="last")].sort_index()


def _completed(df: pd.DataFrame, clock_ny: datetime):
    x = _normalize_index(df)
    today = pd.Timestamp(clock_ny.date())
    minutes = clock_ny.hour * 60 + clock_ny.minute
    # Yahoo can expose an in-progress daily candle. It must never be used in a
    # forward decision. After 16:10 ET we allow today's completed session.
    if clock_ny.weekday() < 5 and minutes < 16 * 60 + 10:
        x = x[x.index < today]
    return x


def _download_prices(symbols, clock_ny):
    prices = {}
    failures = {}
    for symbol in symbols:
        df = pd.DataFrame()
        reason = "empty"
        for attempt in range(pm.DATA_DOWNLOAD_ATTEMPTS):
            delay = pm.DATA_RETRY_SECONDS[min(attempt, len(pm.DATA_RETRY_SECONDS) - 1)]
            if delay:
                time.sleep(delay)
            returned, candidate = em._download(symbol)
            if returned != symbol:
                reason = f"symbol_mismatch:{returned}"
                continue
            if candidate is None or candidate.empty:
                reason = "empty_download"
                continue
            candidate = _completed(candidate, clock_ny)
            if len(candidate) < pm.MIN_HISTORY_ROWS:
                reason = f"short_history:{len(candidate)}"
                continue
            df = candidate
            break
        if df.empty:
            failures[symbol] = reason
        else:
            prices[symbol] = df
    if failures:
        missing = ", ".join(f"{s}({r})" for s, r in sorted(failures.items()))
        raise RuntimeError(f"V4.9 shadow incomplete data snapshot; fail closed: {missing}")
    return prices


def _row_at(symbol, df, spy_f, signal_date: pd.Timestamp):
    x = em._features(df)
    date = pd.Timestamp(signal_date).normalize()
    if date not in x.index:
        return None
    i = x.index.get_loc(date)
    if isinstance(i, slice) or int(i) < 260:
        return None
    r = x.loc[date]
    spy_prior = spy_f.loc[:date]
    if spy_prior.empty:
        return None
    sr = spy_prior.iloc[-1]
    required = [
        "MOM12_1", "MOM6_1", "ATR14", "ATR_PCT", "VOL20", "ADV20",
        "NEAR52", "SMA50", "SMA100", "SMA200",
    ]
    if any(pd.isna(r[k]) for k in required) or pd.isna(sr["MOM12_1"]):
        return None
    return {
        "symbol": symbol,
        "date": str(date.date()),
        "timestamp": date.isoformat(),
        "i": int(i),
        "close": float(r["Close"]),
        "atr": float(r["ATR14"]),
        "atr_pct": float(r["ATR_PCT"]),
        "vol20": float(r["VOL20"]),
        "adv20": float(r["ADV20"]),
        "mom6_1": float(r["MOM6_1"]),
        "mom12_1": float(r["MOM12_1"]),
        "ret1m": float(r["RET1M"]),
        "near52": float(r["NEAR52"]),
        "sma50": float(r["SMA50"]),
        "sma100": float(r["SMA100"]),
        "sma200": float(r["SMA200"]),
        "spy_mom6_1": float(sr["MOM6_1"]),
        "spy_mom12_1": float(sr["MOM12_1"]),
        "spy_vol20": float(sr["VOL20"]),
        "regime_ok": bool(sr["REGIME_OK"]),
        "panic": bool(sr["PANIC"]),
    }


def _sharia_policy():
    policy = (load_yaml(SHARIA_CONFIG).get("policy") or {})
    return {
        "mode": str(policy.get("mode", "conservative_sector_precheck")),
        "excluded_groups": set(str(x) for x in policy.get("excluded_groups", [])),
        "excluded_symbols": set(str(x) for x in policy.get("excluded_symbols", [])),
        "note": str(policy.get("note", "")),
    }


def _candidate_rows(rows):
    groups = pm._group_map()
    sharia = _sharia_policy()
    out = []
    for r in rows:
        symbol = str(r["symbol"])
        group = groups.get(symbol, f"UNGROUPED:{symbol}")
        if group in sharia["excluded_groups"] or symbol in sharia["excluded_symbols"]:
            continue
        if not r.get("base_ok") or int(r["score"]) < THRESHOLD:
            continue
        atr = max(float(r["atr"]), 0.01)
        close = max(float(r["close"]), 0.01)
        estimated_risk_pct = 2.5 * atr / close * 100.0
        spy_vol20 = float(r.get("spy_vol20", 9.0))
        if estimated_risk_pct < 1.2 or estimated_risk_pct > pm.MAX_TRADE_RISK_PCT:
            continue
        if spy_vol20 > pm.MAX_SPY_VOL20_FOR_NEW_RISK:
            continue
        quality = (
            float(r["score"])
            + 6.0 * float(r.get("mom12_rank", 0.0))
            + 4.0 * float(r.get("mom6_rank", 0.0))
            + 2.0 * float(r.get("leadership_breadth50", 0.0))
            - 8.0 * max(0.0, float(r.get("vol20", 0.0)) - 0.35)
        )
        out.append({
            "symbol": symbol,
            "group": group,
            "strategy": "POSITION",
            "setup_type": "PORTFOLIO_MOMENTUM",
            "signal_date": r["date"],
            "timestamp": r["timestamp"],
            "score": int(r["score"]),
            "quality": round(quality, 4),
            "signal_close": round(close, 4),
            "atr": round(atr, 4),
            "estimated_risk_pct": round(estimated_risk_pct, 3),
            "mom12_1": round(float(r["mom12_1"]), 4),
            "mom6_1": round(float(r["mom6_1"]), 4),
            "mom12_rank": round(float(r["mom12_rank"]), 3),
            "mom6_rank": round(float(r["mom6_rank"]), 3),
            "near52": round(float(r["near52"]), 4),
            "spy_vol20": round(spy_vol20, 4),
            "leadership_breadth50": round(float(r.get("leadership_breadth50", 0.0)), 3),
            "leadership_spread20": round(float(r.get("leadership_spread20", 0.0)), 4),
            "sharia_status": "PRECHECK_PASS",
        })
    return out


def _select_new(candidates, state):
    if not candidates:
        return []
    active = list(state.get("pending", [])) + list(state.get("open", []))
    groups_in_use = Counter(x.get("group") for x in active)
    symbols_in_use = {x.get("symbol") for x in active}
    spy_vol20 = float(candidates[0].get("spy_vol20", 9.0))
    max_open, max_new = pm._exposure_limits(spy_vol20)
    capacity = max(0, max_open - len(active))
    limit = min(max_new, capacity)
    chosen = []
    for x in sorted(
        candidates,
        key=lambda z: (float(z["quality"]), int(z["score"]), float(z["mom12_rank"])),
        reverse=True,
    ):
        if len(chosen) >= limit:
            break
        if x["symbol"] in symbols_in_use:
            continue
        if groups_in_use[x["group"]] >= pm.MAX_GROUP_POSITIONS:
            continue
        chosen.append(x)
        symbols_in_use.add(x["symbol"])
        groups_in_use[x["group"]] += 1
    return chosen


def _first_session_after(df, signal_date):
    d = pd.Timestamp(signal_date).normalize()
    later = df[df.index > d]
    return None if later.empty else later.index[0]


def _activate_pending(state, prices, completed_through):
    still_pending = []
    opened = list(state.get("open", []))
    rejected = list(state.get("rejected", []))
    events = []
    for p in state.get("pending", []):
        df = prices.get(p["symbol"])
        if df is None or df.empty:
            still_pending.append(p)
            continue
        entry_date = _first_session_after(df, p["signal_date"])
        if entry_date is None or entry_date > completed_through:
            still_pending.append(p)
            continue
        entry = float(df.loc[entry_date, "Open"])
        atr = max(float(p["atr"]), 0.01)
        gap_atr = abs(entry - float(p["signal_close"])) / atr
        risk = 2.5 * atr
        risk_pct = risk / max(entry, 0.01) * 100.0
        if gap_atr > pm.MAX_ENTRY_GAP_ATR or risk_pct > pm.MAX_TRADE_RISK_PCT or risk_pct < 1.2:
            x = dict(p)
            x.update({
                "rejected_at": now_utc().isoformat(),
                "entry_date": str(entry_date.date()),
                "observed_open": round(entry, 4),
                "entry_gap_atr": round(gap_atr, 3),
                "risk_pct": round(risk_pct, 3),
                "outcome": "ENTRY_REJECTED",
                "reason": "gap_atr" if gap_atr > pm.MAX_ENTRY_GAP_ATR else "risk_pct",
            })
            rejected.append(x)
            events.append(("reject", x))
            continue
        x = dict(p)
        x.update({
            "entry_date": str(entry_date.date()),
            "entry": round(entry, 4),
            "stop": round(entry - risk, 4),
            "target1": round(entry + 2.0 * risk, 4),
            "target2": round(entry + 4.0 * risk, 4),
            "risk_pct": round(risk_pct, 3),
            "entry_gap_atr": round(gap_atr, 3),
            "trail": round(entry - risk, 4),
            "armed": False,
            "last_processed_date": None,
            "bars_held": 0,
            "opened_at": now_utc().isoformat(),
        })
        opened.append(x)
        events.append(("open", x))
    state["pending"] = still_pending
    state["open"] = opened
    state["rejected"] = rejected[-500:]
    return events


def _close_trade(position, exit_date, exit_price, outcome):
    x = dict(position)
    entry = float(x["entry"])
    risk = 2.5 * float(x["atr"])
    cost = entry * (em._cost_pct(COST_BPS_PER_SIDE) / 100.0)
    ret_pct = (float(exit_price) - entry) / entry * 100.0 - em._cost_pct(COST_BPS_PER_SIDE)
    r_mult = (float(exit_price) - entry - cost) / risk
    x.update({
        "exit_date": str(pd.Timestamp(exit_date).date()),
        "exit_price": round(float(exit_price), 4),
        "outcome": outcome,
        "return_pct": round(ret_pct, 3),
        "r_multiple": round(r_mult, 3),
        "closed_at": now_utc().isoformat(),
    })
    return x


def _update_open(state, prices, completed_through):
    remaining = []
    closed = list(state.get("closed", []))
    events = []
    for p in state.get("open", []):
        df = prices.get(p["symbol"])
        if df is None or df.empty:
            remaining.append(p)
            continue
        entry_date = pd.Timestamp(p["entry_date"]).normalize()
        bars = df[(df.index >= entry_date) & (df.index <= completed_through)]
        if bars.empty:
            remaining.append(p)
            continue
        last_processed = (
            pd.Timestamp(p["last_processed_date"]).normalize()
            if p.get("last_processed_date") else None
        )
        trail = float(p.get("trail", p["stop"]))
        armed = bool(p.get("armed", False))
        exit_row = None
        for idx, (bar_date, bar) in enumerate(bars.iterrows()):
            if last_processed is not None and bar_date <= last_processed:
                continue
            op = float(bar["Open"])
            lo = float(bar["Low"])
            hi = float(bar["High"])
            active_stop = trail
            if op <= active_stop:
                exit_row = _close_trade(p, bar_date, op, "STOP_GAP")
            elif lo <= active_stop:
                exit_row = _close_trade(p, bar_date, active_stop, "STOP")
            elif op >= float(p["target2"]):
                exit_row = _close_trade(p, bar_date, float(p["target2"]), "TARGET2")
            elif hi >= float(p["target2"]):
                exit_row = _close_trade(p, bar_date, float(p["target2"]), "TARGET2")
            if exit_row:
                break
            if hi >= float(p["target1"]):
                armed = True
            if armed and idx > 0:
                a = max(0, idx - 10)
                prior = bars.iloc[a:idx]
                if not prior.empty:
                    prior_low = float(prior["Low"].min())
                    trail = max(trail, float(p["entry"]), prior_low - 0.25 * float(p["atr"]))
            if idx >= em.HOLD_DAYS:
                exit_row = _close_trade(p, bar_date, float(bar["Close"]), "TIME_EXIT")
                break
            p["last_processed_date"] = str(bar_date.date())
            p["bars_held"] = idx
            p["trail"] = round(trail, 4)
            p["armed"] = armed
        if exit_row:
            closed.append(exit_row)
            events.append(("close", exit_row))
        else:
            remaining.append(p)
    state["open"] = remaining
    state["closed"] = closed[-2000:]
    return events


def _metrics(closed):
    rows = [
        {
            "timestamp": x.get("timestamp", x.get("opened_at", "")),
            "r_multiple": x["r_multiple"],
            "return_pct": x["return_pct"],
        }
        for x in closed
        if "r_multiple" in x and "return_pct" in x
    ]
    return em._stats(rows)


def _temporal_halves(closed):
    eligible = [x for x in closed if "r_multiple" in x]
    eligible.sort(key=lambda x: x.get("timestamp", ""))
    if not eligible:
        return [em._stats([]), em._stats([])]
    mid = max(1, len(eligible) // 2)
    return [_metrics(eligible[:mid]), _metrics(eligible[mid:])]


def _promotion(state, metrics, halves, clock):
    started = datetime.fromisoformat(state["started_at"].replace("Z", "+00:00"))
    elapsed_days = (clock - started).total_seconds() / 86400.0
    enough = int(metrics["samples"]) >= MIN_FORWARD_TRADES and elapsed_days >= MIN_FORWARD_DAYS
    passed = bool(
        enough
        and float(metrics["expectancy_r"]) > 0.05
        and float(metrics["profit_factor_r"]) >= 1.15
        and float(metrics["max_drawdown_r"]) <= 8.0
        and all(
            int(x["samples"]) >= 6
            and float(x["expectancy_r"]) > -0.05
            and float(x["max_drawdown_r"]) <= 6.0
            for x in halves
        )
    )
    return {
        "eligible_for_review": passed,
        "minimum_samples": MIN_FORWARD_TRADES,
        "minimum_calendar_days": MIN_FORWARD_DAYS,
        "elapsed_days": round(elapsed_days, 1),
        "samples_ready": int(metrics["samples"]) >= MIN_FORWARD_TRADES,
        "time_ready": elapsed_days >= MIN_FORWARD_DAYS,
        "note": "Passing authorizes human review only; never automatic live activation.",
    }


def _init_state(fp, clock):
    branch = os.getenv("GITHUB_REF_NAME", "unknown")
    return {
        "engine": ENGINE,
        "mode": MODE,
        "generation_id": generation_id(fp),
        "strategy_fingerprint": fp,
        "origin_branch": branch,
        "started_at": clock.isoformat(),
        "status": "COLLECTING",
        "last_signal_week": None,
        "last_snapshot_at": None,
        "pending": [],
        "open": [],
        "closed": [],
        "rejected": [],
        "metrics": em._stats([]),
        "halves": [em._stats([]), em._stats([])],
        "promotion_gate": {},
        "historical_validation": {
            "core_v49_full_universe": "PASS",
            "applies_directly_to_sharia_subset": False,
            "note": "Sharia-filtered forward generation must earn its own forward evidence.",
        },
        "sharia": {
            "mode": _sharia_policy()["mode"],
            "status": "CONSERVATIVE_PRECHECK_ONLY",
            "certified": False,
        },
    }


def _is_after_close(clock_ny):
    return clock_ny.weekday() < 5 and (clock_ny.hour * 60 + clock_ny.minute) >= 16 * 60 + 10


def _is_signal_window(clock_ny):
    return clock_ny.weekday() == 4 and _is_after_close(clock_ny)


def _week_key(date):
    d = pd.Timestamp(date)
    iso = d.isocalendar()
    return f"{iso.year}-W{int(iso.week):02d}"


def _message(kind, x):
    if kind == "signal":
        return (
            f"🧪 <b>V4.9 SHADOW — {x['symbol']}</b>\n"
            "مراقبة واختبار فقط — لا تنفذ الصفقة.\n\n"
            f"Score: <b>{x['score']}</b> | المجموعة: <b>{x['group']}</b>\n"
            f"إغلاق الإشارة: <b>${x['signal_close']:.2f}</b>\n"
            f"Momentum 12-1: <b>{x['mom12_1']*100:.1f}%</b>\n"
            f"SPY Vol20: <b>{x['spy_vol20']*100:.1f}%</b>\n"
            "الدخول الافتراضي سيُسجّل على افتتاح الجلسة التالية فقط إذا اجتاز Gap/Risk gates.\n\n"
            "🕌 الفلتر الشرعي هنا Pre-check محافظ فقط، وليس اعتمادًا شرعيًا رسميًا."
        )
    if kind == "open":
        return (
            f"🧪 <b>V4.9 SHADOW ENTRY RECORDED — {x['symbol']}</b>\n"
            "تسجيل افتراضي فقط — لا تنفذ.\n"
            f"Entry: <b>${x['entry']:.2f}</b> | Stop: <b>${x['stop']:.2f}</b>\n"
            f"T1: <b>${x['target1']:.2f}</b> | T2: <b>${x['target2']:.2f}</b>\n"
            f"Risk width: <b>{x['risk_pct']:.2f}%</b>"
        )
    if kind == "close":
        return (
            f"📊 <b>V4.9 SHADOW RESULT — {x['symbol']}</b>\n"
            f"Outcome: <b>{x['outcome']}</b>\n"
            f"Result: <b>{x['r_multiple']:+.2f}R</b> ({x['return_pct']:+.2f}%)\n"
            "هذه نتيجة اختبار Forward وليست صفقة حقيقية."
        )
    return ""


def _notify(events):
    if not telegram_enabled():
        return
    for kind, x in events:
        if kind not in ("signal", "open", "close"):
            continue
        try:
            send(_message(kind, x))
        except Exception as e:
            print(f"V49_SHADOW_TELEGRAM_{kind.upper()}_ERROR: {e}")


def _save_snapshot(state, clock, completed_through=None):
    metrics = _metrics(state.get("closed", []))
    halves = _temporal_halves(state.get("closed", []))
    gate = _promotion(state, metrics, halves, clock)
    state["metrics"] = metrics
    state["halves"] = halves
    state["promotion_gate"] = gate
    state["status"] = "REVIEW_READY" if gate["eligible_for_review"] else "COLLECTING"
    state["last_snapshot_at"] = clock.isoformat()
    if completed_through is not None:
        state["completed_through"] = str(pd.Timestamp(completed_through).date())
    save_json(STATE_PATH, state)
    save_json(SNAPSHOT_PATH, {
        "engine": ENGINE,
        "mode": MODE,
        "generation_id": state["generation_id"],
        "strategy_fingerprint": state["strategy_fingerprint"],
        "status": state["status"],
        "completed_through": state.get("completed_through"),
        "pending": len(state.get("pending", [])),
        "open": len(state.get("open", [])),
        "closed": len(state.get("closed", [])),
        "rejected": len(state.get("rejected", [])),
        "metrics": metrics,
        "halves": halves,
        "promotion_gate": gate,
        "historical_validation": state["historical_validation"],
        "sharia": state["sharia"],
        "last_signal_date": state.get("last_signal_date"),
        "last_candidate_count": state.get("last_candidate_count", 0),
        "last_selected_count": state.get("last_selected_count", 0),
        "updated_at": clock.isoformat(),
    })


def main():
    clock = now_utc()
    clock_ny = now_ny()
    fp = strategy_fingerprint()
    state = load_json(STATE_PATH, {})
    current_branch = os.getenv("GITHUB_REF_NAME", "unknown")
    moved_to_main = current_branch == "main" and state and state.get("origin_branch") not in (None, "main")
    if not state or state.get("strategy_fingerprint") != fp or moved_to_main:
        old = state.get("generation_id") if state else None
        state = _init_state(fp, clock)
        if old:
            state["previous_generation_id"] = old
            state["reset_reason"] = "promoted_to_main" if moved_to_main else "strategy_or_filter_fingerprint_changed"

    bootstrap = os.getenv("V49_SHADOW_BOOTSTRAP", "0").lower() in ("1", "true", "yes")
    if not _is_after_close(clock_ny) and not bootstrap:
        print("V4.9 shadow: outside completed-session processing window; no action.")
        _save_snapshot(state, clock)
        return 0

    universe = list(load_yaml(ROOT / "config/universe.yml").get("universe", []))
    spy_prices = _download_prices(["SPY"], clock_ny)
    spy = spy_prices["SPY"]
    completed_through = spy.index[-1]
    week = _week_key(completed_through)
    signal_due = _is_signal_window(clock_ny) and state.get("last_signal_week") != week and not bootstrap
    already_processed = state.get("completed_through") == str(completed_through.date())
    if already_processed and not signal_due and not bootstrap:
        print(f"V4.9 shadow: session {completed_through.date()} already processed.")
        _save_snapshot(state, clock, completed_through)
        return 0

    prices = dict(spy_prices)
    prices.update(_download_prices(universe, clock_ny))
    for symbol in universe:
        if completed_through not in prices[symbol].index:
            raise RuntimeError(
                f"V4.9 shadow alignment failure: {symbol} missing {completed_through.date()}"
            )

    events = []
    events.extend(_activate_pending(state, prices, completed_through))
    events.extend(_update_open(state, prices, completed_through))

    if signal_due:
        spy_f = em._spy_regime(spy)
        raw = []
        for symbol in universe:
            row = _row_at(symbol, prices[symbol], spy_f, completed_through)
            if row is None:
                raise RuntimeError(
                    f"V4.9 shadow feature failure: {symbol} @ {completed_through.date()}"
                )
            raw.append(row)
        rows = am._enrich_leadership(raw, prices)
        if len(rows) != len(universe):
            raise RuntimeError(f"V4.9 shadow ranking incomplete: {len(rows)}/{len(universe)}")
        candidates = _candidate_rows(rows)
        chosen = _select_new(candidates, state)
        for x in chosen:
            x["created_at"] = clock.isoformat()
            x["generation_id"] = state["generation_id"]
            x["strategy_fingerprint"] = fp
            state["pending"].append(x)
            events.append(("signal", x))
        state["last_signal_week"] = week
        state["last_signal_date"] = str(completed_through.date())
        state["last_candidate_count"] = len(candidates)
        state["last_selected_count"] = len(chosen)

    state["data_integrity"] = {
        "required_symbols": 1 + len(universe),
        "loaded_symbols": len(prices),
        "universe_complete": len(prices) == 1 + len(universe),
        "aligned_session": str(completed_through.date()),
        "fail_closed": True,
    }
    _save_snapshot(state, clock, completed_through)
    _notify(events)
    print(json.dumps(load_json(SNAPSHOT_PATH, {}), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
