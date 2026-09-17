from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

OUT = Path('data/published_edge_benchmark.json')
START = '2014-01-01'
BASELINE_BPS = 30.0
STRESS_BPS = 60.0

PRIMARY = [
    'AAPL','MSFT','NVDA','AMD','AMZN','GOOGL','META','TSLA','AVGO','ORCL',
    'CRM','ADBE','NFLX','MU','QCOM','AMAT','LRCX','KLAC','CRWD','PANW','NOW',
    'PLTR','XOM','CVX','COP','SLB','ABBV','JNJ','LLY','UNH','CAT','DE','GE',
    'WMT','COST','HD','DIS','ROKU','TMUS','GILD'
]
INDEPENDENT = [
    'INTC','SNOW','DDOG','NET','MDB','SHOP','UBER','ABNB','BKNG','TGT','LOW',
    'NKE','SBUX','MCD','ISRG','VRTX','REGN','PFE','BA','HON','RTX','CMCSA',
    'SPOT','RBLX','MELI'
]
STRATEGIES = ['MOMENTUM_12_1', 'HIGH_52W', 'TSMOM_12M', 'SMA_10M']


def norm(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy()
    if isinstance(x.columns, pd.MultiIndex):
        if symbol in x.columns.get_level_values(-1):
            x = x.xs(symbol, axis=1, level=-1)
        else:
            x.columns = x.columns.get_level_values(0)
    need = ['Open','High','Low','Close','Volume']
    if not all(c in x.columns for c in need):
        return pd.DataFrame()
    x = x[need].dropna(subset=['Open','Close']).copy()
    idx = pd.DatetimeIndex(x.index)
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    x.index = idx
    return x.sort_index()


def download(symbol: str) -> pd.DataFrame:
    try:
        x = yf.download(symbol, start=START, interval='1d', auto_adjust=True,
                        progress=False, threads=False, timeout=30)
        return norm(x, symbol)
    except Exception as exc:
        print('DOWNLOAD_FAIL', symbol, repr(exc))
        return pd.DataFrame()


def month_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    x = df.copy()
    x['month'] = x.index.to_period('M')
    grouped = list(x.groupby('month', sort=True))
    rows = []
    for idx, (month, g) in enumerate(grouped):
        g = g.sort_index()
        row = {'month': month, 'close': float(g.Close.iloc[-1]), 'next_open': np.nan}
        if idx + 1 < len(grouped):
            ng = grouped[idx + 1][1].sort_index()
            if not ng.empty:
                row['next_open'] = float(ng.Open.iloc[0])
        rows.append(row)
    m = pd.DataFrame(rows).set_index('month').sort_index()
    m['mom_12_1'] = m['close'].shift(1) / m['close'].shift(12) - 1.0
    m['tsmom_12m'] = m['close'] / m['close'].shift(12) - 1.0
    m['high_52w'] = m['close'].shift(1) / m['close'].shift(1).rolling(12).max()
    m['sma10'] = m['close'].rolling(10).mean()
    return m


def build_tables(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {symbol: month_table(df) for symbol, df in raw.items()}


def build_panel(tables: dict[str, pd.DataFrame], universe: str) -> pd.DataFrame:
    rows = []
    for symbol, mt in tables.items():
        for month, r in mt.iterrows():
            rows.append({
                'universe': universe, 'symbol': symbol, 'month': month,
                'close': r['close'], 'next_open': r['next_open'],
                'mom_12_1': r['mom_12_1'], 'tsmom_12m': r['tsmom_12m'],
                'high_52w': r['high_52w'], 'sma10': r['sma10'],
            })
    return pd.DataFrame(rows)


def select(strategy: str, g: pd.DataFrame) -> list[str]:
    g = g.dropna(subset=['next_open']).copy()
    if strategy == 'MOMENTUM_12_1':
        g = g.dropna(subset=['mom_12_1'])
        if len(g) < 8: return []
        return g.nlargest(max(2, math.ceil(len(g)*0.20)), 'mom_12_1').symbol.tolist()
    if strategy == 'HIGH_52W':
        g = g.dropna(subset=['high_52w'])
        if len(g) < 8: return []
        return g.nlargest(max(2, math.ceil(len(g)*0.20)), 'high_52w').symbol.tolist()
    if strategy == 'TSMOM_12M':
        g = g.dropna(subset=['tsmom_12m'])
        return g[g.tsmom_12m > 0].symbol.tolist()
    if strategy == 'SMA_10M':
        g = g.dropna(subset=['sma10'])
        return g[g.close > g.sma10].symbol.tolist()
    if strategy == 'EQUAL_WEIGHT':
        return g.symbol.tolist()
    return []


def next_month_return(mt: pd.DataFrame, signal_month: pd.Period, cost_bps: float) -> float | None:
    if signal_month not in mt.index: return None
    loc = mt.index.get_loc(signal_month)
    if isinstance(loc, slice) or loc + 1 >= len(mt): return None
    entry = float(mt.iloc[loc]['next_open'])
    if not math.isfinite(entry) or entry <= 0: return None
    exit_close = float(mt.iloc[loc+1]['close'])
    return exit_close/entry - 1.0 - cost_bps/10000.0


def monthly_portfolio(tables: dict[str,pd.DataFrame], panel: pd.DataFrame,
                      strategy: str, cost_bps: float) -> tuple[pd.Series, dict[str,float]]:
    returns, symbol_pnl = {}, defaultdict(float)
    for month, g in panel.groupby('month', sort=True):
        rs = []
        for symbol in select(strategy, g):
            mt = tables.get(symbol)
            if mt is None: continue
            r = next_month_return(mt, month, cost_bps)
            if r is None: continue
            rs.append(r); symbol_pnl[symbol] += r
        if rs:
            returns[month.to_timestamp(how='end')] = float(np.mean(rs))
    return pd.Series(returns, dtype=float).sort_index(), dict(symbol_pnl)


def max_drawdown(s: pd.Series) -> float:
    if s.empty: return 0.0
    wealth = (1+s).cumprod(); peak = wealth.cummax()
    return float((1-wealth/peak).max())


def metrics(s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {'months':0,'annual_return':None,'sharpe':None,'max_drawdown':None,'win_months':None,'total_return':None}
    wealth = float((1+s).prod()); years = max(len(s)/12.0, 1/12.0)
    ann = wealth**(1/years)-1 if wealth > 0 else -1.0
    vol = float(s.std(ddof=1)); sharpe = float(s.mean()/vol*math.sqrt(12)) if vol > 1e-12 else None
    return {'months':len(s),'annual_return':round(ann,4),'sharpe':None if sharpe is None else round(sharpe,3),
            'max_drawdown':round(max_drawdown(s),4),'win_months':round(float((s>0).mean()),3),
            'total_return':round(wealth-1,4)}


def slice_period(s: pd.Series, start: str, end: str|None=None) -> pd.Series:
    x = s[s.index >= pd.Timestamp(start)]
    return x if not end else x[x.index <= pd.Timestamp(end)]


def concentration(symbol_pnl: dict[str,float]) -> float:
    pos = [v for v in symbol_pnl.values() if v > 0]; total = sum(pos)
    return 1.0 if total <= 0 else max(pos)/total


def relative_metrics(strategy: pd.Series, benchmark: pd.Series, start: str, end: str|None=None) -> dict:
    s = slice_period(strategy, start, end); b = slice_period(benchmark, start, end)
    both = pd.concat([s.rename('s'), b.rename('b')], axis=1).dropna()
    if both.empty:
        return {'months':0,'relative_wealth':None,'excess_sharpe':None,'mean_excess_monthly':None}
    sw = float((1+both.s).prod()); bw = float((1+both.b).prod())
    excess = both.s - both.b; vol = float(excess.std(ddof=1))
    es = float(excess.mean()/vol*math.sqrt(12)) if vol > 1e-12 else None
    return {'months':len(both), 'relative_wealth':round(sw/bw-1,4),
            'excess_sharpe':None if es is None else round(es,3),
            'mean_excess_monthly':round(float(excess.mean()),5)}


def eval_strategy(strategy: str, tp:dict[str,pd.DataFrame], ti:dict[str,pd.DataFrame],
                  pp:pd.DataFrame, pi:pd.DataFrame, bp30:pd.Series, bi30:pd.Series, bp60:pd.Series) -> dict:
    p30, pnl = monthly_portfolio(tp, pp, strategy, BASELINE_BPS)
    p60, _ = monthly_portfolio(tp, pp, strategy, STRESS_BPS)
    i30, _ = monthly_portfolio(ti, pi, strategy, BASELINE_BPS)

    mv = metrics(slice_period(p30,'2021-01-01','2023-12-31'))
    mh = metrics(slice_period(p30,'2024-01-01'))
    mi = metrics(slice_period(i30,'2024-01-01'))
    ms = metrics(slice_period(p60,'2024-01-01'))
    conc = concentration(pnl)
    stage1 = (mv['months']>=24 and mh['months']>=24 and mi['months']>=24
              and (mv['total_return'] or -99)>0 and (mh['total_return'] or -99)>0 and (mi['total_return'] or -99)>0
              and (mh['sharpe'] or -99)>0.50 and (mi['sharpe'] or -99)>0.25
              and (ms['total_return'] or -99)>0 and conc<=0.35)

    rv = relative_metrics(p30,bp30,'2021-01-01','2023-12-31')
    rh = relative_metrics(p30,bp30,'2024-01-01')
    ri = relative_metrics(i30,bi30,'2024-01-01')
    rs = relative_metrics(p60,bp60,'2024-01-01')
    stage2 = (stage1 and (rv['relative_wealth'] or -99)>0 and (rh['relative_wealth'] or -99)>0
              and (ri['relative_wealth'] or -99)>0 and (rh['excess_sharpe'] or -99)>0
              and (ri['excess_sharpe'] or -99)>0 and (rs['relative_wealth'] or -99)>0)

    return {
        'strategy':strategy,
        'status':'STAGE2_CANDIDATE' if stage2 else ('STAGE1_ONLY' if stage1 else 'REJECT'),
        'validation_2021_2023':mv, 'holdout_2024_plus':mh, 'independent_2024_plus':mi,
        'stress_60bps_2024_plus':ms, 'largest_positive_symbol_pnl_share':round(conc,3),
        'relative_vs_equal_weight':{'validation':rv,'holdout':rh,'independent_holdout':ri,'stress_holdout':rs},
    }


def main():
    raw_p, raw_i = {}, {}
    items = [(s,'primary') for s in PRIMARY] + [(s,'independent') for s in INDEPENDENT]
    for idx,(symbol,univ) in enumerate(items,1):
        df = download(symbol); print(f'[{idx}/{len(items)}] {symbol}: rows={len(df)}')
        if len(df) >= 500: (raw_p if univ=='primary' else raw_i)[symbol] = df

    tp, ti = build_tables(raw_p), build_tables(raw_i)
    pp, pi = build_panel(tp,'primary'), build_panel(ti,'independent')
    bp30,_ = monthly_portfolio(tp,pp,'EQUAL_WEIGHT',BASELINE_BPS)
    bi30,_ = monthly_portfolio(ti,pi,'EQUAL_WEIGHT',BASELINE_BPS)
    bp60,_ = monthly_portfolio(tp,pp,'EQUAL_WEIGHT',STRESS_BPS)

    results = [eval_strategy(s,tp,ti,pp,pi,bp30,bi30,bp60) for s in STRATEGIES]
    candidates = [r['strategy'] for r in results if r['status']=='STAGE2_CANDIDATE']
    out = {
        'method':'published-rule benchmark; fixed rules; next-open monthly fills; long-only; equal-weight relative Stage 2',
        'baseline_roundtrip_bps':BASELINE_BPS,'stress_roundtrip_bps':STRESS_BPS,
        'primary_symbols_downloaded':len(raw_p),'independent_symbols_downloaded':len(raw_i),
        'equal_weight_benchmark':{
            'primary_validation':metrics(slice_period(bp30,'2021-01-01','2023-12-31')),
            'primary_holdout':metrics(slice_period(bp30,'2024-01-01')),
            'independent_holdout':metrics(slice_period(bi30,'2024-01-01')),
        },
        'results':results,'stage2_candidates':candidates,
        'overall':'STAGE2_CANDIDATE_FOUND' if candidates else 'NO_STAGE2_CANDIDATE',
        'limitations':['current-stock universe has survivorship/selection bias','Yahoo is not institutional point-in-time constituent data',
                       'Stage 2 is research only; no paper/live authorization']
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,indent=2))

if __name__=='__main__': main()
