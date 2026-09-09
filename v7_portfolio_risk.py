"""Portfolio risk sizing for V7 PAPER research only."""
from __future__ import annotations

DEFAULT_EQUITY = 100_000.0
RISK_PER_TRADE_PCT = 0.50
MAX_OPEN_RISK_PCT = 1.50
MAX_GROUP_RISK_PCT = 1.00
MAX_POSITION_PCT = 25.0


def _f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def risk_dollars(position):
    return max(_f(position.get("entry", position.get("signal_entry"))) - _f(position.get("stop", position.get("signal_stop"))), 0.0) * max(int(position.get("shares", 0) or 0), 0)


def size_trade(entry, stop, equity=DEFAULT_EQUITY, open_positions=None, group="OTHER"):
    entry, stop, equity = _f(entry), _f(stop), max(_f(equity), 0.0)
    per_share = entry - stop
    if entry <= 0 or per_share <= 0 or equity <= 0:
        return {"allowed": False, "reason": "INVALID_RISK_GEOMETRY", "shares": 0}

    open_positions = list(open_positions or [])
    trade_budget = equity * RISK_PER_TRADE_PCT / 100.0
    total_cap = equity * MAX_OPEN_RISK_PCT / 100.0
    group_cap = equity * MAX_GROUP_RISK_PCT / 100.0
    open_risk = sum(risk_dollars(p) for p in open_positions)
    group_risk = sum(risk_dollars(p) for p in open_positions if p.get("group", "OTHER") == group)
    available = min(trade_budget, max(total_cap - open_risk, 0), max(group_cap - group_risk, 0))
    shares_by_risk = int(available // per_share)
    shares_by_notional = int((equity * MAX_POSITION_PCT / 100.0) // entry)
    shares = max(min(shares_by_risk, shares_by_notional), 0)
    if shares < 1:
        return {"allowed": False, "reason": "PORTFOLIO_RISK_LIMIT", "shares": 0, "available_risk": round(available, 2)}
    actual_risk = shares * per_share
    return {
        "allowed": True,
        "reason": "RISK_APPROVED",
        "shares": shares,
        "position_value": round(shares * entry, 2),
        "risk_dollars": round(actual_risk, 2),
        "risk_pct_equity": round(actual_risk / equity * 100, 3),
        "open_risk_before": round(open_risk, 2),
        "open_risk_after": round(open_risk + actual_risk, 2),
        "group_risk_before": round(group_risk, 2),
        "limits": {"risk_per_trade_pct": RISK_PER_TRADE_PCT, "max_open_risk_pct": MAX_OPEN_RISK_PCT, "max_group_risk_pct": MAX_GROUP_RISK_PCT, "max_position_pct": MAX_POSITION_PCT},
    }


def install(engine):
    original_add = engine.add_new_picks_to_paper
    def add_with_risk(state, picks):
        before_pending = {(x.get("symbol"), x.get("setup")) for x in state.get("pending", [])}
        state = original_add(state, picks)
        open_positions = list(state.get("open", []))
        revised = []
        for pos in state.get("pending", []):
            key = (pos.get("symbol"), pos.get("setup"))
            if key in before_pending or pos.get("portfolio_risk"):
                revised.append(pos); continue
            pick = next((p for p in picks if (p.get("symbol"), p.get("setup")) == key), {})
            sizing = size_trade(pos.get("signal_entry"), pos.get("signal_stop"), DEFAULT_EQUITY, open_positions, pick.get("group", "OTHER"))
            if not sizing["allowed"]:
                state.setdefault("rejected", []).append({**pos, "status": "REJECTED", "reason": sizing["reason"], "portfolio_risk": sizing})
                continue
            pos = {**pos, "group": pick.get("group", "OTHER"), "shares": sizing["shares"], "portfolio_risk": sizing}
            revised.append(pos)
            open_positions.append({"entry": pos["signal_entry"], "stop": pos["signal_stop"], "shares": pos["shares"], "group": pos["group"]})
        state["pending"] = revised
        return state
    engine.add_new_picks_to_paper = add_with_risk
