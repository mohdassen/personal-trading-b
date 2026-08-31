from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests
import yaml

import backtest_evidence_momentum as em
import backtest_adaptive_momentum as am

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'data/shadow_momentum_state.json'
THRESHOLD=65
MAX_POSITIONS=5
COST_BPS=10.0


def _load():
    if STATE.exists():
        try:return json.loads(STATE.read_text())
        except Exception:pass
    return {'engine':'V4.8-Adaptive-Momentum-Leadership-Shadow','threshold':THRESHOLD,'last_processed_date':None,'pending':[],'positions':{},'closed':[],'stats':{'closed_trades':0,'wins':0,'sum_r':0.0}}


def _save(s):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps(s,indent=2),encoding='utf-8')


def _telegram(text):
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip();chat=os.getenv('TELEGRAM_CHAT_ID','').strip()
    if not token or not chat:return False
    try:
        r=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat,'text':text,'parse_mode':'HTML'},timeout=15)
        return r.ok
    except Exception:return False


def _latest_raw(symbol,df,spy_f,date):
    try:
        x=em._features(df)
        if date not in x.index:return None
        i=x.index.get_loc(date);r=x.loc[date];sr=spy_f.loc[:date].iloc[-1]
        if i<260 or pd.isna(r['MOM12_1']) or pd.isna(sr['MOM12_1']):return None
        return {'symbol':symbol,'date':str(pd.Timestamp(date).date()),'timestamp':pd.Timestamp(date).isoformat(),'i':int(i),'close':float(r['Close']),'atr':float(r['ATR14']),'atr_pct':float(r['ATR_PCT']),'vol20':float(r['VOL20']),'adv20':float(r['ADV20']),'mom6_1':float(r['MOM6_1']),'mom12_1':float(r['MOM12_1']),'ret1m':float(r['RET1M']),'near52':float(r['NEAR52']),'sma50':float(r['SMA50']),'sma100':float(r['SMA100']),'sma200':float(r['SMA200']),'spy_mom6_1':float(sr['MOM6_1']),'spy_mom12_1':float(sr['MOM12_1']),'spy_vol20':float(sr['VOL20']),'regime_ok':bool(sr['REGIME_OK']),'panic':bool(sr['PANIC'])}
    except Exception:return None


def _close_trade(state,symbol,price,outcome,today):
    p=state['positions'].pop(symbol);entry=float(p['entry']);risk=float(p['risk']);cost=entry*(2*COST_BPS/10000.0)
    r=(float(price)-entry-cost)/risk;ret=(float(price)-entry)/entry*100.0-(2*COST_BPS/100.0)
    rec={**p,'symbol':symbol,'exit_date':today,'exit':round(float(price),4),'outcome':outcome,'r_multiple':round(r,3),'return_pct':round(ret,3)}
    state['closed'].append(rec);state['closed']=state['closed'][-1000:]
    st=state['stats'];st['closed_trades']=int(st.get('closed_trades',0))+1;st['wins']=int(st.get('wins',0))+(1 if r>0 else 0);st['sum_r']=round(float(st.get('sum_r',0))+r,3)
    _telegram(f'🧪 <b>Shadow Momentum — إغلاق تجريبي</b>\n{symbol} | {outcome}\nR: <b>{r:+.2f}</b> | عائد: {ret:+.2f}%\n⚠️ Shadow فقط — لا يوجد إجراء تداول حقيقي.')


def _update_positions(state,prices,today):
    for symbol in list(state['positions']):
        if symbol not in prices:continue
        df=prices[symbol]
        idx=[i for i,d in enumerate(df.index) if str(pd.Timestamp(d).date())==today]
        if not idx:continue
        j=idx[-1];bar=df.iloc[j];p=state['positions'][symbol]
        trail=float(p['trail']);op=float(bar['Open']);lo=float(bar['Low']);hi=float(bar['High'])
        if op<=trail:_close_trade(state,symbol,op,'STOP_GAP',today);continue
        if lo<=trail:_close_trade(state,symbol,trail,'STOP',today);continue
        if op>=float(p['target2']) or hi>=float(p['target2']):_close_trade(state,symbol,float(p['target2']),'TARGET2',today);continue
        if hi>=float(p['target1']):p['armed']=True
        if p.get('armed') and j>0:
            a=max(0,j-10);prior=float(df['Low'].iloc[a:j].min());p['trail']=round(max(trail,float(p['entry']),prior-0.25*float(p['atr'])),4)
        p['bars_held']=int(p.get('bars_held',0))+1
        if p['bars_held']>=em.HOLD_DAYS:_close_trade(state,symbol,float(bar['Close']),'TIME_EXIT',today)


