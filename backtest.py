from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from trading_bot.backtester import run_backtest
if __name__ == '__main__':
    raise SystemExit(run_backtest())
