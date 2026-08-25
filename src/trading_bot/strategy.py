from __future__ import annotations
from dataclasses import dataclass, asdict
import math
import pandas as pd
from .indicators import add_indicators


@dataclass
class Signal:
    symbol: str
    signal: str
    score: int
    price: float
    entry_low: float
    entry_high: float
    stop_loss: float
    target1: float
    target2: float
    risk_reward1: float
    risk_reward2: float
    rsi: float
    vwap: float
    ema9: float
    ema21: float
    ema50: float
    vol_ratio: float
    momentum_1h: float
    atr: float
    suggested_shares: int
    suggested_value: float
    reasons: list[str]
    warnings: list[str]

    def to_dict(self):
        return asdict(self)


def _safe(v, default=0.0):
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def analyze(symbol: str, df: pd.DataFrame, account_equity: float, risk_pct: float, max_position_pct: float) -> Signal | None:
    if len(df) < 55:
        return None
    x = add_indicators(df)
    row = x.iloc[-1]
    price = _safe(row["Close"])
    ema9, ema21, ema50 = map(_safe, [row["EMA9"], row["EMA21"], row["EMA50"]])
    rsi = _safe(row["RSI14"], 50)
    atr = _safe(row["ATR14"], price * 0.02)
    vwap = _safe(row["VWAP"], price)
    vol_ratio = _safe(row["VOL_RATIO"], 1)
    mom1h = _safe(row["RET_1H"], 0)

    score = 0
    reasons: list[str] = []
    warnings: list[str] = []

    if ema9 > ema21 > ema50:
        score += 25; reasons.append("اتجاه صاعد: EMA9 > EMA21 > EMA50")
    elif ema9 > ema21:
        score += 18; reasons.append("زخم صاعد قصير الأجل: EMA9 > EMA21")
    elif ema9 < ema21:
        warnings.append("EMA9 أقل من EMA21")

    if price > vwap:
        score += 15; reasons.append("السعر أعلى من VWAP")
    else:
        warnings.append("السعر أسفل VWAP")

    if 50 <= rsi <= 68:
        score += 20; reasons.append(f"RSI مناسب للزخم ({rsi:.1f})")
    elif 45 <= rsi < 50:
        score += 10
    elif rsi > 72:
        warnings.append(f"RSI مرتفع ({rsi:.1f})")

    if vol_ratio >= 1.5:
        score += 20; reasons.append(f"حجم تداول قوي ({vol_ratio:.2f}x)")
    elif vol_ratio >= 1.1:
        score += 12; reasons.append(f"حجم تداول أعلى من المتوسط ({vol_ratio:.2f}x)")
    else:
        warnings.append(f"الحجم غير قوي ({vol_ratio:.2f}x)")

    if mom1h >= 0.8:
        score += 20; reasons.append(f"زخم ساعة إيجابي ({mom1h:.2f}%)")
    elif mom1h > 0:
        score += 10
    elif mom1h < -1:
        warnings.append(f"زخم ساعة سلبي ({mom1h:.2f}%)")

    score = min(100, max(0, score))
    if score >= 80:
        signal = "BUY"
    elif score >= 65:
        signal = "WATCH"
    else:
        signal = "WAIT"

    entry_low = price - 0.15 * atr
    entry_high = price + 0.10 * atr
    stop = price - max(1.5 * atr, price * 0.015)
    risk_per_share = max(price - stop, 0.01)
    target1 = price + 2.0 * risk_per_share
    target2 = price + 3.0 * risk_per_share

    risk_dollars = account_equity * (risk_pct / 100)
    max_pos_value = account_equity * (max_position_pct / 100)
    shares_by_risk = math.floor(risk_dollars / risk_per_share)
    shares_by_cap = math.floor(max_pos_value / price) if price > 0 else 0
    shares = max(0, min(shares_by_risk, shares_by_cap))
    suggested_value = shares * price

    return Signal(
        symbol=symbol,
        signal=signal,
        score=score,
        price=round(price, 2),
        entry_low=round(entry_low, 2),
        entry_high=round(entry_high, 2),
        stop_loss=round(stop, 2),
        target1=round(target1, 2),
        target2=round(target2, 2),
        risk_reward1=2.0,
        risk_reward2=3.0,
        rsi=round(rsi, 1),
        vwap=round(vwap, 2),
        ema9=round(ema9, 2),
        ema21=round(ema21, 2),
        ema50=round(ema50, 2),
        vol_ratio=round(vol_ratio, 2),
        momentum_1h=round(mom1h, 2),
        atr=round(atr, 2),
        suggested_shares=shares,
        suggested_value=round(suggested_value, 2),
        reasons=reasons,
        warnings=warnings,
    )
