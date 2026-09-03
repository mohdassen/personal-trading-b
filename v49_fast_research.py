from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import backtest_adaptive_momentum as am
import backtest_portfolio_momentum as pm
import v49_shadow as shadow
ROOT=Path(__file__).resolve().parent; OUT=ROOT/'data/v49_fast_research.json'

def _gates(r):
 return {'REGIME':bool(r.get('regime_ok')),'TREND':bool(r['close']>r['sma50']>r['sma100']>r['sma200']),'ABS_MOM':bool(r['mom12_1']>.05 and r['mom6_1']>.02),'REL_STRENGTH':bool(r['mom12_1']>r['spy_mom12_1'] and r['mom6_1']>r['spy_mom6_1'] and r['mom12_rank']>=.70 and r['mom6_rank']>=.60),'NEAR_52W':bool(.82<=r['near52']<=1.03),'NOT_CHASED':bool(-.10<=r['ret1m']<=.15),'LIQUID':bool(r['adv20']>=shadow.em.MIN_DOLLAR_VOLUME),'RISK':bool(.008<=r['atr_pct']<=.075 and r['vol20']<=.80),'LEADERSHIP':bool(r.get('leadership_health_ok'))}

def main():
 clock=datetime.now(timezone.utc); ny=shadow.now_ny(); universe=list(shadow.load_yaml(ROOT/'config/universe.yml').get('universe',[])); prices=shadow._download_prices(['SPY']+universe,ny); spy=prices['SPY']; completed=spy.index[-1]
 for s in universe:
  if completed not in prices[s].index: raise RuntimeError(f'alignment failure {s}')
 sf=shadow.em._spy_regime(spy); raw=[]
 for s in universe:
  r=shadow._row_at(s,prices[s],sf,completed)
  if r is None: raise RuntimeError(f'feature failure {s}')
  raw.append(r)
 rows=am._enrich_leadership(raw,prices); sh=shadow._sharia_policy(); groups=pm._group_map(); reasons=Counter(); gatefails=Counter(); near=[]
 for r in rows:
  s=str(r['symbol']); g=groups.get(s,f'UNGROUPED:{s}')
  if g in sh['excluded_groups'] or s in sh['excluded_symbols']: reasons['SHARIA_PRECHECK']+=1; continue
  failed=[k for k,v in _gates(r).items() if not v]; gatefails.update(failed)
  if failed: reason='BASE:'+ '+'.join(failed)
  elif int(r.get('score',0))<shadow.THRESHOLD: reason='SCORE'
  else:
   risk=2.5*max(float(r['atr']),.01)/max(float(r['close']),.01)*100
   reason='RISK_TOO_TIGHT' if risk<1.2 else ('RISK_TOO_WIDE' if risk>pm.MAX_TRADE_RISK_PCT else ('MARKET_VOL' if float(r.get('spy_vol20',9))>pm.MAX_SPY_VOL20_FOR_NEW_RISK else 'CANDIDATE'))
  reasons[reason]+=1; near.append({'symbol':s,'score':int(r.get('score',0)),'reason':reason,'failed_base_gates':failed,'mom12_rank':round(float(r.get('mom12_rank',0)),3),'mom6_rank':round(float(r.get('mom6_rank',0)),3)})
 candidates=shadow._candidate_rows(rows); near.sort(key=lambda x:(-len(x['failed_base_gates']),x['score'],x['mom12_rank']),reverse=True)
 history=shadow.load_json(OUT,{}).get('sessions',[]); history=[x for x in history if x.get('session')!=str(completed.date())]; history.append({'session':str(completed.date()),'captured_at':clock.isoformat(),'universe':len(universe),'candidate_count':len(candidates),'rejection_counts':dict(reasons),'base_gate_failure_counts':dict(gatefails.most_common()),'top_near_candidates':near[:12]}); history=history[-30:]
 last5=history[-5:]; cs=sum(1 for x in last5 if x.get('candidate_count',0)>0); tc=sum(x.get('candidate_count',0) for x in last5); cg=Counter()
 for x in last5: cg.update(x.get('base_gate_failure_counts',{}))
 status='LEARNING' if len(last5)<5 else ('TOO_INACTIVE_RESEARCH_REQUIRED' if tc==0 else ('LOW_ACTIVITY_WATCH' if cs<=1 else 'HEALTHY_ACTIVITY'))
 out={'engine':'V4.9-Fast-Research-Diagnostics','mode':'RESEARCH_ONLY_DO_NOT_TRADE','updated_at':clock.isoformat(),'status':status,'sessions_observed':len(history),'last5':{'sessions':len(last5),'candidate_sessions':cs,'total_candidates':tc,'dominant_bottleneck':cg.most_common(1)[0][0] if cg else None,'base_gate_failures':dict(cg.most_common())},'decision_rule':{'after_5_sessions_zero_candidates':'build and backtest separate challenger around measured bottleneck; never loosen frozen V4.9','healthy_activity':'keep V4.9 frozen'},'sessions':history}; shadow.save_json(OUT,out); print(json.dumps(out,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
