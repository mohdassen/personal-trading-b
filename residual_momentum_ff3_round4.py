from __future__ import annotations
import io, json, math, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import yfinance as yf

OUT=Path("data/residual_momentum_ff3_round4.json")
START="2012-01-01"
BASELINE_BPS=30.0
STRESS_BPS=60.0
FF_URL="https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_CSV.zip"
SYMBOLS=[
"AAPL","MSFT","NVDA","AMD","AMZN","GOOGL","META","TSLA","AVGO","ORCL","CRM","ADBE","NFLX","MU","QCOM","AMAT","LRCX","KLAC","CRWD","PANW","NOW","PLTR","XOM","CVX","COP","SLB","ABBV","JNJ","LLY","UNH","CAT","DE","GE","WMT","COST","HD","DIS","ROKU","TMUS","GILD","INTC","SNOW","DDOG","NET","MDB","SHOP","UBER","ABNB","BKNG","TGT","LOW","NKE","SBUX","MCD","ISRG","VRTX","REGN","PFE","BA","HON","RTX","CMCSA","SPOT","RBLX","MELI"]

def factor_data():
    r=requests.get(FF_URL,timeout=30); r.raise_for_status()
    z=zipfile.ZipFile(io.BytesIO(r.content)); name=z.namelist()[0]
    txt=z.read(name).decode("utf-8",errors="ignore")
    rows=[]
    for line in txt.splitlines():
        parts=[p.strip() for p in line.split(",")]
        if len(parts)<5: continue
        key=parts[0]
        if len(key)==6 and key.isdigit():
            try:
                rows.append((pd.Period(key[:4]+"-"+key[4:],freq="M"),*(float(x)/100.0 for x in parts[1:5])))
            except Exception: pass
    if not rows: raise RuntimeError("No FF3 rows parsed")
    return pd.DataFrame(rows,columns=["month","MKT_RF","SMB","HML","RF"]).set_index("month").sort_index()

def norm(raw,s):
    if raw is None or raw.empty:return pd.DataFrame()
    try:
        if isinstance(raw.columns,pd.MultiIndex):
            if s in raw.columns.get_level_values(0):x=raw[s].copy()
            elif s in raw.columns.get_level_values(-1):x=raw.xs(s,axis=1,level=-1).copy()
            else:return pd.DataFrame()
        else:x=raw.copy()
        if not all(c in x.columns for c in ["Open","Close"]):return pd.DataFrame()
        x=x[["Open","Close"]].dropna().copy(); idx=pd.DatetimeIndex(x.index)
        if idx.tz is not None:idx=idx.tz_convert(None)
        x.index=idx;return x.sort_index()
    except Exception:return pd.DataFrame()

def download():
    raw=yf.download(SYMBOLS,start=START,interval="1d",auto_adjust=True,progress=False,threads=True,group_by="ticker",timeout=30)
    out={}
    for s in SYMBOLS:
        x=norm(raw,s)
        if len(x)>=500:out[s]=x
        print(f"{s}: rows={len(x)}")
    return out

def mt(df):
    x=df.copy();x["month"]=x.index.to_period("M");groups=list(x.groupby("month",sort=True));rows=[]
    for i,(m,g) in enumerate(groups):
        g=g.sort_index()
        rows.append({"month":m,"close":float(g.Close.iloc[-1]),"next_open":float(groups[i+1][1].sort_index().Open.iloc[0]) if i+1<len(groups) else np.nan})
    t=pd.DataFrame(rows).set_index("month").sort_index();t["ret"]=t.close.pct_change();return t

def add_score(t,ff):
    idx=t.index.intersection(ff.index);score=pd.Series(index=t.index,dtype=float)
    er=(t.loc[idx,"ret"]-ff.loc[idx,"RF"]).astype(float)
    for pos,m in enumerate(idx):
        if pos<36:continue
        hist_idx=idx[pos-36:pos+1]
        # Fit with data known through current signal month.
        h=pd.DataFrame({"y":er.reindex(hist_idx),"mkt":ff.MKT_RF.reindex(hist_idx),"smb":ff.SMB.reindex(hist_idx),"hml":ff.HML.reindex(hist_idx)}).dropna()
        if len(h)<30:continue
        X=np.column_stack([np.ones(len(h)),h.mkt.values,h.smb.values,h.hml.values])
        beta,*_=np.linalg.lstsq(X,h.y.values,rcond=None)
        # Score on previous 12 months excluding the most recent completed month.
        score_idx=idx[max(0,pos-12):pos-1]
        q=pd.DataFrame({"y":er.reindex(score_idx),"mkt":ff.MKT_RF.reindex(score_idx),"smb":ff.SMB.reindex(score_idx),"hml":ff.HML.reindex(score_idx)}).dropna()
        if len(q)<9:continue
        Xq=np.column_stack([np.ones(len(q)),q.mkt.values,q.smb.values,q.hml.values])
        eps=q.y.values-Xq.dot(beta)
        sd=float(np.std(eps,ddof=1))
        if sd>1e-12:score.loc[m]=float(np.sum(eps)/sd)
    t=t.copy();t["score"]=score;return t

