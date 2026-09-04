"""Stress tests for V5.1 challenger. Research-only; never live."""
from __future__ import annotations
import json
from pathlib import Path
import yaml
import pandas as pd
import backtest_evidence_momentum as em
import backtest_adaptive_momentum as am
import backtest_portfolio_momentum as pm
import v51_active_challenger as v51

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data/v51_stress_test.json'
COSTS=(10,20,35,50)
THRESHOLD=60

def stats(rows): return em._stats(rows)

def main():
 cfg=yaml.safe_load((ROOT/'config/settings.yml').read_text())['settings']
 universe=yaml.safe_load((ROOT/'config/universe.yml').read_text())['universe']
 sh=yaml.safe_load((ROOT/'config/sharia_shadow.yml').read_text()) or {}
 excluded_groups=set(str(x) for x in ((sh.get('policy') or {}).get('excluded_groups') or []))
 excluded_symbols=set(str(x) for x in ((sh.get('policy') or {}).get('excluded_symbols') or []))
 prices=pm._load_complete_prices(list(dict.fromkeys(['SPY']+list(universe))))
 sf=em._spy_regime(prices['SPY']); raw=[]
 for s in universe: raw.extend(em._signal_rows(s,prices[s],sf))
 rows=v51.qualify(am._enrich_leadership(raw,prices))
 groups=pm._group_map()
 sharia_rows=[r for r in rows if groups.get(str(r['symbol']),f'UNGROUPED:{r["symbol"]}') not in excluded_groups and str(r['symbol']) not in excluded_symbols]
 cost_results={}
 for c in COSTS:
  trades=pm._trades(rows,prices,float(c),THRESHOLD)
  cost_results[str(c)]={'samples':len(trades),'stats':stats(trades)}
 sharia_trades=pm._trades(sharia_rows,prices,float(cfg.get('backtest_transaction_cost_bps',10)),THRESHOLD)
 # Regime buckets from SPY realized volatility on each signal date.
 buckets={'low_vol':[],'mid_vol':[],'high_vol':[]}
 for t in pm._trades(rows,prices,float(cfg.get('backtest_transaction_cost_bps',10)),THRESHOLD):
  v=float(t.get('spy_vol20',0))
  key='low_vol' if v<0.16 else ('mid_vol' if v<0.22 else 'high_vol')
  buckets[key].append(t)
 regime={k:{'samples':len(v),'stats':stats(v)} for k,v in buckets.items()}
 out={
  'engine':'V5.1-Stress-Test','mode':'RESEARCH_ONLY_DO_NOT_TRADE','threshold':THRESHOLD,
  'cost_bps_per_side':cost_results,
  'regime_buckets':regime,
  'sharia_precheck_only':{
    'certified':False,'samples':len(sharia_trades),'stats':stats(sharia_trades),
    'note':'Conservative sector/symbol precheck only; not formal Sharia certification.'},
 }
 # Conservative pass criteria: remains positive under 50bps/side and Sharia subset remains positive.
 out['stress_pass']=bool(cost_results['50']['stats']['expectancy_r']>0 and cost_results['50']['stats']['profit_factor_r']>1 and out['sharia_precheck_only']['stats']['expectancy_r']>0 and out['sharia_precheck_only']['stats']['profit_factor_r']>1)
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
 print(json.dumps(out,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
