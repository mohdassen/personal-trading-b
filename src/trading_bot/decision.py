from __future__ import annotations
from .event_risk import assess
from .risk import size_position, portfolio_gate

def _trade_instruction(setup, signal, score):
    price=float(setup.price)
    low=float(setup.entry_low)
    high=float(setup.entry_high)

    if signal == "STRONG_BUY":
        if price < low:
            return "WAIT_FOR_ENTRY", f"Wait for price to enter ${low:.2f}-${high:.2f}"
        if low <= price <= high:
            return "BUY_NOW", f"Buy only inside ${low:.2f}-${high:.2f}"
        return "DO_NOT_CHASE", f"Price is above entry zone; do not chase above ${high:.2f}"

    if signal == "BUY":
        if low <= price <= high:
            return "BUY_NOW", f"Buy inside ${low:.2f}-${high:.2f}"
        if price < low:
            return "WAIT_FOR_ENTRY", f"Wait for pullback/entry zone ${low:.2f}-${high:.2f}"
        return "DO_NOT_CHASE", f"Wait; price is above preferred entry ${high:.2f}"

    if signal == "WATCH":
        return "WATCH", "Setup is developing but not ready for entry"
    if signal == "BLOCKED":
        return "NO_TRADE", "Trade blocked by event or portfolio risk"
    return "NO_TRADE", "No qualified setup"

def _simple_view(signal, action, score, event_level, risk_pct, low, high, stop, target1):
    if action == "BUY_NOW":
        decision_ar="اشترِ الآن"
        decision_en="BUY NOW"
        next_step=f"ادخل فقط بين ${low:.2f} و ${high:.2f}. ضع وقف الخسارة عند ${stop:.2f}. الهدف الأول ${target1:.2f}."
    elif action == "WAIT_FOR_ENTRY":
        decision_ar="انتظر السعر المناسب"
        decision_en="WAIT"
        next_step=f"لا تشترِ الآن. انتظر وصول السعر إلى منطقة ${low:.2f}–${high:.2f}."
    elif action == "DO_NOT_CHASE":
        decision_ar="لا تطارد السعر"
        decision_en="DO NOT BUY NOW"
        next_step=f"السعر ارتفع فوق منطقة الدخول. انتظر رجوعه إلى ${low:.2f}–${high:.2f}."
    elif action == "WATCH":
        decision_ar="راقب فقط"
        decision_en="WATCH"
        next_step="الفرصة لم تكتمل بعد. لا تدخل الصفقة الآن."
    else:
        decision_ar="لا تدخل"
        decision_en="NO TRADE"
        next_step="لا توجد فرصة مناسبة حاليًا. احتفظ بالكاش وانتظر فرصة أوضح."

    if score >= 90:
        confidence="عالية"
    elif score >= 82:
        confidence="جيدة"
    elif score >= 70:
        confidence="متوسطة"
    else:
        confidence="ضعيفة"

    if event_level == "HIGH" or risk_pct >= 0.75:
        risk_label="مرتفعة"
    elif event_level == "MEDIUM" or risk_pct >= 0.45:
        risk_label="متوسطة"
    else:
        risk_label="منخفضة"

    return {
        "simple_decision_ar":decision_ar,
        "simple_decision_en":decision_en,
        "simple_next_step":next_step,
        "confidence_label":confidence,
        "risk_label":risk_label,
    }

def finalize(setup, regime, settings, portfolio, equity):
    event=assess(setup.symbol, int(settings.get("earnings_block_days",2)))
    score=max(0,min(100, setup.raw_score + regime.score_adjustment + event.score_adjustment))

    strong=int(settings.get("strong_buy_score",90))
    buy=int(settings.get("buy_score",82))
    watch=int(settings.get("watch_score",70))
    rr=float(settings.get("min_risk_reward",2.0))

    if event.level=="HIGH":
        signal="BLOCKED"
    elif score>=strong and setup.rr1>=rr:
        signal="STRONG_BUY"
    elif score>=buy and setup.rr1>=rr:
        signal="BUY"
    elif score>=watch:
        signal="WATCH"
    else:
        signal="WAIT"

    cash=float(portfolio.get("cash",equity))
    sizing=size_position(
        setup.price, setup.stop_loss, equity,
        float(settings.get("risk_per_trade_pct",.5)),
        float(settings.get("max_position_pct",15)),
        cash
    )

    gate,gate_reason=portfolio_gate(
        portfolio, setup.symbol, sizing["position_value"], equity,
        float(settings.get("max_total_exposure_pct",60)),
        int(settings.get("max_open_positions",5))
    )

    if signal in ("BUY","STRONG_BUY") and not gate:
        signal="BLOCKED"

    action, instruction = _trade_instruction(setup, signal, score)

    if signal in ("BUY","STRONG_BUY") and sizing["shares"] <= 0:
        signal="BLOCKED"
        action="NO_TRADE"
        instruction="Position sizing returned zero shares; capital/risk settings block the trade"

    simple=_simple_view(
        signal, action, score, event.level, sizing["risk_pct_equity"],
        float(setup.entry_low), float(setup.entry_high), float(setup.stop_loss), float(setup.target1)
    )

    return {
        **setup.to_dict(),
        "score":score,
        "signal":signal,
        "action":action,
        "instruction":instruction,
        **simple,
        "market_regime":regime.label,
        "event_risk":event.level,
        "event_notes":event.notes,
        "market_notes":regime.notes,
        "suggested_shares":sizing["shares"],
        "suggested_value":sizing["position_value"],
        "risk_dollars":sizing["risk_dollars"],
        "risk_pct_equity":sizing["risk_pct_equity"],
        "portfolio_gate":gate_reason,
    }
