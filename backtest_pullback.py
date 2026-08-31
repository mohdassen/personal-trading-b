from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys

import pandas as pd
import yfinance as yf
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))

import trading_bot.backtester as bt
from trading_bot.indicators import add_daily, add_intraday
from trading_bot.strategies import Setup, safe

ENGINE = 'V4.6-Pullback-Trend-Continuation'
SCORE_GRID = (65, 70, 75, 80, 82, 85, 88, 90)
RR_GRID = (1.5, 1.7, 1.9, 2.0, 2.2)


def _close_location(row):
    hi = safe(row.get('High')); lo = safe(row.get('Low')); c = safe(row.get('Close'))
    return (c - lo) / max(hi - lo, 0.01)


def _recent_resistance(x, lookback, fallback):
    r = x.iloc[-lookback-1:-1] if len(x) > lookback + 1 else x.iloc[:-1]
    if r.empty:
        return fallback
    return safe(r['High'].max(), fallback)


def _build_pullback(symbol, x, strategy):
    r = x.iloc[-1]
    p = safe(r['Close']); a = max(safe(r['ATR14'], p * 0.02), 0.01)
    rs = safe(r['RSI14'], 50); vr = safe(r['VOL_RATIO'], 1); cl = _close_location(r)
    reasons = []; warnings = []; score = 0

    if strategy == 'SWING':
        e20, e50, e200 = map(safe, [r['EMA20'], r['EMA50'], r['EMA200']])
        mom = safe(r['RET_5D'], 0)
        if not (p > e50 and e20 > e50 and (e200 <= 0 or e50 > e200)):
            return None
        score += 30; reasons.append('Bullish EMA20/50 trend structure')
        # Genuine pullback: price must test the EMA20 zone and reclaim it on the close.
        low = safe(r['Low'])
        distance = abs(low - e20) / a
        if distance <= 0.60 and p >= e20:
            score += 24; reasons.append('Pullback tested and reclaimed EMA20')
        else:
            return None
        if 45 <= rs <= 62:
            score += 16; reasons.append(f'Healthy pullback RSI {rs:.1f}')
        else:
            return None
        if -2.5 <= mom <= 4.5:
            score += 12; reasons.append(f'Controlled 5d momentum {mom:+.2f}%')
        else:
            return None
        if cl >= 0.58:
            score += 10; reasons.append('Recovery close in upper candle range')
        else:
            return None
        if vr <= 1.8:
            score += 8; reasons.append(f'No distribution-volume spike ({vr:.2f}x)')
        else:
            return None
        entry = p
        structural = min(low, e20) - 0.30 * a
        max_risk = max(2.2 * a, p * 0.045)
        stop = max(p - max_risk, structural)
        resistance = _recent_resistance(x, 30, p + 2 * a)
    else:
        e9, e21, e50 = map(safe, [r['EMA9'], r['EMA21'], r['EMA50']])
        vw = safe(r['VWAP'], p); mom = safe(r['RET_1H'], 0)
        if not (e9 > e21 > e50 and p >= vw):
            return None
        score += 30; reasons.append('Bullish EMA9/21/50 structure above VWAP')
        low = safe(r['Low']); zone = max(e21, vw)
        distance = abs(low - zone) / a
        if distance <= 0.55 and p >= zone:
            score += 24; reasons.append('Intraday pullback reclaimed EMA21/VWAP zone')
        else:
            return None
        if 45 <= rs <= 64:
            score += 16; reasons.append(f'Healthy intraday RSI {rs:.1f}')
        else:
            return None
        if -0.8 <= mom <= 1.8:
            score += 12; reasons.append(f'Controlled 1h momentum {mom:+.2f}%')
        else:
            return None
        if cl >= 0.60:
            score += 10; reasons.append('Recovery close near bar high')
        else:
            return None
        if vr >= 0.75:
            score += 8; reasons.append(f'Participation volume {vr:.2f}x')
        else:
            return None
        entry = p
        structural = min(low, zone) - 0.20 * a
        max_risk = max(1.7 * a, p * 0.018)
        stop = max(p - max_risk, structural)
        resistance = _recent_resistance(x, 20, p + 2 * a)

    risk = max(entry - stop, 0.01)
    raw_t1 = entry + 2.0 * risk
    target1 = resistance if entry < resistance < raw_t1 else raw_t1
    target2 = entry + 3.0 * risk
    rr1 = (target1 - entry) / risk
    rr2 = (target2 - entry) / risk
    if rr1 < 0.25:
        return None
    return Setup(symbol, strategy, min(100, score), entry,
                 entry - 0.04 * a, entry + 0.04 * a, stop,
                 target1, max(target2, target1), rr1, rr2, a, rs, vr, mom,
                 reasons, warnings, 'PULLBACK')


