from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import yaml

ROOT = Path(__file__).resolve().parent
ENGINE = 'V4.7-Evidence-Momentum-Position'
YEARS = 7
HOLD_DAYS = 40
SCORE_GRID = (55, 60, 65, 70, 75, 80, 85)
MIN_DOLLAR_VOLUME = 20_000_000.0


def _norm(df, symbol):
    if isinstance(df.columns, pd.MultiIndex):
        if symbol in df.columns.get_level_values(-1):
            df = df.xs(symbol, axis=1, level=-1)
        else:
            df.columns = df.columns.get_level_values(0)
    needed = ['Open', 'High', 'Low', 'Close', 'Volume']
    if not all(c in df.columns for c in needed):
        return pd.DataFrame()
    return df.dropna(subset=['Open', 'High', 'Low', 'Close']).copy()


def _download(symbol):
    try:
        # auto_adjust=True is intentional: momentum/trend tests must not treat
        # stock splits or cash distributions as economic price crashes.
        df = yf.download(symbol, period=f'{YEARS}y', interval='1d', auto_adjust=True,
                         progress=False, threads=False, timeout=30)
        return symbol, _norm(df, symbol)
    except Exception as e:
        print('DOWNLOAD', symbol, e)
        return symbol, pd.DataFrame()


def _atr(df, n=14):
    prev = df['Close'].shift(1)
    tr = pd.concat([
        (df['High'] - df['Low']).abs(),
        (df['High'] - prev).abs(),
        (df['Low'] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _features(df):
    x = df.copy()
    c = x['Close']
    x['SMA50'] = c.rolling(50).mean()
    x['SMA100'] = c.rolling(100).mean()
    x['SMA200'] = c.rolling(200).mean()
    x['ATR14'] = _atr(x)
    x['ATR_PCT'] = x['ATR14'] / c
    logret = np.log(c / c.shift(1))
    x['VOL20'] = logret.rolling(20).std() * math.sqrt(252)
    x['ADV20'] = (c * x['Volume']).rolling(20).mean()
    # Standard medium-horizon momentum deliberately skips the latest month.
    x['MOM6_1'] = c.shift(21) / c.shift(126) - 1.0
    x['MOM12_1'] = c.shift(21) / c.shift(252) - 1.0
    x['RET1M'] = c / c.shift(21) - 1.0
    x['RET5'] = c / c.shift(5) - 1.0
    x['RET20'] = c / c.shift(20) - 1.0
    x['RET63'] = c / c.shift(63) - 1.0
    x['HIGH52'] = x['High'].shift(1).rolling(252).max()
    x['NEAR52'] = c / x['HIGH52']
    return x


def _spy_regime(spy):
    s = _features(spy)
    prior_63_before_rebound = s['Close'].shift(5) / s['Close'].shift(68) - 1.0
    panic = (
        (s['RET20'] < -0.08)
        | ((s['RET63'] < -0.10) & (s['VOL20'] > 0.25))
        | ((prior_63_before_rebound < -0.10) & (s['RET5'] > 0.05) & (s['VOL20'] > 0.25))
        | (s['VOL20'] > 0.40)
    )
    s['REGIME_OK'] = (
        (s['Close'] > s['SMA200'])
        & (s['SMA50'] > s['SMA200'])
        & (s['MOM6_1'] > 0)
        & (~panic)
    )
    s['PANIC'] = panic
    return s


def _weekly_dates(df):
    if df.empty:
        return []
    tmp = pd.DataFrame(index=df.index)
    tmp['period'] = tmp.index.to_period('W-FRI')
    return list(tmp.groupby('period').tail(1).index)


def _signal_rows(symbol, df, spy_f):
    if len(df) < 310:
        return []
    x = _features(df)
    rows = []
    for ts in _weekly_dates(x):
        try:
            i = x.index.get_loc(ts)
            if isinstance(i, slice) or i < 260 or i + HOLD_DAYS + 2 >= len(x):
                continue
            r = x.loc[ts]
            sr = spy_f.loc[:ts].iloc[-1] if len(spy_f.loc[:ts]) else None
            if sr is None or pd.isna(r['MOM12_1']) or pd.isna(sr['MOM12_1']):
                continue
            rows.append({
                'symbol': symbol,
                'date': str(pd.Timestamp(ts).date()),
                'timestamp': pd.Timestamp(ts).isoformat(),
                'i': int(i),
                'close': float(r['Close']),
                'atr': float(r['ATR14']),
                'atr_pct': float(r['ATR_PCT']),
                'vol20': float(r['VOL20']),
                'adv20': float(r['ADV20']),
                'mom6_1': float(r['MOM6_1']),
                'mom12_1': float(r['MOM12_1']),
                'ret1m': float(r['RET1M']),
                'near52': float(r['NEAR52']),
                'sma50': float(r['SMA50']),
                'sma100': float(r['SMA100']),
                'sma200': float(r['SMA200']),
                'spy_mom6_1': float(sr['MOM6_1']),
                'spy_mom12_1': float(sr['MOM12_1']),
                'spy_vol20': float(sr['VOL20']),
                'regime_ok': bool(sr['REGIME_OK']),
                'panic': bool(sr['PANIC']),
            })
        except Exception:
            continue
    return rows


def _clip_score(v, lo, hi, points):
    if not np.isfinite(v):
        return 0.0
    if v <= lo:
        return 0.0
    if v >= hi:
        return float(points)
    return float(points) * (v - lo) / (hi - lo)


def _rank_and_qualify(raw_rows):
    if not raw_rows:
        return []
    d = pd.DataFrame(raw_rows)
    d['mom12_rank'] = d.groupby('date')['mom12_1'].rank(pct=True)
    d['mom6_rank'] = d.groupby('date')['mom6_1'].rank(pct=True)
    out = []
    for _, r in d.iterrows():
        trend_ok = bool(r['close'] > r['sma50'] > r['sma100'] > r['sma200'])
        absolute_momentum = bool(r['mom12_1'] > 0.05 and r['mom6_1'] > 0.02)
        relative_strength = bool(
            r['mom12_1'] > r['spy_mom12_1']
            and r['mom6_1'] > r['spy_mom6_1']
            and r['mom12_rank'] >= 0.70
            and r['mom6_rank'] >= 0.60
        )
        near_high = bool(0.82 <= r['near52'] <= 1.03)
        recent_not_chased = bool(-0.10 <= r['ret1m'] <= 0.15)
        liquid = bool(r['adv20'] >= MIN_DOLLAR_VOLUME)
        risk_ok = bool(0.008 <= r['atr_pct'] <= 0.075 and r['vol20'] <= 0.80)
        base_ok = bool(r['regime_ok'] and trend_ok and absolute_momentum and relative_strength
                       and near_high and recent_not_chased and liquid and risk_ok)

        score = 0.0
        score += 15.0 if trend_ok else 0.0
        score += _clip_score(r['mom12_1'], 0.05, 0.55, 18)
        score += _clip_score(r['mom6_1'], 0.02, 0.30, 12)
        score += _clip_score(r['mom12_rank'], 0.60, 1.00, 15)
        score += _clip_score(r['mom6_rank'], 0.55, 1.00, 10)
        score += _clip_score(r['near52'], 0.82, 1.00, 10)
        rs12 = r['mom12_1'] - r['spy_mom12_1']
        rs6 = r['mom6_1'] - r['spy_mom6_1']
        score += _clip_score(rs12, 0.00, 0.30, 5)
        score += _clip_score(rs6, 0.00, 0.18, 5)
        score += 5.0 if recent_not_chased else 0.0
        score += 5.0 if (liquid and risk_ok and r['regime_ok']) else 0.0
        x = r.to_dict()
        x['score'] = int(round(min(100.0, score)))
        x['base_ok'] = base_ok
        x['trend_ok'] = trend_ok
        x['relative_strength_ok'] = relative_strength
        out.append(x)
    return out


def _cost_pct(cost_bps):
    return 2.0 * float(cost_bps) / 100.0


def _simulate(row, df, cost_bps):
    i = int(row['i'])
    if i + 2 >= len(df):
        return None
    entry_i = i + 1
    entry = float(df['Open'].iloc[entry_i])
    atr = max(float(row['atr']), 0.01)
    risk = 2.5 * atr
    if risk / max(entry, 0.01) < 0.012 or risk / max(entry, 0.01) > 0.12:
        return None
    stop = entry - risk
    target1 = entry + 2.0 * risk
    target2 = entry + 4.0 * risk
    trail = stop
    armed = False
    exit_price = float(df['Close'].iloc[min(entry_i + HOLD_DAYS, len(df)-1)])
    exit_i = min(entry_i + HOLD_DAYS, len(df)-1)
    outcome = 'TIME_EXIT'

    last = min(entry_i + HOLD_DAYS, len(df)-1)
    for j in range(entry_i, last + 1):
        bar = df.iloc[j]
        op = float(bar['Open']); lo = float(bar['Low']); hi = float(bar['High'])
        active_stop = trail
        # Conservative ordering: adverse stop is evaluated before upside target.
        if op <= active_stop:
            exit_price = op; exit_i = j; outcome = 'STOP_GAP'; break
        if lo <= active_stop:
            exit_price = active_stop; exit_i = j; outcome = 'STOP'; break
        if op >= target2:
            exit_price = target2; exit_i = j; outcome = 'TARGET2'; break
        if hi >= target2:
            exit_price = target2; exit_i = j; outcome = 'TARGET2'; break
        if hi >= target1:
            armed = True
        # Trailing information uses only completed PRIOR bars, so no look-ahead.
        if armed and j > entry_i:
            a = max(entry_i, j - 10)
            prior_low = float(df['Low'].iloc[a:j].min())
            new_trail = prior_low - 0.25 * atr
            trail = max(trail, entry, new_trail)

    cost = entry * (_cost_pct(cost_bps) / 100.0)
    ret_pct = (exit_price - entry) / entry * 100.0 - _cost_pct(cost_bps)
    r_mult = (exit_price - entry - cost) / risk
    return {
        'entry_date': str(pd.Timestamp(df.index[entry_i]).date()),
        'exit_date': str(pd.Timestamp(df.index[exit_i]).date()),
        'entry': round(entry, 4),
        'stop': round(stop, 4),
        'target1': round(target1, 4),
        'target2': round(target2, 4),
        'risk_pct': round(risk / entry * 100.0, 3),
        'outcome': outcome,
        'return_pct': round(ret_pct, 3),
        'r_multiple': round(r_mult, 3),
        'exit_i': int(exit_i),
    }


def _trades(qualified_rows, prices, cost_bps, threshold):
    selected = [r for r in qualified_rows if r.get('base_ok') and int(r['score']) >= threshold]
    selected.sort(key=lambda x: (x['symbol'], x['timestamp']))
    out = []
    last_exit_by_symbol = {}
    for r in selected:
        symbol = r['symbol']; i = int(r['i'])
        if i <= last_exit_by_symbol.get(symbol, -1):
            continue
        sim = _simulate(r, prices[symbol], cost_bps)
        if sim is None:
            continue
        last_exit_by_symbol[symbol] = int(sim['exit_i'])
        out.append({
            'symbol': symbol,
            'strategy': 'POSITION',
            'setup_type': 'EVIDENCE_MOMENTUM',
            'signal_date': r['date'],
            'timestamp': r['timestamp'],
            'score': int(r['score']),
            'mom12_1': round(float(r['mom12_1']), 4),
            'mom6_1': round(float(r['mom6_1']), 4),
            'mom12_rank': round(float(r['mom12_rank']), 3),
            'mom6_rank': round(float(r['mom6_rank']), 3),
            'near52': round(float(r['near52']), 4),
            'spy_vol20': round(float(r['spy_vol20']), 4),
            **{k:v for k,v in sim.items() if k != 'exit_i'},
        })
    return sorted(out, key=lambda x: x['timestamp'])


def _max_drawdown_r(rows):
    equity = 0.0; peak = 0.0; dd = 0.0
    for r in sorted(rows, key=lambda x: x['timestamp']):
        equity += float(r['r_multiple'])
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return round(dd, 3)


def _stats(rows):
    if not rows:
        return {'samples':0,'win_rate':0.0,'avg_return_pct':0.0,'expectancy_r':0.0,
                'median_r':0.0,'profit_factor_r':0.0,'max_drawdown_r':0.0}
    rs = [float(x['r_multiple']) for x in rows]
    wins = [x for x in rs if x > 0]; losses = [x for x in rs if x <= 0]
    gp = sum(wins); gl = abs(sum(losses))
    return {
        'samples': len(rows),
        'win_rate': round(len(wins)/len(rows)*100.0, 1),
        'avg_return_pct': round(sum(float(x['return_pct']) for x in rows)/len(rows), 3),
        'expectancy_r': round(sum(rs)/len(rs), 3),
        'median_r': round(float(pd.Series(rs).median()), 3),
        'profit_factor_r': round(gp/gl, 2) if gl else (999.0 if gp else 0.0),
        'max_drawdown_r': _max_drawdown_r(rows),
    }


def _date_slice(rows, start_frac, end_frac):
    if not rows:
        return []
    dates = sorted(set(x['signal_date'] for x in rows))
    if len(dates) < 2:
        return rows
    a = min(len(dates)-1, int(len(dates)*start_frac))
    b = min(len(dates), max(a+1, int(len(dates)*end_frac)))
    wanted = set(dates[a:b])
    return [x for x in rows if x['signal_date'] in wanted]


def _validate(qualified_rows, prices, cost_bps):
    # We create each threshold's trades independently because higher thresholds
    # change overlap/cooldown, then reserve the final 20% calendar dates untouched.
    all_by_threshold = {s: _trades(qualified_rows, prices, cost_bps, s) for s in SCORE_GRID}
    ref = all_by_threshold[min(SCORE_GRID)]
    all_dates = sorted(set(x['signal_date'] for x in ref))
    if len(all_dates) < 20:
        return {'status':'INSUFFICIENT_DATA','safe_for_shadow':False,'robust_candidate_count':0}
    cut = all_dates[max(1, int(len(all_dates)*0.80))-1]
    candidates = []
    for threshold, rows in all_by_threshold.items():
        selection = [x for x in rows if x['signal_date'] <= cut]
        folds = [(0.00,0.32),(0.16,0.48),(0.32,0.64),(0.48,0.80)]
        fs = []
        for a,b in folds:
            # Fold fractions are with respect to the first 80% selection history.
            f = _date_slice(selection, a/0.80, b/0.80)
            fs.append(_stats(f))
        useful = [x for x in fs if int(x['samples']) >= 8]
        if len(useful) < 3:
            continue
        exps = [float(x['expectancy_r']) for x in useful]
        pfs = [float(x['profit_factor_r']) for x in useful]
        positive_ratio = sum(e > 0 for e in exps)/len(exps)
        median_exp = float(pd.Series(exps).median())
        worst_exp = min(exps)
        median_pf = float(pd.Series(pfs).median())
        total_samples = sum(int(x['samples']) for x in useful)
        if positive_ratio >= 0.75 and median_exp > 0.05 and worst_exp > -0.12 and median_pf >= 1.10 and total_samples >= 40:
            candidates.append({
                'score': threshold,
                'positive_fold_ratio': round(positive_ratio,3),
                'median_fold_expectancy_r': round(median_exp,3),
                'worst_fold_expectancy_r': round(worst_exp,3),
                'median_fold_profit_factor': round(median_pf,2),
                'fold_samples': [int(x['samples']) for x in fs],
                'folds': fs,
            })
    # Require a neighborhood, not a single magic threshold.
    passing_scores = {x['score'] for x in candidates}
    robust = [x for x in candidates if (x['score']-5 in passing_scores or x['score']+5 in passing_scores)]
    robust.sort(key=lambda x:(x['median_fold_expectancy_r'], x['worst_fold_expectancy_r'], sum(x['fold_samples'])), reverse=True)
    selected = robust[0] if robust else None
    if selected:
        full = all_by_threshold[selected['score']]
        holdout = [x for x in full if x['signal_date'] > cut]
        holdout_stats = _stats(holdout)
        h1 = _date_slice(holdout,0.0,0.5); h2 = _date_slice(holdout,0.5,1.0)
        halves = [_stats(h1), _stats(h2)]
        halves_ok = all(int(x['samples']) >= 8 and float(x['expectancy_r']) > -0.10 for x in halves)
    else:
        holdout = []; holdout_stats = _stats([]); halves = [_stats([]),_stats([])]; halves_ok=False
    safe = bool(
        selected
        and holdout_stats['samples'] >= 20
        and holdout_stats['expectancy_r'] > 0.05
        and holdout_stats['profit_factor_r'] >= 1.15
        and holdout_stats['max_drawdown_r'] <= 10.0
        and halves_ok
    )
    return {
        'status': 'PASS' if safe else ('HOLDOUT_FAIL' if selected else 'NO_ROBUST_REGION'),
        'selection_end_date': cut,
        'robust_candidate_count': len(robust),
        'selected': selected,
        'final_untouched_holdout': holdout_stats,
        'holdout_halves': halves,
        'safe_for_shadow': safe,
        'acceptance_gate': {
            'holdout_min_samples': 20,
            'holdout_min_expectancy_r': 0.05,
            'holdout_min_profit_factor': 1.15,
            'holdout_max_drawdown_r': 10.0,
            'each_holdout_half_min_samples': 8,
            'each_holdout_half_min_expectancy_r': -0.10,
        },
        'top_candidates': robust[:10],
        'threshold_overall': {str(s): _stats(rows) for s, rows in all_by_threshold.items()},
        'selected_holdout_samples': holdout[-500:],
    }


def main():
    cfg = yaml.safe_load((ROOT/'config/settings.yml').read_text())['settings']
    universe = yaml.safe_load((ROOT/'config/universe.yml').read_text())['universe']
    cost_bps = float(cfg.get('backtest_transaction_cost_bps', 10))
    symbols = list(dict.fromkeys(['SPY'] + list(universe)))
    prices = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_download, s): s for s in symbols}
        for f in as_completed(futs):
            s, df = f.result()
            if not df.empty:
                prices[s] = df
    if 'SPY' not in prices:
        raise RuntimeError('SPY market data unavailable')
    spy_f = _spy_regime(prices['SPY'])
    raw = []
    for symbol in universe:
        if symbol not in prices:
            continue
        raw.extend(_signal_rows(symbol, prices[symbol], spy_f))
    qualified_rows = _rank_and_qualify(raw)
    base_count = sum(bool(x.get('base_ok')) for x in qualified_rows)
    validation = _validate(qualified_rows, prices, cost_bps)
    out = {
        'engine': ENGINE,
        'method': (
            'Long-only evidence-based position momentum. Weekly evaluation; adjusted prices; next-session-open entry; '
            '12-1 and 6-1 medium-horizon momentum; cross-sectional winner ranks; relative strength versus SPY; '
            '52-week-high proximity; 50/100/200-day trend; liquidity floor; Daniel-Moskowitz-style panic/rebound and '
            'high-volatility regime avoidance; 2.5ATR initial risk; 2R trailing activation; 4R target; 40-session max hold; '
            'round-trip transaction costs. Four overlapping walk-forward folds select only stable neighboring score regions; '
            'the final 20% of calendar history is untouched holdout and must also be stable in both halves.'
        ),
        'years_requested': YEARS,
        'holding_days_max': HOLD_DAYS,
        'transaction_cost_bps_per_side': cost_bps,
        'universe_size': len(universe),
        'symbols_with_data': len([s for s in universe if s in prices]),
        'weekly_feature_rows': len(qualified_rows),
        'base_qualified_signals': base_count,
        'validation': validation,
    }
    (ROOT/'data/backtest.json').write_text(json.dumps(out, indent=2), encoding='utf-8')
    printable = {**out, 'validation': {k:v for k,v in validation.items() if k != 'selected_holdout_samples'}}
    print(json.dumps(printable, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
