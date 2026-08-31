from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yaml

import backtest_evidence_momentum as em

ROOT = Path(__file__).resolve().parent
ENGINE = 'V4.8-Adaptive-Momentum-Leadership'
SCORE_GRID = em.SCORE_GRID


def _enrich_leadership(rows, prices):
    enriched=[]
    for r in rows:
        try:
            df=prices[r['symbol']]; i=int(r['i'])
            x=dict(r)
            x['ret20_stock']=float(df['Close'].iloc[i]/df['Close'].iloc[i-20]-1.0) if i>=20 else 0.0
            x['above_sma50']=bool(float(r['close'])>float(r['sma50']))
            enriched.append(x)
        except Exception:
            continue
    ranked=em._rank_and_qualify(enriched)
    if not ranked:return []
    d=pd.DataFrame(ranked)
    health={}
    for date,g in d.groupby('date'):
        leaders=g[g['mom12_rank']>=0.70]
        if len(leaders)<5:
            health[date]={'ok':False,'leader_ret20':0.0,'spread':0.0,'breadth':0.0}
            continue
        leader_ret=float(leaders['ret20_stock'].median())
        universe_ret=float(g['ret20_stock'].median())
        spread=leader_ret-universe_ret
        breadth=float(leaders['above_sma50'].mean())
        # Factor-specific health: pause when past winners themselves are rolling over,
        # even if SPY remains in a broad bull trend. This targets momentum rotations.
        ok=bool(leader_ret>-0.02 and spread>-0.015 and breadth>=0.60)
        health[date]={'ok':ok,'leader_ret20':leader_ret,'spread':spread,'breadth':breadth}
    out=[]
    for r in ranked:
        h=health.get(r['date'],{'ok':False,'leader_ret20':0.0,'spread':0.0,'breadth':0.0})
        x=dict(r)
        x['leadership_health_ok']=bool(h['ok'])
        x['leader_ret20']=round(float(h['leader_ret20']),4)
        x['leadership_spread20']=round(float(h['spread']),4)
        x['leadership_breadth50']=round(float(h['breadth']),3)
        x['base_ok']=bool(x.get('base_ok') and h['ok'])
        if h['ok'] and h['spread']>0.02:
            x['score']=min(100,int(x['score'])+3)
        out.append(x)
    return out


def _validate_locked(rows,prices,cost_bps):
    all_by={s:em._trades(rows,prices,cost_bps,s) for s in SCORE_GRID}
    ref=all_by[min(SCORE_GRID)]
    dates=sorted(set(x['signal_date'] for x in ref))
    if len(dates)<30:
        return {'status':'INSUFFICIENT_DATA','safe_for_shadow':False,'robust_candidate_count':0}
    cut=dates[max(1,int(len(dates)*0.85))-1]
    folds=((0.00,0.35),(0.20,0.55),(0.40,0.75),(0.60,1.00))
    candidates=[]; fold_report={}
    for threshold,trs in all_by.items():
        selection=[x for x in trs if x['signal_date']<=cut]
        fs=[em._stats(em._date_slice(selection,a,b)) for a,b in folds]
        fold_report[str(threshold)]=fs
        useful=[x for x in fs if int(x['samples'])>=8]
        if len(useful)<3:continue
        exps=[float(x['expectancy_r']) for x in useful]
        pfs=[float(x['profit_factor_r']) for x in useful]
        pos=sum(e>0 for e in exps)/len(exps)
        med=float(pd.Series(exps).median()); worst=min(exps); medpf=float(pd.Series(pfs).median())
        if pos>=0.75 and med>0.05 and worst>-0.10 and medpf>=1.15 and sum(int(x['samples']) for x in useful)>=50:
            candidates.append({'score':threshold,'positive_fold_ratio':round(pos,3),'median_fold_expectancy_r':round(med,3),'worst_fold_expectancy_r':round(worst,3),'median_fold_profit_factor':round(medpf,2),'fold_samples':[int(x['samples']) for x in fs],'folds':fs})
    passing={x['score'] for x in candidates}
    robust=[x for x in candidates if x['score']-5 in passing or x['score']+5 in passing]
    robust.sort(key=lambda x:(x['median_fold_expectancy_r'],x['worst_fold_expectancy_r'],sum(x['fold_samples'])),reverse=True)
    selected=robust[0] if robust else None
    if selected:
        holdout=[x for x in all_by[selected['score']] if x['signal_date']>cut]
        hs=em._stats(holdout)
        halves=[em._stats(em._date_slice(holdout,0,0.5)),em._stats(em._date_slice(holdout,0.5,1.0))]
        halves_ok=all(int(x['samples'])>=6 and float(x['expectancy_r'])>-0.10 for x in halves)
    else:
        holdout=[];hs=em._stats([]);halves=[em._stats([]),em._stats([])];halves_ok=False
    safe=bool(selected and hs['samples']>=15 and hs['expectancy_r']>0.05 and hs['profit_factor_r']>=1.15 and hs['max_drawdown_r']<=8.0 and halves_ok)
    return {'status':'PASS' if safe else ('HOLDOUT_FAIL' if selected else 'NO_ROBUST_REGION'),'selection_end_date':cut,'robust_candidate_count':len(robust),'selected':selected,'final_untouched_holdout':hs,'holdout_halves':halves,'safe_for_shadow':safe,'acceptance_gate':{'holdout_min_samples':15,'holdout_min_expectancy_r':0.05,'holdout_min_profit_factor':1.15,'holdout_max_drawdown_r':8.0,'each_half_min_samples':6,'each_half_min_expectancy_r':-0.10},'threshold_overall':{str(s):em._stats(v) for s,v in all_by.items()},'fold_report':fold_report,'selected_holdout_samples':holdout[-500:]}


