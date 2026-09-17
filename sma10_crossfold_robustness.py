from __future__ import annotations

import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT=Path('data/sma10_crossfold_robustness.json')
START='2014-01-01'; BASE=30.0; STRESS=60.0
SYMBOLS=sorted([
'AAPL','MSFT','NVDA','AMD','AMZN','GOOGL','META','TSLA','AVGO','ORCL','CRM','ADBE','NFLX','MU','QCOM','AMAT','LRCX','KLAC','CRWD','PANW','NOW','PLTR','XOM','CVX','COP','SLB','ABBV','JNJ','LLY','UNH','CAT','DE','GE','WMT','COST','HD','DIS','ROKU','TMUS','GILD','INTC','SNOW','DDOG','NET','MDB','SHOP','UBER','ABNB','BKNG','TGT','LOW','NKE','SBUX','MCD','ISRG','VRTX','REGN','PFE','BA','HON','RTX','CMCSA','SPOT','RBLX','MELI'])
PERIODS=[('2016_2020','2016-01-01','2020-12-31'),('2021_2023','2021-01-01','2023-12-31'),('2024_PLUS','2024-01-01',None)]

def dl(s):
    try:
        x=yf.download(s,start=START,interval='1d',auto_adjust=True,progress=False,threads=False,timeout=30)
        if isinstance(x.columns,pd.MultiIndex):
            x=x.xs(s,axis=1,level=-1) if s in x.columns.get_level_values(-1) else x.set_axis(x.columns.get_level_values(0),axis=1)
        if x.empty or not {'Open','Close'}.issubset(x.columns): return pd.DataFrame()
        x=x[['Open','Close']].dropna().copy(); idx=pd.DatetimeIndex(x.index)
        if idx.tz is not None: idx=idx.tz_convert(None)
        x.index=idx; return x.sort_index()
    except Exception as e:
        print('DOWNLOAD_FAIL',s,repr(e)); return pd.DataFrame()

def mt(df):
    x=df.copy(); x['m']=x.index.to_period('M'); groups=list(x.groupby('m',sort=True)); rows=[]
    for i,(m,g) in enumerate(groups):
        row={'m':m,'close':float(g.Close.iloc[-1]),'next_open':np.nan}
        if i+1<len(groups): row['next_open']=float(groups[i+1][1].sort_index().Open.iloc[0])
        rows.append(row)
    z=pd.DataFrame(rows).set_index('m').sort_index(); z['sma10']=z.close.rolling(10).mean(); return z

def nret(t,m,bps):
    if m not in t.index:return None
    i=t.index.get_loc(m)
    if isinstance(i,slice) or i+1>=len(t):return None
    e=float(t.iloc[i].next_open)
    if not math.isfinite(e) or e<=0:return None
    return float(t.iloc[i+1].close)/e-1-bps/10000

def port(tables, filtered, bps):
    months=sorted(set().union(*[set(t.index) for t in tables.values()])) if tables else []
    out={}
    for m in months:
        rs=[]
        for s,t in tables.items():
            if m not in t.index: continue
            r=t.loc[m]
            if filtered and (pd.isna(r.sma10) or not (r.close>r.sma10)): continue
            v=nret(t,m,bps)
            if v is not None: rs.append(v)
        if rs: out[m.to_timestamp(how='end')]=float(np.mean(rs))
    return pd.Series(out,dtype=float).sort_index()

def rel(s,b,start,end):
    a=s[s.index>=pd.Timestamp(start)]; q=b[b.index>=pd.Timestamp(start)]
    if end:
        a=a[a.index<=pd.Timestamp(end)]; q=q[q.index<=pd.Timestamp(end)]
    d=pd.concat([a.rename('s'),q.rename('b')],axis=1).dropna()
    if d.empty:return {'months':0,'relative_wealth':None,'excess_sharpe':None}
    ex=d.s-d.b; vol=float(ex.std(ddof=1)); sh=float(ex.mean()/vol*math.sqrt(12)) if vol>1e-12 else None
    return {'months':len(d),'relative_wealth':round(float((1+d.s).prod()/(1+d.b).prod()-1),4),'excess_sharpe':None if sh is None else round(sh,3)}

def main():
    raw={}
    for i,s in enumerate(SYMBOLS,1):
        x=dl(s); print(f'[{i}/{len(SYMBOLS)}] {s}: rows={len(x)}')
        if len(x)>=500: raw[s]=x
    tables={s:mt(x) for s,x in raw.items()}
    folds={k:[] for k in range(5)}
    for i,s in enumerate(sorted(tables)): folds[i%5].append(s)
    cells=[]; stress2024=[]
    for k,ss in folds.items():
        ft={s:tables[s] for s in ss}
        strat30=port(ft,True,BASE); bench30=port(ft,False,BASE); strat60=port(ft,True,STRESS); bench60=port(ft,False,STRESS)
        for name,start,end in PERIODS:
            r=rel(strat30,bench30,start,end); cells.append({'fold':k,'period':name,**r})
        stress2024.append({'fold':k,**rel(strat60,bench60,'2024-01-01',None)})
    positive=sum(1 for c in cells if (c['relative_wealth'] or -99)>0)
    p2024=[c for c in cells if c['period']=='2024_PLUS']; pos2024=sum(1 for c in p2024 if (c['relative_wealth'] or -99)>0)
    stress_pos=sum(1 for c in stress2024 if (c['relative_wealth'] or -99)>0)
    medians={}
    for name,_,_ in PERIODS:
        vals=[c['excess_sharpe'] for c in cells if c['period']==name and c['excess_sharpe'] is not None]
        medians[name]=round(float(np.median(vals)),3) if vals else None
    passed=(positive>=12 and pos2024>=4 and stress_pos>=4 and all((v or -99)>0 for v in medians.values()))
    out={'candidate':'SMA_10M','downloaded_symbols':len(tables),'folds':folds,'cells':cells,'stress_2024_plus':stress2024,
         'positive_cells':positive,'positive_2024_folds':pos2024,'positive_stress_2024_folds':stress_pos,
         'median_excess_sharpe_by_period':medians,'stage3_status':'PASS' if passed else 'FAIL',
         'limitations':['current-survivor universe remains a bias','Yahoo data is not institutional point-in-time data','PASS would justify only higher-fidelity validation, not trading']}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
