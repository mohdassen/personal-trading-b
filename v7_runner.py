from __future__ import annotations

import v7_decision_engine as engine
from v7_catalyst_rules import classify_catalyst


def diversified_picks(rows, n=3):
    eligible = [x for x in rows if x.get("status") == "PAPER_ENTRY" and x.get("sharia_status") == "PRECHECK_PASS"]
    eligible.sort(
        key=lambda x: (
            x["score"],
            x["mtf"]["status"] == "PASS_STRONG",
            x["catalyst"]["score"],
            x["relative_volume"],
        ),
        reverse=True,
    )
    picked = []
    group_counts = {}
    for x in eligible:
        group = x.get("group", "OTHER")
        if group_counts.get(group, 0) >= 2:
            continue
        picked.append(x)
        group_counts[group] = group_counts.get(group, 0) + 1
        if len(picked) >= n:
            break
    return picked


engine.classify_catalyst = classify_catalyst
engine._pick_diverse = diversified_picks

if __name__ == "__main__":
    raise SystemExit(engine.main())
