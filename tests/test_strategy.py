import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from trading_bot.strategy import analyze


def test_analyze_returns_signal():
    n = 80
    idx = pd.date_range("2026-08-20 09:30", periods=n, freq="15min", tz="America/New_York")
    close = np.linspace(100, 112, n)
    df = pd.DataFrame({
        "Open": close - 0.1,
        "High": close + 0.4,
        "Low": close - 0.4,
        "Close": close,
        "Volume": np.linspace(1_000_000, 2_000_000, n),
    }, index=idx)
    s = analyze("TEST", df, 10_000, 0.5, 15)
    assert s is not None
    assert 0 <= s.score <= 100
    assert s.stop_loss < s.price < s.target1 < s.target2
