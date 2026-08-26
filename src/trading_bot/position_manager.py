from __future__ import annotations

def advice(position,current_price,vwap=None,ema21=None,atr=None):
    entry=float(position['avg_price']);stop=float(position.get('stop',0) or 0);t1=float(position.get('target1',0) or 0);t2=float(position.get('target2',0) or 0);strategy=str(position.get('strategy','manual')).upper();pnl=(current_price-entry)/entry*100 if entry else 0;risk=max(entry-stop,.01) if stop else max(entry*.02,.01);r_mult=(current_price-entry)/risk
    if stop and current_price<=stop:return {'action':'EXIT_NOW','reason':'وقف الخسارة تم الوصول إليه','pnl_pct':round(pnl,2),'new_stop':stop}
    if t2 and current_price>=t2:return {'action':'EXIT_NOW','reason':'الهدف الثاني تحقق','pnl_pct':round(pnl,2),'new_stop':current_price}
    if t1 and current_price>=t1:
        trail=max(entry,stop,current_price-(1.2*atr if atr else current_price*.012));return {'action':'TAKE_50_PERCENT','reason':'الهدف الأول تحقق؛ خذ نصف الربح واحمِ الباقي','pnl_pct':round(pnl,2),'new_stop':round(trail,2)}
    if r_mult>=1.0:
        new_stop=max(stop,entry);return {'action':'HOLD_RAISE_STOP','reason':'الصفقة حققت 1R؛ انقل الوقف إلى نقطة الدخول','pnl_pct':round(pnl,2),'new_stop':round(new_stop,2)}
    if atr and pnl>0:
        new_stop=max(stop,current_price-1.5*atr)
        if new_stop>stop:return {'action':'HOLD_TRAIL_STOP','reason':'احمِ الربح بوقف متحرك','pnl_pct':round(pnl,2),'new_stop':round(new_stop,2)}
    if vwap and ema21 and current_price<vwap and current_price<ema21:return {'action':'TIGHTEN_OR_EXIT','reason':'السعر فقد مستويين مهمين؛ شدد الوقف أو اخرج','pnl_pct':round(pnl,2),'new_stop':round(max(stop,current_price*.995),2)}
    if strategy=='DAY' and pnl<-.60:return {'action':'REVIEW_EXIT','reason':'زخم الصفقة اليومية ضعيف والخسارة تتوسع','pnl_pct':round(pnl,2),'new_stop':round(max(stop,current_price*.995),2)}
    return {'action':'HOLD','reason':'لا يوجد سبب خروج حاليًا','pnl_pct':round(pnl,2),'new_stop':round(stop,2)}
