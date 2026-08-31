from pathlib import Path
import sys
from dataclasses import replace

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))

import trading_bot.backtester as bt

bt.ENGINE = 'V4.4.3-Retest-Risk-Validation'


def _confirmed_retest(s, confirm_bar, prior_history, strategy):
    """Wait one candle after a breakout and enter only after a clean retest hold.

    Risk is anchored to the retest structure rather than inheriting the original
    pre-breakout stop. Targets are NOT pushed farther away: nearby resistance and
    the original conservative targets remain intact. This keeps the test honest
    while making the stop represent the actual post-retest invalidation point.
    """
    if s.setup_type != 'BREAKOUT':
        return s
    try:
        a = max(float(s.atr), 0.01)
        level = float(s.entry_low) + (0.08 * a if strategy == 'DAY' else 0.15 * a)
        lo = float(confirm_bar['Low']); hi = float(confirm_bar['High']); close = float(confirm_bar['Close'])
        rng = max(hi - lo, 0.01)
        close_location = (close - lo) / rng

        if prior_history is None or prior_history.empty:
            return None
        baseline = float(prior_history['Volume'].tail(20).mean()) if 'Volume' in prior_history else 0.0
        vol = float(confirm_bar.get('Volume', 0.0))
        vol_ratio = vol / baseline if baseline > 0 else 1.0

        retest_tolerance = 0.30 * a if strategy == 'DAY' else 0.40 * a
        max_extension = 0.65 * a if strategy == 'DAY' else 0.85 * a
        min_volume = 1.05 if strategy == 'DAY' else 0.90

        if lo > level + retest_tolerance:
            return None
        if close < level:
            return None
        if close > level + max_extension:
            return None
        if close_location < 0.55:
            return None
        if vol_ratio < min_volume:
            return None

        # After a successful retest, the trade is invalid if price loses the
        # retest structure. Use the tighter of that structural invalidation and
        # the old stop, but never place a stop at/above the actual entry.
        buffer = 0.18 * a if strategy == 'DAY' else 0.25 * a
        retest_stop = min(lo, level) - buffer
        old_stop = float(s.stop_loss)
        stop = max(old_stop, retest_stop)
        if stop >= close - 0.05 * a:
            return None

        risk = max(close - stop, 0.01)
        target1 = float(s.target1)
        target2 = float(s.target2)
        rr1 = (target1 - close) / risk
        rr2 = (target2 - close) / risk
        if rr1 <= 0 or rr2 <= 0:
            return None

        return replace(
            s,
            price=close,
            entry_low=close - 0.03 * a,
            entry_high=close + 0.03 * a,
            stop_loss=stop,
            target1=target1,
            target2=target2,
            rr1=rr1,
            rr2=rr2,
            reasons=list(s.reasons) + [
                f'Breakout retest held; confirmation volume {vol_ratio:.2f}x',
                'Stop anchored to retest invalidation structure',
            ],
            warnings=list(s.warnings),
        )
    except Exception:
        return None


def _swing_retest(symbol, df, spy, cost):
    rows=[]; last_signal_i=-99
    for i in range(220, len(df)-7):
        if i-last_signal_i < 3: continue
        hist=df.iloc[:i+1]; s=bt.swing_setup(symbol,hist)
        if not bt._collectable(s): continue
        if s.setup_type == 'BREAKOUT':
            s2=_confirmed_retest(s,df.iloc[i+1],hist,'SWING')
            if s2 is None: continue
            future=df.iloc[i+2:i+7]
            ts=df.index[i+1]
            qual_hist=df.iloc[:i+2]
        else:
            s2=s; future=df.iloc[i+1:i+6]; ts=df.index[i]; qual_hist=hist
        o=bt._outcome(s2,future,cost)
        if o:
            rows.append(bt._row(symbol,'SWING',ts,s2,o,bt._qualification(s2,qual_hist,spy,ts)))
            last_signal_i=i
    return rows


def _day_retest(symbol, df, daily, spy, cost):
    rows=[]; last_day=None
    if len(df)<100: return rows
    for i in range(60,len(df)-5,4):
        signal_time=df.index[i]
        if last_day==signal_time.date(): continue
        history=df.iloc[:i+1].tail(350); s=bt.intraday_setup(symbol,history)
        if not bt._collectable(s): continue
        if s.setup_type == 'BREAKOUT':
            confirm=df.iloc[i+1]
            if df.index[i+1].date()!=signal_time.date(): continue
            s2=_confirmed_retest(s,confirm,history,'DAY')
            if s2 is None: continue
            confirm_time=df.index[i+1]
            same_day=df[(df.index>confirm_time)&(df.index.date==confirm_time.date())].head(12)
            ts=confirm_time
        else:
            s2=s
            same_day=df[(df.index>signal_time)&(df.index.date==signal_time.date())].head(12)
            ts=signal_time
        if same_day.empty: continue
        o=bt._outcome(s2,same_day,cost)
        if o:
            stock_daily=bt._daily_history_at(daily,ts)
            rows.append(bt._row(symbol,'DAY',ts,s2,o,bt._qualification(s2,stock_daily,spy,ts)))
            last_day=signal_time.date()
    return rows


bt._swing = _swing_retest
bt._day = _day_retest

if __name__ == '__main__':
    raise SystemExit(bt.run_backtest())
