from __future__ import annotations
from .event_risk import assess
from .risk import size_position, portfolio_gate

def _entry_quality(setup):
    price=float(setup.price); low=float(setup.entry_low); high=float(setup.entry_high); atr=max(float(setup.atr),0.01)
    center=(low+high)/2; dist_atr=abs(price-center)/atr
    strategy=str(setup.strategy).upper()
    notes=[]
    if price>high:
        return False,"price-above-zone",["السعر أعلى من منطقة الدخول المناسبة"]
    if price<low:
        return False,"price-below-zone",["السعر لم يصل بعد إلى منطقة الدخول"]
    if dist_atr>0.22:
        return False,"entry-too-far-from-center",["السعر داخل المنطقة لكنه قريب من طرفها؛ ننتظر دخولًا أفضل"]
    if strategy=="DAY" and float(setup.momentum)>=1.8:
        return False,"intraday-move-too-fast",["السهم يتحرك بسرعة؛ الانتظار يقلل خطر الشراء بعد اندفاع"]
    if strategy=="SWING" and float(setup.momentum)>=8:
        return False,"swing-move-extended",["السهم ارتفع بقوة خلال أيام قليلة؛ الأفضل عدم مطاردة السعر"]
    notes.append("السعر داخل منطقة دخول مناسبة")
    return True,"entry-quality-ok",notes

def _trade_instruction(setup, signal, score):
    price=float(setup.price); low=float(setup.entry_low); high=float(setup.entry_high)
    if signal in ("STRONG_BUY","BUY"):
        entry_ok,entry_reason,_=_entry_quality(setup)
        if price<low:return "WAIT_FOR_ENTRY",f"Wait for price to enter ${low:.2f}-${high:.2f}"
        if price>high:return "DO_NOT_CHASE",f"Price is above entry zone; do not chase above ${high:.2f}"
        if not entry_ok:return "WAIT_FOR_ENTRY",f"Setup is valid, but wait for a cleaner entry inside ${low:.2f}-${high:.2f}"
        return "BUY_NOW",f"Buy only inside ${low:.2f}-${high:.2f}"
    if signal=="WATCH":return "WATCH","Setup is developing but not ready for entry"
    if signal=="BLOCKED":return "NO_TRADE","Trade blocked by confirmation, event or portfolio risk"
    return "NO_TRADE","No qualified setup"

def _confirmation_gate(setup, regime, signal):
    if signal not in ("BUY","STRONG_BUY"):return True,"not-required",[]
    strategy=str(setup.strategy).upper()
    if regime.label in ("RISK_OFF","HIGH_VOLATILITY"):
        return False,"market conditions are unfavorable",["السوق العام غير مناسب للدخول الجديد"]
    if strategy=="DAY":
        checks=[(setup.vol_ratio>=1.2,"حجم التداول يدعم الحركة"),(setup.momentum>0,"الزخم قصير المدى إيجابي"),(48<=setup.rsi<=70,"الحركة ليست ضعيفة أو مفرطة")]
    else:
        checks=[(setup.vol_ratio>=1.05,"حجم التداول مقبول"),(setup.momentum>0,"اتجاه الأيام الأخيرة إيجابي"),(47<=setup.rsi<=68,"قوة السهم ضمن نطاق صحي")]
    passed=sum(1 for ok,_ in checks if ok);required=3 if signal=="STRONG_BUY" else 2;notes=[text for ok,text in checks if ok]
    if passed<required:return False,f"only {passed}/{len(checks)} confirmations passed",notes
    return True,f"{passed}/{len(checks)} confirmations passed",notes

