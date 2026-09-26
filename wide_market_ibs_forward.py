from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import yfinance as yf

OUT=Path('data/wide_market_ibs_forward.json')
EPOCH='2026-09-28'
COST_BPS=30.0
RULE='IBS-FROZEN-2026-09-18-r1'
MIN_PRICE=5.0
MIN_DOLLAR_VOL20=20_000_000
MAX_DOWNLOAD_ERROR_RATE=0.20

# Frozen broad liquid-equity research universe. Parameters are not changed based on outcomes.
_RAW_SYMBOLS='''AAPL MSFT NVDA AMZN GOOGL GOOG META AVGO TSLA BRK-B JPM LLY V MA NFLX COST WMT ORCL HD PG JNJ BAC ABBV KO PLTR CRM AMD CSCO GE IBM CVX XOM CAT GS MS AXP MRK MCD DIS UBER ABNB BKNG NOW PANW CRWD SNOW DDOG NET SHOP AMAT MU QCOM TXN LRCX KLAC ADI INTU ADP ISRG REGN VRTX GILD AMGN TMUS CMCSA PEP SBUX NKE LOW TGT TJX ROST MAR CMG YUM DHI LEN PHM NEE DUK SO AEP EXC CEG VST ETN HON RTX LMT NOC GD BA DE UNP UPS FDX WM RSG LIN APD SHW ECL FCX NUE STLD COP EOG SLB OXY MPC VLO PSX KMI WMB TMO DHR ABT MDT SYK BSX EW UNH ELV CVS CI HUM PFE BMY ZTS CB PGR ALL TRV MMC AJG SCHW BLK SPGI ICE CME COF USB PNC TFC BK C WFC PYPL SQ COIN HOOD RBLX DASH RDDT ARM SMCI ANET DELL HPE HPQ ACN IBM SAP ADBE ADSK CDNS SNPS FTNT ZS TEAM MDB OKTA DOCU TWLO TTD APP SPOT ROKU PARA WBD F GM RIVN LCID CCL RCL NCLH DAL UAL AAL LUV EXPE TCOM MELI SE PDD BABA JD BIDU NVO AZN NVS SNY UL DEO PM MO MDLZ KHC GIS CL KMB EL MNST CELH DG DLTR KR ACI ORLY AZO GPC ODFL FAST URI PWR GWW ITW EMR MMM JCI CARR TT PH ROK IR CMI PCAR'''.split()
SYMBOLS=list(dict.fromkeys(_RAW_SYMBOLS))


def dl(symbol):
    end=(datetime.now(timezone.utc)+timedelta(days=2)).date().isoformat()
    # Yahoo class-share notation uses '-' (for example BRK-B).
    x=yf.download(symbol,start='2024-01-01',end=end,interval='1d',auto_adjust=True,progress=False,threads=False,timeout=20)
    if x.empty:return x
    if isinstance(x.columns,pd.MultiIndex):x.columns=x.columns.get_level_values(0)
    x=x[['Open','High','Low','Close','Volume']].dropna().copy()
    idx=pd.DatetimeIndex(x.index)
    if idx.tz is not None:idx=idx.tz_convert(None)
    x.index=idx.normalize();return x.sort_index()


def prep(x):
    y=x.copy(); y['range25']=(y.High-y.Low).rolling(25).mean(); y['high10']=y.High.rolling(10).max()
    y['lower']=y.high10-2.5*y.range25; y['ibs']=(y.Close-y.Low)/(y.High-y.Low).replace(0,pd.NA)
    y['sma300']=y.Close.rolling(300).mean(); y['prev_high']=y.High.shift(1)
    y['dollar_vol20']=(y.Close*y.Volume).rolling(20).mean(); return y


def default_state(): return {'pending_entry':None,'position':None,'pending_exit':None,'trades':[],'last_processed':None}


def validate_state(state):
    if not isinstance(state,dict): raise RuntimeError('state is not a dict')
    for k in ('pending_entry','position','pending_exit','trades','last_processed'):
        if k not in state: raise RuntimeError(f'missing state key {k}')
    if not isinstance(state['trades'],list): raise RuntimeError('trades is not a list')