def _swing(symbol, df, spy, cost):
    rows = []; last_i = -99
    for i in range(220, len(df) - 6):
        if i - last_i < 3:
            continue
        hist = df.iloc[:i+1]
        try:
            x = add_daily(hist)
            s = _build_pullback(symbol, x, 'SWING')
        except Exception:
            continue
        if not bt._collectable(s):
            continue
        o = bt._outcome(s, df.iloc[i+1:i+6], cost)
        if o:
            rows.append(bt._row(symbol, 'SWING', df.index[i], s, o, bt._qualification(s, hist, spy, df.index[i])))
            last_i = i
    return rows


def _day(symbol, df, daily, spy, cost):
    rows = []; last_day = None
    if len(df) < 100:
        return rows
    for i in range(60, len(df) - 4, 4):
        ts = df.index[i]
        if last_day == ts.date():
            continue
        hist = df.iloc[:i+1].tail(350)
        try:
            x = add_intraday(hist)
            s = _build_pullback(symbol, x, 'DAY')
        except Exception:
            continue
        if not bt._collectable(s):
            continue
        future = df[(df.index > ts) & (df.index.date == ts.date())].head(12)
        if future.empty:
            continue
        o = bt._outcome(s, future, cost)
        if o:
            stock_daily = bt._daily_history_at(daily, ts)
            rows.append(bt._row(symbol, 'DAY', ts, s, o, bt._qualification(s, stock_daily, spy, ts)))
            last_day = ts.date()
    return rows


def _one(symbol, years, spy, cost):
    rows = []; daily = pd.DataFrame()
    try:
        daily = bt._norm(yf.download(symbol, period=f'{years}y', interval='1d', auto_adjust=False, progress=False, threads=False, timeout=25), symbol)
        rows.extend(_swing(symbol, daily, spy, cost))
    except Exception as e:
        print('PB SWING', symbol, e)
    try:
        intra = bt._norm(yf.download(symbol, period='60d', interval='15m', auto_adjust=False, progress=False, threads=False, timeout=25), symbol)
        rows.extend(_day(symbol, intra, daily, spy, cost))
    except Exception as e:
        print('PB DAY', symbol, e)
    return rows


def _eligible(rows, score, rr):
    return [x for x in rows if x.get('qualification_passed') and int(x['score']) >= score and float(x['rr1']) >= rr]


def _stats(rows):
    return bt._stats(rows)


def _date_split(rows, start_frac, end_frac):
    if not rows:
        return []
    dates = sorted(set(x['date'] for x in rows))
    if not dates:
        return []
    a = min(len(dates)-1, int(len(dates) * start_frac))
    b = min(len(dates), max(a+1, int(len(dates) * end_frac)))
    wanted = set(dates[a:b])
    return [x for x in rows if x['date'] in wanted]