def _simple_view(signal, action, score, event_level, risk_pct, low, high, stop, target1):
    if action=="BUY_NOW":decision_ar="اشترِ الآن";decision_en="BUY NOW";next_step=f"ادخل فقط بين ${low:.2f} و ${high:.2f}. ضع وقف الخسارة عند ${stop:.2f}. الهدف الأول ${target1:.2f}."
    elif action=="WAIT_FOR_ENTRY":decision_ar="انتظر دخولًا أفضل";decision_en="WAIT";next_step=f"الفرصة جيدة لكن التوقيت ليس مثاليًا الآن. انتظر دخولًا أوضح داخل ${low:.2f}–${high:.2f}."
    elif action=="DO_NOT_CHASE":decision_ar="لا تطارد السعر";decision_en="DO NOT BUY NOW";next_step=f"السعر ارتفع فوق منطقة الدخول. انتظر رجوعه إلى ${low:.2f}–${high:.2f}."
    elif action=="WATCH":decision_ar="راقب فقط";decision_en="WATCH";next_step="الفرصة لم تكتمل بعد. لا تدخل الصفقة الآن."
    else:decision_ar="لا تدخل";decision_en="NO TRADE";next_step="الشروط غير مكتملة حاليًا. احتفظ بالكاش وانتظر فرصة أوضح."
    confidence="عالية" if score>=90 else "جيدة" if score>=82 else "متوسطة" if score>=70 else "ضعيفة"
    risk_label="مرتفعة" if event_level=="HIGH" or risk_pct>=.75 else "متوسطة" if event_level=="MEDIUM" or risk_pct>=.45 else "منخفضة"
    return {"simple_decision_ar":decision_ar,"simple_decision_en":decision_en,"simple_next_step":next_step,"confidence_label":confidence,"risk_label":risk_label}

def finalize(setup, regime, settings, portfolio, equity):
    event=assess(setup.symbol,int(settings.get("earnings_block_days",2)))
    strategy=str(setup.strategy).upper(); learned=settings.get("strategy_score_adjustments",{}); strategy_adjustment=int(learned.get(strategy,0))
    score=max(0,min(100,setup.raw_score+regime.score_adjustment+event.score_adjustment+strategy_adjustment))
    strong=int(settings.get("strong_buy_score",90));buy=int(settings.get("buy_score",82));watch=int(settings.get("watch_score",70));rr=float(settings.get("min_risk_reward",2.0))
    if event.level=="HIGH":signal="BLOCKED"
    elif score>=strong and setup.rr1>=rr:signal="STRONG_BUY"
    elif score>=buy and setup.rr1>=rr:signal="BUY"
    elif score>=watch:signal="WATCH"
    else:signal="WAIT"
    confirmed,confirmation_reason,confirmation_notes=_confirmation_gate(setup,regime,signal)
    if signal in ("BUY","STRONG_BUY") and not confirmed:signal="WATCH"
    cash=float(portfolio.get("cash",equity));sizing=size_position(setup.price,setup.stop_loss,equity,float(settings.get("risk_per_trade_pct",.5)),float(settings.get("max_position_pct",15)),cash)
    gate,gate_reason=portfolio_gate(portfolio,setup.symbol,sizing["position_value"],equity,float(settings.get("max_total_exposure_pct",60)),int(settings.get("max_open_positions",5)))
    if signal in ("BUY","STRONG_BUY") and not gate:signal="BLOCKED"
    entry_ok,entry_quality_reason,entry_quality_notes=_entry_quality(setup) if signal in ("BUY","STRONG_BUY") else (False,"not-required",[])
    action,instruction=_trade_instruction(setup,signal,score)
    if signal in ("BUY","STRONG_BUY") and sizing["shares"]<=0:signal="BLOCKED";action="NO_TRADE";instruction="Position sizing returned zero shares; capital/risk settings block the trade"
    simple=_simple_view(signal,action,score,event.level,sizing["risk_pct_equity"],float(setup.entry_low),float(setup.entry_high),float(setup.stop_loss),float(setup.target1))
    return {**setup.to_dict(),"score":score,"signal":signal,"action":action,"instruction":instruction,**simple,"market_regime":regime.label,"event_risk":event.level,"event_notes":event.notes,"market_notes":regime.notes,"strategy_adjustment":strategy_adjustment,"confirmation_passed":confirmed,"confirmation_reason":confirmation_reason,"confirmation_notes":confirmation_notes,"entry_quality_passed":entry_ok,"entry_quality_reason":entry_quality_reason,"entry_quality_notes":entry_quality_notes,"suggested_shares":sizing["shares"],"suggested_value":sizing["position_value"],"risk_dollars":sizing["risk_dollars"],"risk_pct_equity":sizing["risk_pct_equity"],"portfolio_gate":gate_reason}
