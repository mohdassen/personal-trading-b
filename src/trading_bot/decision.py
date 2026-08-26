from __future__ import annotations
from .event_risk import assess
from .risk import size_position, portfolio_gate
from .risk_v2 import quality_risk_multiplier, portfolio_correlation_gate, scale_sizing
from .precision import opportunity_grade

def _confirmation_gate(setup,regime,signal):
    if signal not in ('BUY','STRONG_BUY'):return True,'not-required',[]
    if regime.label in ('RISK_OFF','HIGH_VOLATILITY'):return False,'market-unfavorable',['السوق العام غير مناسب للدخول الجديد']
    if setup.strategy=='DAY':checks=[(setup.vol_ratio>=1.2,'الحجم يؤكد الحركة'),(setup.momentum>0,'الزخم إيجابي'),(48<=setup.rsi<=70,'قوة الحركة ضمن نطاق صحي')]
    else:checks=[(setup.vol_ratio>=1.05,'الحجم مقبول'),(setup.momentum>0,'اتجاه الأيام الأخيرة إيجابي'),(47<=setup.rsi<=68,'قوة السهم ضمن نطاق صحي')]
    passed=sum(1 for ok,_ in checks if ok);required=3 if signal=='STRONG_BUY' else 2
    return passed>=required,f'{passed}/{len(checks)} confirmations',[t for ok,t in checks if ok]

def _action(setup,signal,precision,daily_loss_block=False,validation_block=False):
    p=float(setup.price);lo=float(setup.entry_low);hi=float(setup.entry_high);blockers=precision.get('blockers',[])
    if validation_block:return 'NO_TRADE','تم إيقاف هذا النوع مؤقتًا لأن نتائج التحقق التاريخي غير كافية أو ضعيفة'
    if daily_loss_block:return 'NO_TRADE','تم إيقاف الصفقات الجديدة بسبب حد الحماية اليومي'
    if signal not in ('BUY','STRONG_BUY'):
        return ('WATCH','الفرصة لم تكتمل بعد') if signal=='WATCH' else ('NO_TRADE','لا توجد صفقة مناسبة الآن')
    if p>hi:return 'DO_NOT_CHASE','السعر أعلى من منطقة الدخول المناسبة'
    if p<lo:return 'WAIT_FOR_ENTRY','السعر لم يصل بعد إلى منطقة الدخول'
    if blockers:return 'WAIT_FOR_ENTRY','السهم جيد لكن توقيت الدخول أو المساحة المتاحة للربح ليست مثالية الآن'
    return 'BUY_NOW','الدخول مسموح فقط داخل المنطقة المحددة'

def _simple(action,score,grade,event,risk,setup):
    lo,hi,stop,t1=map(float,[setup.entry_low,setup.entry_high,setup.stop_loss,setup.target1])
    if action=='BUY_NOW':d='اشترِ الآن';step=f'ادخل بين ${lo:.2f} و ${hi:.2f}. وقف الخسارة ${stop:.2f}. الهدف الأول ${t1:.2f}.'
    elif action=='WAIT_FOR_ENTRY':d='انتظر دخولًا أفضل';step=f'لا تشترِ الآن. انتظر تحسن التوقيت داخل ${lo:.2f}–${hi:.2f}.'
    elif action=='DO_NOT_CHASE':d='لا تطارد السعر';step=f'انتظر رجوع السعر إلى ${lo:.2f}–${hi:.2f}.'
    elif action=='WATCH':d='راقب فقط';step='الفرصة لم تكتمل. لا تدخل الآن.'
    else:d='لا تدخل';step='احتفظ بالكاش وانتظر فرصة أوضح.'
    conf='عالية' if grade in ('A+','A') else 'متوسطة' if grade=='B' else 'ضعيفة'
    risk_label='مرتفعة' if event=='HIGH' or risk>=.75 else 'متوسطة' if event=='MEDIUM' or risk>=.45 else 'منخفضة'
    return {'simple_decision_ar':d,'simple_next_step':step,'confidence_label':conf,'risk_label':risk_label,'quality_grade':grade}

