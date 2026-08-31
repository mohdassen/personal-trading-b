from pathlib import Path
import sys
from dataclasses import replace

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))

import trading_bot.backtester as bt

bt.ENGINE = 'V4.4.2-Retest-Validation'


def _confirmed_retest(s, confirm_bar, prior_history, strategy):
    """Wait one candle after a breakout and only enter if the level holds.

    This is deliberately conservative: no same-candle breakout entry, no close
    back under resistance, no badly extended close, and the confirmation candle
    must finish in its upper half with reasonable volume.
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

        # Must revisit/hold the breakout neighborhood, close above it, avoid chase,
        # and finish with buyers controlling the confirmation candle.
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
        if close <= float(s.stop_loss):
            return None

        risk = max(close - float(s.stop_loss), 0.01)
        rr1 = (float(s.target1) - close) / risk
        rr2 = (float(s.target2) - close) / risk
        if rr1 <= 0 or rr2 <= 0:
            return None

        return replace(
            s,
            price=close,
            entry_low=close - 0.03 * a,
            entry_high=close + 0.03 * a,
            rr1=rr1,
            rr2=rr2,
            reasons=list(s.reasons) + [f'Breakout retest held; confirmation volume {vol_ratio:.2f}x'],
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
