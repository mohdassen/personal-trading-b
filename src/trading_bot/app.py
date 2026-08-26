from __future__ import annotations
import json, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml
from .market import quote_daily, quote_intraday, health_snapshot, reset_health
from .strategies import intraday_setup, swing_setup
from .regime import detect
from .decision import finalize
from .portfolio import PortfolioStore
from .performance import save as save_performance
from .signal_tracker import evaluate as evaluate_signals
from .opportunity_selector import select as select_opportunities
from .position_manager import advice
from .dashboard import render
from .telegram import enabled as telegram_enabled, send, signal_message
ROOT=Path(__file__).resolve().parents[2]
def load_yaml(p):return yaml.safe_load(Path(p).read_text(encoding='utf-8'))
def load_json(p,d):
    try:return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception:return d
def write_json(p,d):Path(p).write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding='utf-8')
def env_num(n,d):
    v=os.getenv(n);return float(v) if v and v.strip() else float(d)
def market_open():
    n=datetime.now(ZoneInfo('America/New_York'));return n.weekday()<5 and 570<=n.hour*60+n.minute<=960
def strategy_adjustments(accuracy):
    out={'DAY':0,'SWING':0}
    for name in out:
        x=(accuracy or {}).get('by_strategy',{}).get(name,{})
        samples=int(x.get('samples',0));wr=float(x.get('win_rate',0));avg=float(x.get('avg_return_pct',0))
        if samples<12:continue
        if wr<40 or avg<-.5:out[name]=-8
        elif wr<50 or avg<0:out[name]=-4
        elif samples>=20 and wr>=65 and avg>1:out[name]=4
        elif samples>=20 and wr>=58 and avg>.3:out[name]=2
    return out
def scan_symbol(symbol,s,regime,portfolio,equity):
    daily=quote_daily(symbol)
    if len(daily)<55:return []
    price=float(daily['Close'].iloc[-1]);av=float(daily['Volume'].tail(20).mean())
    if price<float(s.get('min_price',5)) or av<float(s.get('min_avg_daily_volume',1_000_000)):return []
    out=[];sw=swing_setup(symbol,daily)
    if sw:out.append(finalize(sw,regime,s,portfolio,equity))
    try:
        intr=quote_intraday(symbol,s.get('intraday_period','5d'),s.get('intraday_interval','15m'));day=intraday_setup(symbol,intr)
        if day:out.append(finalize(day,regime,s,portfolio,equity))
    except Exception as e:print(f'{symbol} intraday warning: {e}')
    return out
def should_alert(sig,state,cooldown):
    k=f"{sig['symbol']}:{sig['strategy']}";p=state.get(k)
    if not p:return True
    try:
        t=datetime.fromisoformat(p['sent_at']);t=t if t.tzinfo else t.replace(tzinfo=timezone.utc);age=(datetime.now(timezone.utc)-t).total_seconds()/3600
    except Exception:return True
    old=float(p.get('price',sig['price']));move=abs(float(sig['price'])-old)/max(old,.01)*100
    return age>=cooldown or int(sig['score'])>=int(p.get('score',0))+8 or move>=1