def nextret(t,m,bps):
    if m not in t.index:return None
    i=t.index.get_loc(m)
    if isinstance(i,slice) or i+1>=len(t):return None
    e=float(t.iloc[i].next_open)
    if not math.isfinite(e) or e<=0:return None
    return float(t.iloc[i+1].close)/e-1-bps/10000.0

def portfolio(tables,symbols,bps,active):
    months=sorted(set().union(*[set(tables[s].index) for s in symbols if s in tables]))
    out={}
    for m in months:
        q=[]
        for s in symbols:
            t=tables.get(s)
            if t is None or m not in t.index:continue
            sc=t.loc[m,"score"]
            if pd.notna(sc) and pd.notna(t.loc[m,"next_open"]):q.append((s,float(sc)))
        if len(q)<5:continue
        if active:
            n=max(2,math.ceil(len(q)*0.20)); picked=[s for s,_ in sorted(q,key=lambda x:x[1],reverse=True)[:n]]
        else:
            picked=[s for s,_ in q]
        rs=[nextret(tables[s],m,bps) for s in picked];rs=[r for r in rs if r is not None]
        if rs:out[m.to_timestamp(how="end")]=float(np.mean(rs))
    return pd.Series(out,dtype=float).sort_index()

def rel(s,b,start,end=None):
    x=pd.concat([s.rename("s"),b.rename("b")],axis=1).dropna();x=x[x.index>=pd.Timestamp(start)]
    if end:x=x[x.index<=pd.Timestamp(end)]
    if x.empty:return {"months":0,"relative_wealth":None,"excess_sharpe":None}
    ex=x.s-x.b;sd=float(ex.std(ddof=1));sh=float(ex.mean()/sd*math.sqrt(12)) if sd>1e-12 else None
    rw=float(((1+x.s)/(1+x.b)).prod()-1)
    return {"months":len(x),"relative_wealth":round(rw,4),"excess_sharpe":None if sh is None else round(sh,3)}

def folds(symbols,k=5):
    o=sorted(symbols);return [set(o[i::k]) for i in range(k)]

def main():
    ff=factor_data();print("FF rows",len(ff),ff.index.min(),ff.index.max())
    raw=download();tables={s:add_score(mt(raw[s]),ff) for s in SYMBOLS if s in raw};available=list(tables)
    periods=[("2016_2020","2016-01-01","2020-12-31"),("2021_2023","2021-01-01","2023-12-31"),("2024_PLUS","2024-01-01",None)]
    cells=[];stress=[]
    for fi,syms in enumerate(folds(available)):
        s30=portfolio(tables,syms,BASELINE_BPS,True);b30=portfolio(tables,syms,BASELINE_BPS,False)
        s60=portfolio(tables,syms,STRESS_BPS,True);b60=portfolio(tables,syms,STRESS_BPS,False)
        for n,a,e in periods:cells.append({"fold":fi,"period":n,**rel(s30,b30,a,e)})
        stress.append({"fold":fi,**rel(s60,b60,"2024-01-01",None)})
    pos=sum(1 for c in cells if (c["relative_wealth"] or -99)>0)
    recent=[c for c in cells if c["period"]=="2024_PLUS"];pr=sum(1 for c in recent if (c["relative_wealth"] or -99)>0)
    ps=sum(1 for c in stress if (c["relative_wealth"] or -99)>0)
    med={}
    for n,_,_ in periods:
        v=[c["excess_sharpe"] for c in cells if c["period"]==n and c["excess_sharpe"] is not None]
        med[n]=None if not v else round(float(np.median(v)),3)
    passed=(pos>=11 and pr>=4 and ps>=4 and all(med[n] is not None and med[n]>0 for n,_,_ in periods) and med["2024_PLUS"]>=0.25)
    out={"strategy":"RESIDUAL_MOMENTUM_FF3","status":"ROUND4_ROBUST_CANDIDATE" if passed else "REJECT","positive_cells":pos,"positive_2024_folds":pr,"positive_stress_2024_folds":ps,"median_excess_sharpe_by_period":med,"cells":cells,"stress_2024_plus":stress,"downloaded_symbols":len(available),"factor_last_month":str(ff.index.max()),"limitations":["current-survivor symbol universe remains biased","long-only top quintile is an adaptation of the published long-short residual momentum strategy","candidate would still require point-in-time universe and forward validation"]}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,indent=2),encoding="utf-8");print(json.dumps({k:out[k] for k in ["strategy","status","positive_cells","positive_2024_folds","positive_stress_2024_folds","median_excess_sharpe_by_period","downloaded_symbols","factor_last_month"]},indent=2))

if __name__=="__main__":main()
