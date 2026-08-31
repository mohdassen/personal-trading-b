from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from trading_bot.market_discovery_diagnostics import run

if __name__ == '__main__':
    raise SystemExit(run())