def main():
    cfg=yaml.safe_load((ROOT/'config/settings.yml').read_text())['settings']
    universe=yaml.safe_load((ROOT/'config/universe.yml').read_text())['universe']
    cost=float(cfg.get('backtest_transaction_cost_bps',10))
    symbols=list(dict.fromkeys(['SPY']+list(universe)));prices={}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs={pool.submit(em._download,s):s for s in symbols}
        for f in as_completed(futs):
            s,df=f.result()
            if not df.empty:prices[s]=df
    if 'SPY' not in prices:raise RuntimeError('SPY market data unavailable')
    spy_f=em._spy_regime(prices['SPY']);raw=[]
    for symbol in universe:
        if symbol in prices:raw.extend(em._signal_rows(symbol,prices[symbol],spy_f))
    rows=_enrich_leadership(raw,prices)
    validation=_validate_locked(rows,prices,cost)
    health_dates=sorted(set(x['date'] for x in rows))
    latest_date=health_dates[-1] if health_dates else None
    latest=[x for x in rows if x['date']==latest_date] if latest_date else []
    latest_health={'date':latest_date,'ok':bool(latest and latest[0].get('leadership_health_ok')),'leader_ret20':latest[0].get('leader_ret20') if latest else None,'spread20':latest[0].get('leadership_spread20') if latest else None,'breadth50':latest[0].get('leadership_breadth50') if latest else None}
    out={'engine':ENGINE,'method':'V4.7 evidence-based medium-horizon cross-sectional momentum plus factor-specific leadership-health gate. The gate requires the top momentum cohort to avoid a 20-day rollover, avoid material underperformance versus the universe, and retain >=60% breadth above SMA50. Final 15% calendar history is untouched until selection is complete.','years_requested':em.YEARS,'holding_days_max':em.HOLD_DAYS,'transaction_cost_bps_per_side':cost,'universe_size':len(universe),'symbols_with_data':len([s for s in universe if s in prices]),'weekly_feature_rows':len(rows),'base_qualified_signals':sum(bool(x.get('base_ok')) for x in rows),'latest_momentum_health':latest_health,'validation':validation}
    (ROOT/'data/backtest.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    printable={**out,'validation':{k:v for k,v in validation.items() if k not in ('selected_holdout_samples','fold_report')}}
    print(json.dumps(printable,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