def run():
    s=load_yaml(ROOT/'config/settings.yml')['settings'];universe=load_yaml(ROOT/'config/universe.yml')['universe'];groups=(load_yaml(ROOT/'config/groups.yml') or {}).get('groups',{});event_name=os.getenv('GITHUB_EVENT_NAME','local');telegram_ok=telegram_enabled()
    print(f'Telegram configured: {telegram_ok}; event: {event_name}')
    if event_name in ('workflow_dispatch','push') and not telegram_ok:print('TELEGRAM_CONFIG_ERROR');return 3
    if os.getenv('FORCE_RUN')!='1' and not market_open():print('US market closed; scheduled scan skipped.');return 0
    reset_health();equity=env_num('ACCOUNT_EQUITY_USD',s.get('account_equity_usd',10000));s['risk_per_trade_pct']=env_num('RISK_PER_TRADE_PCT',s.get('risk_per_trade_pct',.5));s['max_position_pct']=env_num('MAX_POSITION_PCT',s.get('max_position_pct',15));portfolio=PortfolioStore(ROOT/'data').portfolio()
    prior_accuracy=load_json(ROOT/'data/signal_accuracy.json',{});s['strategy_score_adjustments']=strategy_adjustments(prior_accuracy);print('Adaptive strategy weights:',s['strategy_score_adjustments'])
    try:regime=detect()
    except Exception as e:print('DATA_ERROR regime:',e);return 2
    signals=[]
    with ThreadPoolExecutor(max_workers=int(s.get('max_intraday_workers',6))) as pool:
        fut={pool.submit(scan_symbol,x,s,regime,portfolio,equity):x for x in universe}
        for f in as_completed(fut):
            try:signals.extend(f.result())
            except Exception as e:print('ERROR',fut[f],e)
    h=health_snapshot();total=h['success']+h['failures'];print('Market-data health:',h)
    if h['success']<8 or (total and h['success']/total<.35):
        reason=f"unreliable market data: {h['success']}/{total} successful requests";write_json(ROOT/'data/data_health.json',{'status':'DATA_ERROR','reason':reason,'health':h,'timestamp':datetime.now(timezone.utc).isoformat()});return 2
    write_json(ROOT/'data/data_health.json',{'status':'OK','health':h,'timestamp':datetime.now(timezone.utc).isoformat()})
    signals=sorted(signals,key=lambda x:x['score'],reverse=True);final=[];seen={}
    for x in signals:
        if x['symbol'] not in seen:final.append(x);seen[x['symbol']]=x
        elif x['score']>=90 and seen[x['symbol']]['strategy']!=x['strategy']:final.append(x)
    final=final[:30];write_json(ROOT/'data/signals.json',final)
    opportunities=select_opportunities(final,groups,int(s.get('top_n_alerts',3)));write_json(ROOT/'data/top_opportunities.json',opportunities)
    hist=load_json(ROOT/'data/signal_history.json',[]);now=datetime.now(timezone.utc).isoformat()
    for x in final:hist.append({'timestamp':now,'symbol':x['symbol'],'strategy':x['strategy'],'signal':x['signal'],'score':x['score'],'price':x['price'],'stop_loss':x['stop_loss'],'target1':x['target1'],'target2':x['target2']})
    write_json(ROOT/'data/signal_history.json',hist[-10000:])
    accuracy=evaluate_signals(ROOT/'data',int(s.get('backtest_horizon_days',5)),int(s.get('backtest_min_score',85)));write_json(ROOT/'data/signal_accuracy.json',accuracy);write_json(ROOT/'data/strategy_weights.json',{'generated_at':now,'adjustments':strategy_adjustments(accuracy),'accuracy':accuracy});print('Signal accuracy:',accuracy)
    perf=save_performance(ROOT/'data');updates=[]
    for p in portfolio.get('positions',[]):
        try:
            d=quote_intraday(p['symbol'],'1d','15m')
            if not d.empty:cp=float(d['Close'].iloc[-1]);updates.append({'symbol':p['symbol'],'current_price':cp,**advice(p,cp)})
        except Exception as e:print('Position monitor',p['symbol'],e)
    write_json(ROOT/'data/position_advice.json',updates);render(final,ROOT/'docs/index.html',s.get('timezone','Asia/Riyadh'),regime,portfolio,perf)
    state=load_json(ROOT/'data/alert_state.json',{});cooldown=float(s.get('alert_cooldown_hours',4));alerts_sent=0
    if telegram_ok:
        for x in opportunities:
            if should_alert(x,state,cooldown):
                try:send(signal_message(x));alerts_sent+=1;state[f"{x['symbol']}:{x['strategy']}"]={'sent_at':datetime.now(timezone.utc).isoformat(),'score':x['score'],'price':x['price']}
                except Exception as e:print('Telegram signal error:',e)
        write_json(ROOT/'data/alert_state.json',state)
        if event_name in ('workflow_dispatch','push'):
            best=opportunities[0] if opportunities else (final[0] if final else None);lines=['✅ <b>Trading Bot — Scan Completed</b>',f"أفضل الفرص المؤكدة الآن: <b>{len(opportunities)}</b>",f"Market data: <b>{h['success']}/{total} successful</b>"]
            if accuracy['samples']>=10:
                lines += [f"Measured accuracy: <b>{accuracy['win_rate']}%</b> from {accuracy['samples']} completed signals"]
                for name in ('DAY','SWING'):
                    a=accuracy['by_strategy'].get(name,{});lines += [f"{name}: {a.get('win_rate',0)}% ({a.get('samples',0)} samples)"]
            else:lines += [f"Accuracy learning: <b>{accuracy['samples']}/10</b> completed signals collected"]
            if best:lines += ['',f"أفضل فرصة: <b>{best['symbol']}</b> — {best['simple_decision_ar']} ({best['score']}/100)"]
            if not opportunities:lines += ['','ℹ️ لا توجد فرصة شراء مؤكدة الآن. الانتظار قرار صحيح أيضًا.']
            try:send('\n'.join(lines))
            except Exception as e:print('TELEGRAM_SEND_ERROR:',e);return 3
    print(f'Completed: {len(final)} ranked setups; selected opportunities: {len(opportunities)}; Telegram alerts: {alerts_sent}.');return 0
if __name__=='__main__':raise SystemExit(run())
