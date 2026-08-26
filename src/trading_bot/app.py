from __future__ import annotations
import json,os
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml
from .market import quote_daily,quote_intraday
from .strategies import intraday_setup,swing_setup
from .regime import detect
from .decision import finalize
from .portfolio import PortfolioStore
from .performance import save as save_performance
from .position_manager import advice
from .dashboard import render
from .telegram import enabled as telegram_enabled,send,signal_message
ROOT=Path(__file__).resolve().parents[2]
def load_yaml(path):return yaml.safe_load(Path(path).read_text(encoding='utf-8'))
def load_json(path,default):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:return default
def write_json(path,data):Path(path).write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
def env_num(name,default):
    v=os.getenv(name);return float(v) if v and v.strip() else float(default)
def market_open():
    n=datetime.now(ZoneInfo('America/New_York'))
    if n.weekday()>=5:return False
    m=n.hour*60+n.minute;return 570<=m<=960
def scan_symbol(symbol,settings,regime,portfolio,equity):
    daily=quote_daily(symbol)
    if daily.empty or len(daily)<55:return []
    price=float(daily['Close'].iloc[-1]);av=float(daily['Volume'].tail(20).mean())
    if price<float(settings.get('min_price',5)) or av<float(settings.get('min_avg_daily_volume',1000000)):return []
    c=[];sw=swing_setup(symbol,daily)
    if sw:c.append(finalize(sw,regime,settings,portfolio,equity))
    try:
        intr=quote_intraday(symbol,settings.get('intraday_period','5d'),settings.get('intraday_interval','15m'));day=intraday_setup(symbol,intr)
        if day:c.append(finalize(day,regime,settings,portfolio,equity))
    except Exception as exc:print(f'{symbol} intraday warning: {exc}')
    return c
def should_alert(s,state,cooldown):
    prev=state.get(f"{s['symbol']}:{s['strategy']}")
    if not prev:return True
    try:
        sent=datetime.fromisoformat(prev['sent_at']);sent=sent if sent.tzinfo else sent.replace(tzinfo=timezone.utc);age=(datetime.now(timezone.utc)-sent).total_seconds()/3600
    except Exception:return True
    move=abs(s['price']-prev.get('price',s['price']))/max(prev.get('price',s['price']),.01)*100
    return age>=cooldown or s['score']>=prev.get('score',0)+8 or move>=1
def run():
    cfg=load_yaml(ROOT/'config/settings.yml')['settings'];universe=load_yaml(ROOT/'config/universe.yml')['universe']
    if os.getenv('FORCE_RUN')!='1' and not market_open():print('US market closed; scheduled scan skipped.');return 0
    equity=env_num('ACCOUNT_EQUITY_USD',cfg.get('account_equity_usd',10000));cfg['risk_per_trade_pct']=env_num('RISK_PER_TRADE_PCT',cfg.get('risk_per_trade_pct',.5));cfg['max_position_pct']=env_num('MAX_POSITION_PCT',cfg.get('max_position_pct',15))
    store=PortfolioStore(ROOT/'data');portfolio=store.portfolio();regime=detect();print('Market regime:',regime)
    signals=[];workers=int(cfg.get('max_intraday_workers',6))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut={pool.submit(scan_symbol,s,cfg,regime,portfolio,equity):s for s in universe}
        for f in as_completed(fut):
            s=fut[f]
            try:signals.extend(f.result())
            except Exception as exc:print(f'ERROR {s}: {exc}')
    signals=sorted(signals,key=lambda x:x['score'],reverse=True);final=[];seen={}
    for s in signals:
        if s['symbol'] not in seen:final.append(s);seen[s['symbol']]=s
        elif s['score']>=90 and seen[s['symbol']]['strategy']!=s['strategy']:final.append(s)
    final=final[:30];write_json(ROOT/'data/signals.json',final)
    hist=load_json(ROOT/'data/signal_history.json',[]);now=datetime.now(timezone.utc).isoformat()
    for s in final:hist.append({'timestamp':now,'symbol':s['symbol'],'strategy':s['strategy'],'signal':s['signal'],'score':s['score'],'price':s['price'],'stop_loss':s['stop_loss'],'target1':s['target1'],'target2':s['target2']})
    write_json(ROOT/'data/signal_history.json',hist[-10000:]);perf=save_performance(ROOT/'data')
    updates=[]
    for p in portfolio.get('positions',[]):
        try:
            d=quote_intraday(p['symbol'],'1d','15m')
            if not d.empty:cp=float(d['Close'].iloc[-1]);updates.append({'symbol':p['symbol'],'current_price':cp,**advice(p,cp)})
        except Exception as exc:print('Position monitor:',p['symbol'],exc)
    write_json(ROOT/'data/position_advice.json',updates);render(final,ROOT/'docs/index.html',cfg.get('timezone','Asia/Riyadh'),regime,portfolio,perf)
    state=load_json(ROOT/'data/alert_state.json',{});cool=float(cfg.get('alert_cooldown_hours',4))
    if telegram_enabled():
        for s in [x for x in final if x['signal'] in ('STRONG_BUY','BUY')][:int(cfg.get('top_n_alerts',3))]:
            if should_alert(s,state,cool):
                try:send(signal_message(s));state[f"{s['symbol']}:{s['strategy']}"]={'sent_at':datetime.now(timezone.utc).isoformat(),'score':s['score'],'price':s['price']}
                except Exception as exc:print('Telegram:',exc)
        for u in updates:
            if u['action'] in ('EXIT','TAKE_PARTIAL','TIGHTEN/EXIT'):
                try:send(f"🔔 <b>{u['symbol']} — {u['action']}</b>\n{u['reason']}\nP/L: {u['pnl_pct']}%")
                except Exception:pass
        write_json(ROOT/'data/alert_state.json',state)
    print(f'Completed: {len(final)} ranked setups.');return 0
if __name__=='__main__':raise SystemExit(run())