def _enter_pending(state,prices,today):
    if not state.get('pending'):return
    available=max(0,MAX_POSITIONS-len(state['positions']))
    pending=sorted(state['pending'],key=lambda x:x.get('score',0),reverse=True)
    keep=[]
    for sig in pending:
        symbol=sig['symbol']
        if symbol in state['positions']:continue
        if available<=0:keep.append(sig);continue
        df=prices.get(symbol)
        if df is None:keep.append(sig);continue
        idx=[i for i,d in enumerate(df.index) if str(pd.Timestamp(d).date())==today]
        if not idx:keep.append(sig);continue
        entry=float(df['Open'].iloc[idx[-1]]);atr=max(float(sig['atr']),0.01);risk=2.5*atr
        if risk/entry<0.012 or risk/entry>0.12:continue
        state['positions'][symbol]={'signal_date':sig['date'],'entry_date':today,'entry':round(entry,4),'atr':round(atr,4),'risk':round(risk,4),'stop':round(entry-risk,4),'trail':round(entry-risk,4),'target1':round(entry+2*risk,4),'target2':round(entry+4*risk,4),'score':int(sig['score']),'armed':False,'bars_held':0,'mom12_rank':sig.get('mom12_rank'),'near52':sig.get('near52')}
        available-=1
    state['pending']=keep


def _make_signals(state,prices,spy_f,today_ts):
    # Weekly formation, matching research/backtest cadence. Friday only.
    if pd.Timestamp(today_ts).weekday()!=4:return
    raw=[]
    for symbol,df in prices.items():
        if symbol=='SPY':continue
        r=_latest_raw(symbol,df,spy_f,today_ts)
        if r:raw.append(r)
    rows=am._enrich_leadership(raw,prices)
    candidates=[x for x in rows if x.get('base_ok') and int(x.get('score',0))>=THRESHOLD and x['symbol'] not in state['positions']]
    candidates=sorted(candidates,key=lambda x:(x['score'],x.get('mom12_rank',0)),reverse=True)[:MAX_POSITIONS]
    state['pending']=[{'symbol':x['symbol'],'date':x['date'],'score':int(x['score']),'atr':float(x['atr']),'mom12_rank':round(float(x['mom12_rank']),3),'near52':round(float(x['near52']),4)} for x in candidates]
    health=rows[0] if rows else {}
    _telegram(f'🧪 <b>Shadow Momentum — ملخص أسبوعي</b>\nLeadership health: <b>{"ON" if health.get("leadership_health_ok") else "OFF"}</b>\nإشارات مؤهلة للأسبوع القادم: <b>{len(state["pending"])}</b>\nمراكز Shadow المفتوحة: <b>{len(state["positions"])}</b>\n⚠️ لا توجد توصية دخول حقيقية.')


def main():
    now=pd.Timestamp.now(tz='America/New_York');force=os.getenv('FORCE_SHADOW','0')=='1'
    # Two UTC schedules cover DST/standard time; only one is accepted after US close.
    if not force and not (now.weekday()<5 and (now.hour==16 or now.hour==17)):
        print('Shadow: outside after-close processing window');return 0
    today=str(now.date());state=_load()
    if state.get('last_processed_date')==today and not force:
        print('Shadow: already processed',today);return 0
    cfg=yaml.safe_load((ROOT/'config/universe.yml').read_text())['universe'];symbols=list(dict.fromkeys(['SPY']+list(cfg)));prices={}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs={pool.submit(em._download,s):s for s in symbols}
        for f in as_completed(futs):
            s,df=f.result()
            if not df.empty:prices[s]=df
    if 'SPY' not in prices:raise RuntimeError('SPY unavailable')
    spy_f=em._spy_regime(prices['SPY'])
    _update_positions(state,prices,today)
    _enter_pending(state,prices,today)
    # Match the actual latest completed daily bar; avoid fabricating a market date.
    market_date=prices['SPY'].index[-1]
    if str(pd.Timestamp(market_date).date())==today:_make_signals(state,prices,spy_f,market_date)
    state['last_processed_date']=today;state['latest_market_date']=str(pd.Timestamp(market_date).date());_save(state)
    st=state['stats'];wr=(100*st['wins']/st['closed_trades']) if st['closed_trades'] else 0
    print(json.dumps({'engine':state['engine'],'date':today,'pending':len(state['pending']),'open_positions':len(state['positions']),'closed_trades':st['closed_trades'],'win_rate':round(wr,1),'sum_r':st['sum_r']},indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
