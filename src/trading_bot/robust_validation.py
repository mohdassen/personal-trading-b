from __future__ import annotations

from statistics import median

SCORE_GRID=(70,75,80,82,85,87,88,90,92,95)
RR_GRID=(1.2,1.4,1.5,1.7,1.9,2.0)


def _eligible(x,score,rr):
    return bool(x.get('qualification_passed')) and int(x['score'])>=score and float(x['rr1'])>=rr


def _stats(rows):
    if not rows:
        return {'samples':0,'win_rate':0.0,'expectancy_r':0.0,'profit_factor_r':0.0,'max_drawdown_r':0.0}
    rs=[float(x['r_multiple']) for x in sorted(rows,key=lambda z:z['timestamp'])]
    wins=[r for r in rs if r>0];losses=[r for r in rs if r<=0]
    gp=sum(wins);gl=abs(sum(losses));eq=0.0;peak=0.0;dd=0.0
    for r in rs:
        eq+=r;peak=max(peak,eq);dd=max(dd,peak-eq)
    return {
        'samples':len(rs),
        'win_rate':round(len(wins)/len(rs)*100,1),
        'expectancy_r':round(sum(rs)/len(rs),3),
        'profit_factor_r':round(gp/gl,2) if gl else (999.0 if gp else 0.0),
        'max_drawdown_r':round(dd,3),
    }


def _view(rows,score,rr):
    return _stats([x for x in rows if _eligible(x,score,rr)])


def _neighbor_keys(score,rr):
    si=SCORE_GRID.index(score);ri=RR_GRID.index(rr);out=[]
    for ds,dr in ((-1,0),(1,0),(0,-1),(0,1)):
        a=si+ds;b=ri+dr
        if 0<=a<len(SCORE_GRID) and 0<=b<len(RR_GRID):out.append((SCORE_GRID[a],RR_GRID[b]))
    return out


def _date_split(rows,holdout_fraction=.15,folds=4):
    dates=sorted(set(x['date'] for x in rows))
    if len(dates)<40:return [],[],[]
    hold_n=max(10,int(len(dates)*holdout_fraction));hold_dates=set(dates[-hold_n:]);pre=[d for d in dates if d not in hold_dates]
    initial=max(20,int(len(pre)*.45));remaining=len(pre)-initial;test_n=max(5,remaining//folds)
    windows=[]
    for i in range(folds):
        test_start=initial+i*test_n;test_end=len(pre) if i==folds-1 else min(len(pre),test_start+test_n)
        if test_start>=len(pre) or test_end<=test_start:continue
        train_dates=set(pre[:test_start]);test_dates=set(pre[test_start:test_end])
        windows.append((train_dates,test_dates))
    return windows,set(pre),hold_dates


def evaluate(rows):
    windows,pre_dates,hold_dates=_date_split(rows)
    if not windows:
        return {'status':'INSUFFICIENT_HISTORY','reason':'not enough unique chronological dates for robust validation'}

    parameter_results=[]
    for score in SCORE_GRID:
        for rr in RR_GRID:
            fold_stats=[]
            for _,test_dates in windows:
                test=[x for x in rows if x['date'] in test_dates]
                fold_stats.append(_view(test,score,rr))
            agg=[x for x in rows if x['date'] in pre_dates]
            agg_stats=_view(agg,score,rr)
            valid=[f for f in fold_stats if f['samples']>0]
            positive=sum(f['expectancy_r']>0 and f['profit_factor_r']>1 for f in valid)
            positive_ratio=(positive/len(valid)) if valid else 0.0
            worst=min((f['expectancy_r'] for f in valid),default=-999.0)
            med=median([f['expectancy_r'] for f in valid]) if valid else -999.0
            parameter_results.append({
                'score':score,'min_rr':rr,
                'folds':fold_stats,
                'positive_fold_ratio':round(positive_ratio,3),
                'median_fold_expectancy_r':round(med,3),
                'worst_fold_expectancy_r':round(worst,3),
                'pre_holdout':agg_stats,
            })

    by_key={(x['score'],x['min_rr']):x for x in parameter_results}
    candidates=[]
    for x in parameter_results:
        p=x['pre_holdout'];neighbor_pass=0
        for k in _neighbor_keys(x['score'],x['min_rr']):
            n=by_key[k];np=n['pre_holdout']
            if np['samples']>=20 and np['expectancy_r']>0 and np['profit_factor_r']>1.05 and n['positive_fold_ratio']>=0.5:
                neighbor_pass+=1
        robust=(
            p['samples']>=30 and p['expectancy_r']>=0.05 and p['profit_factor_r']>=1.15 and
            x['positive_fold_ratio']>=0.6 and x['median_fold_expectancy_r']>0 and
            x['worst_fold_expectancy_r']>-0.35 and neighbor_pass>=2
        )
        if robust:
            quality=(p['expectancy_r']*2)+(min(p['profit_factor_r'],3)-1)+x['median_fold_expectancy_r']-(p['max_drawdown_r']/20)+(neighbor_pass*.05)
            candidates.append({**x,'neighbor_support':neighbor_pass,'robust_quality':round(quality,3)})

    candidates=sorted(candidates,key=lambda x:(x['robust_quality'],x['pre_holdout']['samples']),reverse=True)
    selected=candidates[0] if candidates else None
    holdout=[x for x in rows if x['date'] in hold_dates]
    holdout_stats=_view(holdout,selected['score'],selected['min_rr']) if selected else _stats([])
    holdout_pass=bool(selected and holdout_stats['samples']>=8 and holdout_stats['expectancy_r']>0 and holdout_stats['profit_factor_r']>1.0)

    return {
        'status':'PASS' if holdout_pass else ('NO_ROBUST_REGION' if not selected else 'HOLDOUT_FAIL'),
        'method':'multi-window chronological validation with untouched final holdout and neighborhood stability requirement',
        'fold_count':len(windows),
        'holdout_dates':{'count':len(hold_dates),'first':min(hold_dates) if hold_dates else None,'last':max(hold_dates) if hold_dates else None},
        'robust_candidate_count':len(candidates),
        'selected_parameters':None if not selected else {'score':selected['score'],'min_rr':selected['min_rr'],'neighbor_support':selected['neighbor_support'],'pre_holdout':selected['pre_holdout'],'positive_fold_ratio':selected['positive_fold_ratio'],'median_fold_expectancy_r':selected['median_fold_expectancy_r'],'worst_fold_expectancy_r':selected['worst_fold_expectancy_r']},
        'final_holdout':holdout_stats,
        'safe_for_shadow':holdout_pass,
        'top_robust_candidates':[{k:v for k,v in x.items() if k!='folds'} for x in candidates[:10]],
    }