def finalize(setup,regime,settings,portfolio,equity,precision=None):
    precision=precision or {'adjustment':0,'blockers':[],'market_v2':{'label':regime.label},'group':'OTHER'}
    event=assess(setup.symbol,int(settings.get('earnings_block_days',2)))
    strategy=str(setup.strategy).upper();setup_type=str(getattr(setup,'setup_type','')).upper();learned=int(settings.get('strategy_score_adjustments',{}).get(strategy,0));setup_adj=int(settings.get('setup_score_adjustments',{}).get(setup_type,0));prec=int(precision.get('adjustment',0))
    score=max(0,min(100,int(setup.raw_score)+int(regime.score_adjustment)+int(event.score_adjustment)+learned+setup_adj+prec))
    strong=int(settings.get('strong_buy_score',92));buy=int(settings.get('buy_score',85));watch=int(settings.get('watch_score',72));rr=float(settings.get('min_risk_reward',2.0))
    validation_block=strategy in settings.get('disabled_strategies',[]) or setup_type in settings.get('disabled_setup_types',[])
    if validation_block:signal='BLOCKED'
    elif event.level=='HIGH':signal='BLOCKED'
    elif setup.rr1<rr:signal='WATCH'
    elif score>=strong:signal='STRONG_BUY'
    elif score>=buy:signal='BUY'
    elif score>=watch:signal='WATCH'
    else:signal='WAIT'
    confirmed,confirmation_reason,confirmation_notes=_confirmation_gate(setup,regime,signal)
    if signal in ('BUY','STRONG_BUY') and not confirmed:signal='WATCH'
    grade=opportunity_grade(score,precision)
    if signal in ('BUY','STRONG_BUY') and grade=='C':signal='WATCH'
    if settings.get('daily_loss_block') and signal in ('BUY','STRONG_BUY'):signal='BLOCKED'
    cash=float(portfolio.get('cash',equity));planned_entry=(float(setup.entry_low)+float(setup.entry_high))/2
    base=size_position(planned_entry,setup.stop_loss,equity,float(settings.get('risk_per_trade_pct',.5)),float(settings.get('max_position_pct',15)),cash)
    multiplier=quality_risk_multiplier(grade,precision.get('market_v2',{}).get('label',regime.label));sizing=scale_sizing(base,multiplier)
    gate,gate_reason=portfolio_gate(portfolio,setup.symbol,sizing['position_value'],equity,float(settings.get('max_total_exposure_pct',60)),int(settings.get('max_open_positions',5)))
    corr,corr_reason=portfolio_correlation_gate(portfolio,precision.get('group','OTHER'),int(settings.get('max_same_group_positions',2)))
    if signal in ('BUY','STRONG_BUY') and (not gate or not corr or sizing['shares']<=0):signal='BLOCKED'
    action,instruction=_action(setup,signal,precision,bool(settings.get('daily_loss_block')),validation_block)
    simple=_simple(action,score,grade,event.level,sizing['risk_pct_equity'],setup)
    return {**setup.to_dict(),'score':score,'signal':signal,'action':action,'instruction':instruction,**simple,'market_regime':regime.label,'market_regime_v2':precision.get('market_v2',{}).get('label',regime.label),'event_risk':event.level,'event_notes':event.notes,'strategy_adjustment':learned,'setup_adjustment':setup_adj,'precision_adjustment':prec,'precision':precision,'validation_blocked':validation_block,'confirmation_passed':confirmed,'confirmation_reason':confirmation_reason,'confirmation_notes':confirmation_notes,'planned_entry_price':round(planned_entry,2),'suggested_shares':sizing['shares'],'suggested_value':sizing['position_value'],'risk_dollars':sizing['risk_dollars'],'risk_pct_equity':sizing['risk_pct_equity'],'risk_multiplier':multiplier,'portfolio_gate':gate_reason,'correlation_gate':corr_reason,'daily_loss_blocked':bool(settings.get('daily_loss_block'))}
