from __future__ import annotations
import json,os
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml
from .market import quote_daily,quote_intraday,health_snapshot,reset_health
from .strategies import intraday_setup,swing_setup
from .indicators import add_intraday
from .regime import detect
from .decision import finalize
from .precision import precision_context
from .risk_v2 import daily_loss_guard,daily_trade_count_guard
from .macro_guard import assess as assess_macro
from .validation_policy import derive as derive_validation
from .paper_trader import sync as sync_paper
from .portfolio import PortfolioStore
from .performance import save as save_performance
from .signal_tracker import evaluate as evaluate_signals
from .opportunity_selector import select as select_opportunities
from .position_manager import advice
from .dashboard import render
from .telegram import enabled as telegram_enabled,send,signal_message
ROOT=Path(__file__).resolve().parents[2];ENGINE_VERSION='V4.1-Precision'
def load_yaml(p):return yaml.safe_load(Path(p).read_text(encoding='utf-8'))
def load_json(p,d):
    try:return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception:return d
def write_json(p,d):Path(p).write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding='utf-8')
def env_num(n,d):
    v=os.getenv(n);return float(v) if v and v.strip() else float(d)
def market_open():
    n=datetime.now(ZoneInfo('America/New_York'));return n.weekday()<5 and 570<=n.hour*60+n.minute<=960
def group_for(symbol,groups):
    for name,symbols in (groups or {}).items():
        if symbol in symbols:return name
    return 'OTHER'
def strategy_adjustments(accuracy):
    out={'DAY':0,'SWING':0}
    for name in out:
        x=(accuracy or {}).get('by_strategy',{}).get(name,{});samples=int(x.get('samples',0));wr=float(x.get('win_rate',0));avg=float(x.get('avg_return_pct',0))
        if samples<12:continue
        if wr<40 or avg<-.5:out[name]=-8
        elif wr<50 or avg<0:out[name]=-4
        elif samples>=20 and wr>=65 and avg>1:out[name]=4
        elif samples>=20 and wr>=58 and avg>.3:out[name]=2
    return out
def expiry_for(strategy,s):
    now=datetime.now(timezone.utc);return (now+timedelta(minutes=int(s.get('day_signal_expiry_minutes',45))) if strategy=='DAY' else now+timedelta(hours=int(s.get('swing_signal_expiry_hours',24)))).isoformat()
def scan_symbol(symbol,s,regime,portfolio,equity,groups):
    daily=quote_daily(symbol)
    if len(daily)<55:return []
    price=float(daily['Close'].iloc[-1]);av=float(daily['Volume'].tail(20).mean())
    if price<float(s.get('min_price',5)) or av<float(s.get('min_avg_daily_volume',1_000_000)):return []
    group=group_for(symbol,groups);out=[];sw=swing_setup(symbol,daily)
    if sw:
        z=finalize(sw,regime,s,portfolio,equity,precision_context(sw,daily,regime,group));z['expires_at']=expiry_for('SWING',s);z['engine']=ENGINE_VERSION;out.append(z)
    try:
        intr=quote_intraday(symbol,s.get('intraday_period','5d'),s.get('intraday_interval','15m'));day=intraday_setup(symbol,intr)
        if day:
            z=finalize(day,regime,s,portfolio,equity,precision_context(day,daily,regime,group));z['expires_at']=expiry_for('DAY',s);z['engine']=ENGINE_VERSION;out.append(z)
    except Exception as e:print(f'{symbol} intraday warning: {e}')
    return out
def should_alert(sig,state,cooldown):
    k=f"{sig['symbol']}:{sig['strategy']}";p=state.get(k)
    if not p:return True
    try:t=datetime.fromisoformat(p['sent_at']);t=t if t.tzinfo else t.replace(tzinfo=timezone.utc);age=(datetime.now(timezone.utc)-t).total_seconds()/3600
    except Exception:return True
    old=float(p.get('price',sig['price']));move=abs(float(sig['price'])-old)/max(old,.01)*100
    return age>=cooldown or p.get('action')!=sig.get('action') or int(sig['score'])>=int(p.get('score',0))+6 or move>=1
