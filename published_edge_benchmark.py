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
        row = {
            'month': month,
            'close': float(g.Close.iloc[-1]),
            'next_open': np.nan,
        }
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
                'universe': universe,
                'symbol': symbol,
                'month': month,
                'close': r['close'],
                'next_open': r['next_open'],
                'mom_12_1': r['mom_12_1'],
                'tsmom_12m': r['tsmom_12m'],
                'high_52w': r['high_52w'],
                'sma10': r['sma10'],
            })
    return pd.DataFrame(rows)


def select(strategy: str, g: pd.DataFrame) -> list[str]:
    g = g.dropna(subset=['next_open']).copy()
    if strategy == 'MOMENTUM_12_1':
        g = g.dropna(subset=['mom_12_1'])
        if len(g) < 8:
            return []
        n = max(2, math.ceil(len(g) * 0.20))
        return g.nlargest(n, 'mom_12_1').symbol.tolist()
    if strategy == 'HIGH_52W':
        g = g.dropna(subset=['high_52w'])
        if len(g) < 8:
            return []
        n = max(2, math.ceil(len(g) * 0.20))
        return g.nlargest(n, 'high_52w').symbol.tolist()
    if strategy == 'TSMOM_12M':
        g = g.dropna(subset=['tsmom_12m'])
        return g[g.tsmom_12m > 0].symbol.tolist()
    if strategy == 'SMA_10M':
        g = g.dropna(subset=['sma10'])
        return g[g.close > g.sma10].symbol.tolist()
    return []


def next_month_return(mt: pd.DataFrame, signal_month: pd.Period, cost_bps: float) -> float | None:
    if signal_month not in mt.index:
        return None
    loc = mt.index.get_loc(signal_month)
    if isinstance(loc, slice) or loc + 1 >= len(mt):
        return None
    entry = float(mt.iloc[loc]['next_open'])
    if not math.isfinite(entry) or entry <= 0:
        return None
    exit_close = float(mt.iloc[loc + 1]['close'])
    gross = exit_close / entry - 1.0
    return gross - cost_bps / 10000.0


def monthly_portfolio(tables: dict[str, pd.DataFrame], panel: pd.DataFrame,
                      strategy: str, cost_bps: float) -> tuple[pd.Series, dict[str, float]]:
    returns = {}
    symbol_pnl = defaultdict(float)
    for month, g in panel.groupby('month', sort=True):
        picked = select(strategy, g)
        rs = []
        for symbol in picked:
            mt = tables.get(symbol)
            if mt is None:
                continue
            r = next_month_return(mt, month, cost_bps)
            if r is None:
                continue
            rs.append(r)
            symbol_pnl[symbol] += r
        if rs:
            returns[month.to_timestamp(how='end')] = float(np.mean(rs))
    return pd.Series(returns, dtype=float).sort_index(), dict(symbol_pnl)


def max_drawdown(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    wealth = (1.0 + series).cumprod()
    peak = wealth.cummax()
    return float((1.0 - wealth / peak).max())


def metrics(series: pd.Series) -> dict:
    s = series.dropna()
    if s.empty:
        return {'months':0,'annual_return':None,'sharpe':None,'max_drawdown':None,'win_months':None,'total_return':None}
    wealth = float((1+s).prod())
    years = max(len(s)/12.0, 1/12.0)
    ann = wealth ** (1/years) - 1 if wealth > 0 else -1.0
    vol = float(s.std(ddof=1))
    sharpe = float(s.mean()/vol*math.sqrt(12)) if vol > 1e-12 else None
    return {
        'months': int(len(s)),
        'annual_return': round(ann,4),
        'sharpe': None if sharpe is None else round(sharpe,3),
        'max_drawdown': round(max_drawdown(s),4),
        'win_months': round(float((s>0).mean()),3),
        'total_return': round(wealth-1,4),
    }


def slice_period(s: pd.Series, start: str, end: str | None = None) -> pd.Series:
    x = s[s.index >= pd.Timestamp(start)]
    if end:
        x = x[x.index <= pd.Timestamp(end)]
    return x


def concentration(symbol_pnl: dict[str,float]) -> float:
    pos = {k:v for k,v in symbol_pnl.items() if v > 0}
    total = sum(pos.values())
    return 1.0 if total <= 0 else max(pos.values())/total


def eval_strategy(strategy: str, tables_p: dict[str,pd.DataFrame], tables_i: dict[str,pd.DataFrame],
                  panel_p: pd.DataFrame, panel_i: pd.DataFrame) -> dict:
    p30, pnl = monthly_portfolio(tables_p, panel_p, strategy, BASELINE_BPS)
    p60, _ = monthly_portfolio(tables_p, panel_p, strategy, STRESS_BPS)
    i30, _ = monthly_portfolio(tables_i, panel_i, strategy, BASELINE_BPS)

    val = slice_period(p30, '2021-01-01', '2023-12-31')
    hold = slice_period(p30, '2024-01-01')
    indep = slice_period(i30, '2024-01-01')
    stress = slice_period(p60, '2024-01-01')

    mv, mh, mi, ms = map(metrics, [val, hold, indep, stress])
    conc = concentration(pnl)
    passes = (
        mv['months'] >= 24 and mh['months'] >= 24 and mi['months'] >= 24
        and (mv['total_return'] or -99) > 0
        and (mh['total_return'] or -99) > 0
        and (mi['total_return'] or -99) > 0
        and (mh['sharpe'] or -99) > 0.50
        and (mi['sharpe'] or -99) > 0.25
        and (ms['total_return'] or -99) > 0
        and conc <= 0.35
    )
    return {
        'strategy': strategy,
        'status': 'RESEARCH_CANDIDATE' if passes else 'REJECT',
        'validation_2021_2023': mv,
        'holdout_2024_plus': mh,
        'independent_2024_plus': mi,
        'stress_60bps_2024_plus': ms,
        'largest_positive_symbol_pnl_share': round(conc,3),
    }


def main():
    raw_p, raw_i = {}, {}
    all_items = [(s,'primary') for s in PRIMARY] + [(s,'independent') for s in INDEPENDENT]
    for idx,(symbol,univ) in enumerate(all_items,1):
        df = download(symbol)
        print(f'[{idx}/{len(all_items)}] {symbol}: rows={len(df)}')
        if len(df) < 500:
            continue
        (raw_p if univ == 'primary' else raw_i)[symbol] = df

    tables_p = build_tables(raw_p)
    tables_i = build_tables(raw_i)
    panel_p = build_panel(tables_p, 'primary')
    panel_i = build_panel(tables_i, 'independent')
    results = [eval_strategy(s, tables_p, tables_i, panel_p, panel_i) for s in STRATEGIES]
    candidates = [r['strategy'] for r in results if r['status']=='RESEARCH_CANDIDATE']
    out = {
        'method': 'published-rule benchmark; fixed before results; next-open monthly fills; long-only',
        'baseline_roundtrip_bps': BASELINE_BPS,
        'stress_roundtrip_bps': STRESS_BPS,
        'primary_symbols_downloaded': len(raw_p),
        'independent_symbols_downloaded': len(raw_i),
        'results': results,
        'research_candidates': candidates,
        'overall': 'CANDIDATE_FOUND' if candidates else 'NO_CANDIDATE_FOUND',
        'limitations': [
            'current-stock universe has survivorship/selection bias',
            'Yahoo data is not institutional point-in-time constituent data',
            'research candidate is not paper/live approval',
            'time-series momentum literature is strongest in diversified futures; stock adaptation is exploratory',
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(json.dumps(out, indent=2))

if __name__ == '__main__':
    main()
