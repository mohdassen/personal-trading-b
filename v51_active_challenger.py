"""V5.1 research-only active challenger.

Purpose: test a higher-activity hypothesis without modifying frozen V4.9.
This is NOT a live/paper execution engine.

Hypothesis:
- retain market regime, liquidity, risk and portfolio concentration controls;
- require strong cross-sectional momentum;
- allow either classic perfect trend OR a controlled pullback inside a healthy long-term uptrend;
- replace V4.9's all-or-nothing near-52w/not-chased conjunction with a scored entry-quality gate.

Any adoption requires portfolio-aware walk-forward + untouched holdout validation.
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import yaml
import backtest_evidence_momentum as em
import backtest_adaptive_momentum as am
import backtest_portfolio_momentum as pm

ROOT=Path(__file__).resolve().parent
ENGINE='V5.1-Active-Challenger-Research'
SCORE_GRID=(60,65,70,75)

def qualify(rows):
 out=[]
 for r in rows:
  x=dict(r)
  long_trend=bool(r['close']>r['sma200'] and r['sma50']>r['sma200'])
  perfect_trend=bool(r['close']>r['sma50']>r['sma100']>r['sma200'])
  pullback=bool(long_trend and r['close']>=0.96*r['sma50'] and r['ret1m']<=0.10)
  momentum=bool(r['mom12_1']>0.05 and r['mom6_1']>0.01 and r['mom12_rank']>=0.75 and r['mom6_rank']>=0.60)
  rs=bool(r['mom12_1']>r['spy_mom12_1'] and r['mom6_1']>r['spy_mom6_1'])
  entry_quality=bool((0.78<=r['near52']<=1.03) and (-0.12<=r['ret1m']<=0.12))
  liquid=bool(r['adv20']>=em.MIN_DOLLAR_VOLUME)
  risk=bool(.008<=r['atr_pct']<=.075 and r['vol20']<=.80)
  x['base_ok']=bool(r['regime_ok'] and (perfect_trend or pullback) and momentum and rs and entry_quality and liquid and risk and r.get('leadership_health_ok'))
  # Re-score for this hypothesis; score is ranking, never a substitute for hard risk gates.
  score=0
  score+=20 if perfect_trend else (12 if pullback else 0)
  score+=20*min(1,max(0,(r['mom12_rank']-.60)/.40))
  score+=15*min(1,max(0,(r['mom6_rank']-.55)/.45))
  score+=15*min(1,max(0,(r['mom12_1']-.05)/.45))
  score+=10*min(1,max(0,(r['mom6_1']-.01)/.25))
  score+=10 if rs else 0
  score+=5 if entry_quality else 0
  score+=5 if r.get('leadership_health_ok') else 0
  x['score']=int(round(min(100,score)))
  out.append(x)
 return out

def main():
 cfg=yaml.safe_load((ROOT/'config/settings.yml').read_text())['settings']; universe=yaml.safe_load((ROOT/'config/universe.yml').read_text())['universe']; cost=float(cfg.get('backtest_transaction_cost_bps',10))
 prices=pm._load_complete_prices(list(dict.fromkeys(['SPY']+list(universe))))
 sf=em._spy_regime(prices['SPY']); raw=[]
 for s in universe: raw.extend(em._signal_rows(s,prices[s],sf))
 rows=qualify(am._enrich_leadership(raw,prices))
 # Use existing portfolio-aware simulator/validator by temporarily supplying challenger thresholds.
 original=pm.SCORE_GRID
 try:
  pm.SCORE_GRID=SCORE_GRID
  validation=pm._validate_locked(rows,prices,cost)
 finally: pm.SCORE_GRID=original
 out={'engine':ENGINE,'mode':'RESEARCH_ONLY_DO_NOT_TRADE','hypothesis':'controlled pullback or perfect trend + strong relative momentum; V4.9 remains frozen','universe_size':len(universe),'feature_rows':len(rows),'base_qualified_signals':sum(bool(x.get('base_ok')) for x in rows),'validation':validation}
 (ROOT/'data/v51_challenger.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({**out,'validation':{k:v for k,v in validation.items() if k not in ('selected_holdout_samples','fold_report')}},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