def _robust_validate(rows):
    # Final 20% is untouched holdout. Selection sees only first 80%.
    selection = _date_split(rows, 0.0, 0.80)
    holdout = _date_split(rows, 0.80, 1.0)
    folds = [(0.00,0.32),(0.16,0.48),(0.32,0.64),(0.48,0.80)]
    candidates = []
    for score in SCORE_GRID:
        for rr in RR_GRID:
            fold_stats = []
            for a,b in folds:
                window = _date_split(selection, a/0.80, b/0.80)
                s = _stats(_eligible(window, score, rr))
                fold_stats.append(s)
            sample_counts = [int(x['samples']) for x in fold_stats]
            exps = [float(x['expectancy_r']) for x in fold_stats if int(x['samples']) >= 5]
            pfs = [float(x['profit_factor_r']) for x in fold_stats if int(x['samples']) >= 5]
            if len(exps) < 3:
                continue
            positive_ratio = sum(e > 0 for e in exps) / len(exps)
            median_exp = float(pd.Series(exps).median())
            worst_exp = min(exps)
            median_pf = float(pd.Series(pfs).median()) if pfs else 0.0
            # Require temporal stability; no parameter can pass on one lucky window only.
            if positive_ratio >= 0.75 and median_exp > 0.05 and worst_exp > -0.10 and median_pf >= 1.10 and sum(sample_counts) >= 30:
                candidates.append({
                    'score': score, 'min_rr': rr,
                    'positive_fold_ratio': round(positive_ratio, 3),
                    'median_fold_expectancy_r': round(median_exp, 3),
                    'worst_fold_expectancy_r': round(worst_exp, 3),
                    'median_fold_profit_factor': round(median_pf, 2),
                    'fold_samples': sample_counts,
                    'folds': fold_stats,
                })
    candidates.sort(key=lambda x: (x['median_fold_expectancy_r'], x['positive_fold_ratio'], sum(x['fold_samples'])), reverse=True)
    selected = candidates[0] if candidates else None
    holdout_stats = _stats(_eligible(holdout, selected['score'], selected['min_rr'])) if selected else _stats([])
    safe = bool(selected and holdout_stats['samples'] >= 15 and holdout_stats['expectancy_r'] > 0.05 and holdout_stats['profit_factor_r'] >= 1.15 and holdout_stats['max_drawdown_r'] <= 8.0)
    return {
        'status': 'PASS' if safe else ('HOLDOUT_FAIL' if selected else 'NO_ROBUST_REGION'),
        'robust_candidate_count': len(candidates),
        'selected': selected,
        'final_untouched_holdout': holdout_stats,
        'safe_for_shadow': safe,
        'acceptance_gate': {
            'holdout_min_samples': 15,
            'holdout_min_expectancy_r': 0.05,
            'holdout_min_profit_factor': 1.15,
            'holdout_max_drawdown_r': 8.0,
        },
        'top_candidates': candidates[:10],
    }


def main():
    cfg = yaml.safe_load((ROOT/'config/settings.yml').read_text())['settings']
    universe = yaml.safe_load((ROOT/'config/universe.yml').read_text())['universe']
    years = int(cfg.get('backtest_years', 3)); cost = float(cfg.get('backtest_transaction_cost_bps', 10))
    spy = bt._norm(yf.download('SPY', period=f'{years}y', interval='1d', auto_adjust=False, progress=False, threads=False, timeout=25), 'SPY')
    rows = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_one, s, years, spy, cost): s for s in universe}
        for f in as_completed(futs):
            try:
                rows.extend(f.result())
            except Exception as e:
                print('PB', futs[f], e)
    rows = sorted(rows, key=lambda x: x['timestamp'])
    qualified = [x for x in rows if x.get('qualification_passed')]
    robust = _robust_validate(rows)
    live_score = int(cfg.get('backtest_min_score',85)); live_rr = float(cfg.get('min_risk_reward',2.0))
    live_rows = _eligible(rows, live_score, live_rr)
    out = {
        'engine': ENGINE,
        'method': 'Genuine trend-continuation pullback: trend alignment, EMA/VWAP zone test and reclaim, controlled momentum, healthy RSI, recovery candle, structure-based stop, nearby-resistance-aware 2R target. Multi-window robust selection with final untouched 20% holdout.',
        'years_requested': years,
        'transaction_cost_bps': cost,
        'candidate_pool_samples': len(rows),
        'qualified_pool_samples': len(qualified),
        'qualified_pool_stats': _stats(qualified),
        'live_parameters': {'score': live_score, 'min_rr': live_rr},
        'live_parameters_stats': _stats(live_rows),
        'live_by_strategy': {n: _stats([x for x in live_rows if x['strategy']==n]) for n in ('DAY','SWING')},
        'robust_validation': robust,
        'validation_samples': live_rows[-1000:],
    }
    (ROOT/'data/backtest.json').write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in out.items() if k != 'validation_samples'}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
