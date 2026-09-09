"""V7 paper portfolio ledger, legacy sizing backfill, MAE/MFE and drawdown.

Research/PAPER only. Backfilled sizing is explicitly hypothetical and never
changes historical entries/stops or implies the old trades passed the new risk gate.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from src.trading_bot.market import quote_intraday
from v7_portfolio_risk import DEFAULT_EQUITY, size_trade, risk_dollars


def _f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def max_drawdown_r(closed):
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for trade in closed or []:
        if trade.get("r") is None:
            continue
        cumulative += _f(trade.get("r"))
        peak = max(peak, cumulative)
        worst = max(worst, peak - cumulative)
    return round(worst, 3)


def _backfill_legacy_sizing(state):
    sized = [p for p in state.get("open", []) if int(p.get("shares", 0) or 0) > 0]
    revised = []
    for pos in state.get("open", []):
        if int(pos.get("shares", 0) or 0) > 0:
            revised.append(pos)
            continue
        # Historical positions pre-date the risk engine. Size them for analytics
        # only; use a unique legacy group so we do not retroactively reject trades.
        sizing = size_trade(
            pos.get("entry"), pos.get("stop"), DEFAULT_EQUITY, sized,
            f"LEGACY_BACKFILL_{pos.get('symbol','UNKNOWN')}",
        )
        if sizing.get("allowed"):
            pos = {
                **pos,
                "shares": sizing["shares"],
                "portfolio_risk": sizing,
                "risk_backfill": True,
                "risk_backfill_note": "Hypothetical analytics sizing; trade opened before V7 portfolio risk engine",
            }
            sized.append(pos)
        revised.append(pos)
    state["open"] = revised
    return state


def _excursions(pos):
    entry, stop = _f(pos.get("entry")), _f(pos.get("stop"))
    shares = int(pos.get("shares", 0) or 0)
    risk_per_share = max(entry - stop, 0.01)
    intr = quote_intraday(pos["symbol"], "5d", "15m")
    opened = pd.Timestamp(pos.get("opened_at"))
    if opened.tzinfo is None:
        opened = opened.tz_localize("UTC")
    bars = intr[intr.index >= opened]
    if bars.empty:
        return {}
    low = _f(bars["Low"].min(), entry)
    high = _f(bars["High"].max(), entry)
    mark = _f(bars["Close"].iloc[-1], entry)
    mae_r = (low - entry) / risk_per_share
    mfe_r = (high - entry) / risk_per_share
    unrealized_r = (mark - entry) / risk_per_share
    return {
        "mark": round(mark, 4),
        "mae_r": round(mae_r, 3),
        "mfe_r": round(mfe_r, 3),
        "unrealized_r": round(unrealized_r, 3),
        "mae_dollars": round((low - entry) * shares, 2) if shares else None,
        "mfe_dollars": round((high - entry) * shares, 2) if shares else None,
        "unrealized_pnl_dollars": round((mark - entry) * shares, 2) if shares else None,
        "observed_bars": int(len(bars)),
    }


def enrich_state(state):
    state = _backfill_legacy_sizing(state)
    revised = []
    for pos in state.get("open", []):
        try:
            revised.append({**pos, **_excursions(pos)})
        except Exception as exc:
            revised.append({**pos, "observability_error": type(exc).__name__})
    state["open"] = revised

    closed = state.get("closed", [])
    realized_dollars = sum(_f(t.get("pnl_dollars")) for t in closed)
    unrealized_dollars = sum(_f(t.get("unrealized_pnl_dollars")) for t in revised)
    open_risk = sum(risk_dollars(t) for t in revised)
    metrics = dict(state.get("metrics", {}))
    metrics["max_drawdown_r"] = max_drawdown_r(closed)
    metrics["realized_pnl_dollars"] = round(realized_dollars, 2)
    metrics["unrealized_pnl_dollars"] = round(unrealized_dollars, 2)
    state["metrics"] = metrics
    state["portfolio_ledger"] = {
        "paper_starting_equity": DEFAULT_EQUITY,
        "equity_mark": round(DEFAULT_EQUITY + realized_dollars + unrealized_dollars, 2),
        "realized_pnl_dollars": round(realized_dollars, 2),
        "unrealized_pnl_dollars": round(unrealized_dollars, 2),
        "open_risk_dollars": round(open_risk, 2),
        "open_risk_pct": round(open_risk / DEFAULT_EQUITY * 100, 3),
        "open_positions": len(revised),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return state


def install(engine):
    original_update = engine.update_paper_state
    original_close = engine._close_trade

    def close_with_dollars(pos, exit_price, reason, when):
        closed = original_close(pos, exit_price, reason, when)
        shares = int(pos.get("shares", 0) or 0)
        if shares:
            closed["pnl_dollars"] = round((_f(exit_price) - _f(pos.get("entry"))) * shares, 2)
        for field in ("mae_r", "mfe_r", "mae_dollars", "mfe_dollars"):
            if field in pos:
                closed[field] = pos[field]
        return closed

    def update_with_observability(state):
        updated, events = original_update(state)
        return enrich_state(updated), events

    engine._close_trade = close_with_dollars
    engine.update_paper_state = update_with_observability
