from __future__ import annotations

import v7_decision_engine as engine
from v7_catalyst_rules import classify_catalyst

engine.classify_catalyst = classify_catalyst

if __name__ == "__main__":
    raise SystemExit(engine.main())