def audit_append(final,now,macro):
    path=ROOT/'data/decision_audit.json';rows=load_json(path,[])
    for x in final:rows.append({'timestamp':now,'engine':ENGINE_VERSION,'symbol':x['symbol'],'strategy':x['strategy'],'setup_type':x.get('setup_type'),'decision':x['simple_decision_ar'],'action':x['action'],'signal':x['signal'],'score':x['score'],'grade':x.get('quality_grade'),'price':x['price'],'entry':[x['entry_low'],x['entry_high']],'stop':x['stop_loss'],'target1':x['target1'],'market':x.get('market_regime_v2'),'event_risk':x.get('event_risk'),'blockers':x.get('precision',{}).get('blockers',[]),'macro_guard':macro,'expires_at':x.get('expires_at')})
    write_json(path,rows[-15000:])
def run():
    s=load_yaml(ROOT/'config/settings.yml')['settings'];universe=load_yaml(ROOT/'config/universe.yml')['universe'];groups=(load_yaml(ROOT/'config/groups.yml') or {}).get('groups',{});macro_events=(load_yaml(ROOT/'config/economic_events.yml') or {}).get('events',[]);macro=assess_macro(macro_events);event_name=os.getenv('GITHUB_EVENT_NAME','local');telegram_ok=telegram_enabled();notify_allowed=event_name in ('schedule','workflow_dispatch')
    if notify_allowed and not telegram_ok:print('TELEGRAM_CONFIG_ERROR');return 3
    if os.getenv('FORCE_RUN')!='1' and not market_open():print('US market closed; scheduled scan skipped.');return 0
    reset_health();equity=env_num('ACCOUNT_EQUITY_USD',s.get('account_equity_usd',10000));s['risk_per_trade_pct']=env_num('RISK_PER_TRADE_PCT',s.get('risk_per_trade_pct',.5));s['max_position_pct']=env_num('MAX_POSITION_PCT',s.get('max_position_pct',15));portfolio=PortfolioStore(ROOT/'data').portfolio()
    for p in portfolio.get('positions',[]):p['group']=group_for(p.get('symbol'),groups)
    trades=load_json(ROOT/'data/trades.json',[]);daily_ok,daily_pnl_pct=daily_loss_guard(trades,equity,float(s.get('max_daily_loss_pct',1.5)));trade_count_ok,today_entries=daily_trade_count_guard(trades,int(s.get('max_daily_new_trades',3)));s['daily_loss_block']=not daily_ok or not trade_count_ok or bool(macro.get('blocked'))
    prior_accuracy=load_json(ROOT/'data/signal_accuracy.json',{});live_adj=strategy_adjustments(prior_accuracy);validation=derive_validation(load_json(ROOT/'data/backtest.json',{}));val_adj=validation.get('strategy_adjustments',{})
    s['strategy_score_adjustments']={n:max(-12,min(4,int(live_adj.get(n,0))+int(val_adj.get(n,0)))) for n in ('DAY','SWING')};s['setup_score_adjustments']=validation.get('setup_adjustments',{});s['disabled_strategies']=validation.get('disabled_strategies',[]);s['disabled_setup_types']=validation.get('disabled_setup_types',[]);write_json(ROOT/'data/validation_policy.json',validation)
    try:regime=detect()
    except Exception as e:print('DATA_ERROR regime:',e);return 2
    signals=[]
    with ThreadPoolExecutor(max_workers=int(s.get('max_intraday_workers',6))) as pool:
        fut={pool.submit(scan_symbol,x,s,regime,portfolio,equity,groups):x for x in universe}
        for f in as_completed(fut):
            try:signals.extend(f.result())
            except Exception as e:print('ERROR',fut[f],e)
    h=health_snapshot();total=h['success']+h['failures'];print('Market-data health:',h)
    if h['success']<8 or (total and h['success']/total<.35):
        reason=f"unreliable market data: {h['success']}/{total} successful requests";write_json(ROOT/'data/data_health.json',{'status':'DATA_ERROR','reason':reason,'health':h,'timestamp':datetime.now(timezone.utc).isoformat()});return 2
    write_json(ROOT/'data/data_health.json',{'status':'OK','health':h,'timestamp':datetime.now(timezone.utc).isoformat()})
    signals=sorted(signals,key=lambda x:(x['score'],x.get('quality_grade')=='A+'),reverse=True);final=[];seen={}
    for x in signals:
        if x['symbol'] not in seen:final.append(x);seen[x['symbol']]=x
        elif x['score']>=92 and seen[x['symbol']]['strategy']!=x['strategy']:final.append(x)
    final=final[:30];now=datetime.now(timezone.utc).isoformat();write_json(ROOT/'data/signals.json',final);audit_append(final,now,macro)
    opportunities=[] if s['daily_loss_block'] else select_opportunities(final,groups,int(s.get('top_n_alerts',3)));write_json(ROOT/'data/top_opportunities.json',opportunities)
    paper=sync_paper(ROOT/'data',opportunities,int(s.get('max_open_positions',5)));write_json(ROOT/'data/paper_validation.json',paper)
    hist=load_json(ROOT/'data/signal_history.json',[])
    for x in final:hist.append({'timestamp':now,'engine':ENGINE_VERSION,'symbol':x['symbol'],'strategy':x['strategy'],'setup_type':x.get('setup_type'),'signal':x['signal'],'action':x['action'],'score':x['score'],'grade':x.get('quality_grade'),'price':x['price'],'stop_loss':x['stop_loss'],'target1':x['target1'],'target2':x['target2'],'market':x.get('market_regime_v2'),'expires_at':x.get('expires_at')})
    write_json(ROOT/'data/signal_history.json',hist[-10000:]);accuracy=evaluate_signals(ROOT/'data',int(s.get('backtest_horizon_days',5)),int(s.get('backtest_min_score',85)));write_json(ROOT/'data/signal_accuracy.json',accuracy)
    perf=save_performance(ROOT/'data');updates=[]
    for p in portfolio.get('positions',[]):
        try:
            d=quote_intraday(p['symbol'],'1d','15m')
            if not d.empty:z=add_intraday(d).iloc[-1];cp=float(z['Close']);updates.append({'symbol':p['symbol'],'current_price':cp,**advice(p,cp,vwap=float(z['VWAP']),ema21=float(z['EMA21']),atr=float(z['ATR14']))})
        except Exception as e:print('Position monitor',p['symbol'],e)
    write_json(ROOT/'data/position_advice.json',updates);render(final,ROOT/'docs/index.html',s.get('timezone','Asia/Riyadh'),regime,portfolio,{**perf,'signal_accuracy':accuracy,'paper_validation':paper,'daily_pnl_pct':daily_pnl_pct,'engine':ENGINE_VERSION})
    state=load_json(ROOT/'data/alert_state.json',{});prev=load_json(ROOT/'data/decision_state.json',{});cooldown=float(s.get('alert_cooldown_hours',4));alerts_sent=0;current={f"{x['symbol']}:{x['strategy']}":{'action':x['action'],'score':x['score'],'price':x['price']} for x in final}
    if notify_allowed and telegram_ok:
        for x in opportunities:
            if should_alert(x,state,cooldown):
                try:send(signal_message(x));alerts_sent+=1;state[f"{x['symbol']}:{x['strategy']}"]={'sent_at':now,'score':x['score'],'price':x['price'],'action':x['action']}
                except Exception as e:print('Telegram signal error:',e)
        for key,p in prev.items():
            if p.get('action')=='BUY_NOW' and current.get(key,{}).get('action')!='BUY_NOW':
                try:send(f"⚠️ <b>{key.split(':')[0]} — ألغي الدخول السابق</b>\nالفرصة لم تعد تحقق شروط اشترِ الآن.")
                except Exception:pass
        write_json(ROOT/'data/alert_state.json',state);write_json(ROOT/'data/decision_state.json',current)
        if event_name=='workflow_dispatch':
            best=opportunities[0] if opportunities else None;lines=[f'✅ <b>Trading Assistant {ENGINE_VERSION}</b>',f"أفضل الفرص: <b>{len(opportunities)}</b>",f"Paper validation: <b>{paper.get('status')}</b> — {paper.get('samples',0)} صفقة",f"Expectancy: <b>{paper.get('expectancy_r',0)}R</b> | Profit Factor: <b>{paper.get('profit_factor',0)}</b>"]
            lines += ['',f"🥇 {best['symbol']} — {best['simple_decision_ar']}"] if best else ['','ℹ️ لا توجد صفقة تستحق الدخول الآن.']
            try:send('\n'.join(lines))
            except Exception as e:print('TELEGRAM_SEND_ERROR:',e);return 3
    else:write_json(ROOT/'data/decision_state.json',current)
    print(f'{ENGINE_VERSION}: {len(final)} ranked, {len(opportunities)} selected; paper={paper}.');return 0
if __name__=='__main__':raise SystemExit(run())
