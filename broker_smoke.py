"""Safe broker connectivity check.

Default behavior is read-only. It never prints credentials and cannot access the
live trading endpoint because AlpacaBroker hard-locks live mode.
"""
from __future__ import annotations

import argparse
import json

from alpaca_integration import AlpacaBroker, AlpacaError


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["read_only", "paper"], default="read_only")
    args = p.parse_args()
    try:
        broker = AlpacaBroker(mode=args.mode)
        print(json.dumps(broker.diagnostics(), indent=2, ensure_ascii=False))
        return 0
    except AlpacaError as exc:
        print(json.dumps({
            "ok": False,
            "mode": args.mode,
            "error": str(exc),
            "live_execution_authorized": False,
        }, indent=2, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
