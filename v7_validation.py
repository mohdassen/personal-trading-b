"""V7 forward-validation scorecard and promotion gate.

No live execution is authorized by this module. It separates the clean V7.2
forward epoch from legacy/pre-fix trades and requires objective evidence before
calling the engine a shadow candidate.
"""
from __future__ import annotations

from collections import defaultdict

from v7_observability import max_drawdown_r

EPOCH = "V7.2_FORWARD_2026-09-09"
LEGACY_EPOCH = "LEGACY_PRE_V7_2"
MIN_CLOSED_TRADES = 30
MIN_PROFIT_FACTOR = 1.30
MIN_EXPECTANCY_R = 0.0
MAX_DRAWDOWN_R = 8.0


def _f(v, default=0.0):
    try: return float(v)
    except Exception: return default


def trade_stats(trades):
    rs = [_f(x.get("r")) for x in trades or [] if x.get("r") is not None]
    if not rs:
        return {"samples":0,"win_rate":0.0,"expectancy_r":0.0,"profit_factor":0.0,"total_r":0.0,"max_drawdown_r":0.0}
    wins = [r for r in rs if r > 0]; losses = [-r for r in rs if r < 0]
    pf = sum(wins) / sum(losses) if losses else (99.0 if wins else 0.0)
    return {
        "samples":len(rs), "win_rate":round(len(wins)/len(rs)*100,1),
        "expectancy_r":round(sum(rs)/len(rs),3), "profit_factor":round(pf,2),
        "total_r":round(sum(rs),2), "max_drawdown_r":max_drawdown_r(trades),
    }


def _breakdown(trades, field):
    groups = defaultdict(list)
    for t in trades:
        groups[str(t.get(field) or "UNKNOWN")].append(t)
    return {k: trade_stats(v) for k, v in sorted(groups.items())}


def normalize_epochs(state):
    for bucket in ("pending", "open", "closed", "rejected"):
        revised=[]
        for x in state.get(bucket, []):
            if not x.get("paper_epoch"):
                x = {**x, "paper_epoch": LEGACY_EPOCH}
            revised.append(x)
        state[bucket] = revised
    return state


def validation_report(state):
    clean = [x for x in state.get("closed", []) if x.get("paper_epoch") == EPOCH]
    stats = trade_stats(clean)
    gates = {
        "closed_trades_gte_30": stats["samples"] >= MIN_CLOSED_TRADES,
        "expectancy_positive": stats["expectancy_r"] > MIN_EXPECTANCY_R,
        "profit_factor_gte_1_30": stats["profit_factor"] >= MIN_PROFIT_FACTOR,
        "max_drawdown_lte_8R": stats["max_drawdown_r"] <= MAX_DRAWDOWN_R,
    }
    if stats["samples"] < MIN_CLOSED_TRADES:
        status = "COLLECTING_FORWARD_DATA"
    else:
        status = "SHADOW_CANDIDATE" if all(gates.values()) else "PROMOTION_FAILED"
    return {
        "paper_epoch": EPOCH,
        "status": status,
        "live_execution_authorized": False,
        "clean_forward_stats": stats,
        "gates": gates,
        "thresholds": {"min_closed_trades":MIN_CLOSED_TRADES,"min_profit_factor":MIN_PROFIT_FACTOR,"min_expectancy_r_exclusive":MIN_EXPECTANCY_R,"max_drawdown_r":MAX_DRAWDOWN_R},
        "by_setup": _breakdown(clean, "setup"),
        "by_regime": _breakdown(clean, "signal_regime"),
        "legacy_closed_excluded": sum(1 for x in state.get("closed", []) if x.get("paper_epoch") != EPOCH),
    }


def apply_state_validation(state):
    state = normalize_epochs(state)
    state["validation"] = validation_report(state)
    return state


def install(engine):
    original_add = engine.add_new_picks_to_paper
    original_main = engine.main

    def add_with_epoch(state, picks):
        before = {(x.get("symbol"), x.get("setup"), x.get("signal_at")) for x in state.get("pending", [])}
        state = original_add(state, picks)
        try: regime = engine.detect_regime().label
        except Exception: regime = "MIXED"
        pick_map = {(p.get("symbol"), p.get("setup")): p for p in picks or []}
        revised=[]
        for pos in state.get("pending", []):
            key3 = (pos.get("symbol"), pos.get("setup"), pos.get("signal_at"))
            if key3 not in before and not pos.get("paper_epoch"):
                pick = pick_map.get((pos.get("symbol"), pos.get("setup")), {})
                pos = {**pos, "paper_epoch":EPOCH, "signal_regime":regime,
                       "entry_quality":pick.get("entry_quality"), "sharia_research":pick.get("sharia_research")}
            revised.append(pos)
        state["pending"] = revised
        return state

    def main_with_validation():
        rc = original_main()
        state = engine._load(engine.PAPER_STATE, {"pending":[],"open":[],"closed":[],"rejected":[]})
        state = apply_state_validation(state)
        engine._save(engine.PAPER_STATE, state)
        snapshot = engine._load(engine.SNAPSHOT, {})
        snapshot["paper_epoch"] = EPOCH
        snapshot["validation"] = state["validation"]
        engine._save(engine.SNAPSHOT, snapshot)
        return rc

    engine.add_new_picks_to_paper = add_with_epoch
    engine.main = main_with_validation
