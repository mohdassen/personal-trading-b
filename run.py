import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from trading_bot.app import run

if __name__ == "__main__":
    raise SystemExit(run())