def process(symbol,x,state):
    validate_state(state)
    start=pd.Timestamp(EPOCH)
    if state.get('last_processed'): start=max(start,pd.Timestamp(state['last_processed'])+pd.Timedelta(days=1))
    for date,row in x.loc[x.index>=start].iterrows():
        exited=False
        if state['pending_exit'] and state['position']:
            ep=float(row.Open); p=state['position']; gross=ep/p['entry_price']-1; net=gross-COST_BPS/10000
            state['trades'].append({'symbol':symbol,'signal_date':p['signal_date'],'entry_date':p['entry_date'],'entry_price':round(float(p['entry_price']),6),'exit_signal_date':state['pending_exit']['signal_date'],'exit_date':str(date.date()),'exit_price':round(ep,6),'gross_return':round(gross,6),'net_return':round(net,6)})
            state['position']=None;state['pending_exit']=None;exited=True
        if state['pending_entry'] and not state['position'] and not exited:
            state['position']={'signal_date':state['pending_entry']['signal_date'],'entry_date':str(date.date()),'entry_price':float(row.Open)};state['pending_entry']=None
        if state['position'] and not state['pending_exit']:
            if (pd.notna(row.prev_high) and row.Close>row.prev_high) or (pd.notna(row.sma300) and row.Close<row.sma300): state['pending_exit']={'signal_date':str(date.date())}
        if not state['position'] and not state['pending_entry'] and not state['pending_exit'] and not exited:
            liquid=pd.notna(row.dollar_vol20) and row.dollar_vol20>=MIN_DOLLAR_VOL20 and row.Close>=MIN_PRICE
            if liquid and pd.notna(row.lower) and pd.notna(row.ibs) and row.Close<row.lower and row.ibs<0.30:
                state['pending_entry']={'signal_date':str(date.date()),'close':round(float(row.Close),4),'ibs':round(float(row.ibs),4),'lower_band':round(float(row.lower),4)}
        state['last_processed']=str(date.date())
    return state


def stats(trades):
    r=[t['net_return'] for t in trades]
    if not r:return {'trades':0,'avg_net_return':None,'win_rate':None,'profit_factor':None}
    w=[z for z in r if z>0];l=[z for z in r if z<0];pf=sum(w)/abs(sum(l)) if l else 999.0
    return {'trades':len(r),'avg_net_return':round(sum(r)/len(r),6),'win_rate':round(len(w)/len(r),4),'profit_factor':round(pf,3)}


def main():
    old_states={}; previous=None
    if OUT.exists():
        previous=json.loads(OUT.read_text(encoding='utf-8'))
        # Fail closed if someone changes the frozen experiment definition after evidence begins.
        for key,expected in [('rule_version',RULE),('epoch',EPOCH),('cost_bps_roundtrip',COST_BPS)]:
            if previous.get(key)!=expected: raise RuntimeError(f'frozen metadata mismatch: {key}')
        old_states=previous.get('states',{})

    states={}; errors=[]; latest_dates=[]
    for s in SYMBOLS:
        try:
            x=dl(s)
            if len(x)<320:
                errors.append(s); states[s]=old_states.get(s,default_state()); continue
            latest_dates.append(str(x.index[-1].date()))
            states[s]=process(s,prep(x),old_states.get(s,default_state()))
        except Exception as e:
            errors.append(s); states[s]=old_states.get(s,default_state())
            print(f'WARN {s}: {type(e).__name__}: {e}',flush=True)

    error_rate=len(errors)/len(SYMBOLS)
    if error_rate>MAX_DOWNLOAD_ERROR_RATE:
        raise RuntimeError(f'data coverage failure: {len(errors)}/{len(SYMBOLS)} symbols failed ({error_rate:.1%})')

    trades=[t for st in states.values() for t in st['trades']]
    # Duplicate closed trade IDs indicate ledger corruption; fail instead of publishing statistics.
    ids=[(t['symbol'],t['signal_date'],t['entry_date'],t['exit_date']) for t in trades]
    if len(ids)!=len(set(ids)): raise RuntimeError('duplicate closed trade detected')

    pending=[s for s,st in states.items() if st['pending_entry']]
    openp=[s for s,st in states.items() if st['position']]
    exits=[s for s,st in states.items() if st['pending_exit']]
    result={
        'strategy':'WIDE_MARKET_IBS_FORWARD','rule_version':RULE,'epoch':EPOCH,
        'mode':'FORWARD_SHADOW_ONLY_NO_ORDERS','live_execution':'LOCKED',
        'universe_size':len(SYMBOLS),'universe_duplicates_removed':len(_RAW_SYMBOLS)-len(SYMBOLS),
        'liquidity_filter':f'price>={MIN_PRICE:g} and 20d avg dollar volume>={MIN_DOLLAR_VOL20/1_000_000:g}M',
        'cost_bps_roundtrip':COST_BPS,'latest_downloaded_market_date':max(latest_dates) if latest_dates else None,
        'download_error_rate':round(error_rate,4),'stats':stats(trades),'pending_entries':pending,
        'open_positions':openp,'pending_exits':exits,'download_errors':errors,'states':states,
        'integrity':'append-only state; frozen metadata checked; duplicate trades rejected; prior fills/trades are never recomputed from later historical revisions'
    }
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['universe_size','latest_downloaded_market_date','download_error_rate','stats','pending_entries','open_positions','pending_exits','download_errors']},indent=2))
if __name__=='__main__':main()
