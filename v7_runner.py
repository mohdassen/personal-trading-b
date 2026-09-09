from __future__ import annotations

import v7_decision_engine as engine
from v7_catalyst_rules import classify_catalyst
from v7_paper_guard import install as install_paper_guard
from v7_quality_rules import install as install_quality_rules
from v7_signal_quality import install as install_signal_quality
from v7_sharia_research import install as install_sharia_research
from v7_portfolio_risk import install as install_portfolio_risk
from v7_observability import install as install_observability


def diversified_picks(rows, n=3):
    eligible = [x for x in rows if x.get("status") == "PAPER_ENTRY" and x.get("sharia_status") == "PRECHECK_PASS"]
    eligible.sort(key=lambda x: (x["score"], x["mtf"]["status"] == "PASS_STRONG", x["catalyst"]["score"], x["relative_volume"]), reverse=True)
    picked, group_counts = [], {}
    for x in eligible:
        group = x.get("group", "OTHER")
        if group_counts.get(group, 0) >= 2:
            continue
        picked.append(x); group_counts[group] = group_counts.get(group, 0) + 1
        if len(picked) >= n:
            break
    return picked


engine.classify_catalyst = classify_catalyst
engine._pick_diverse = diversified_picks
install_quality_rules(engine)
install_signal_quality(engine)
# Research-only AAOIFI-style screen wraps the diversified shortlist and fails
# closed on explicit Fail/Review Required; it is not a certification.
install_sharia_research(engine)
install_paper_guard(engine)
install_portfolio_risk(engine)
install_observability(engine)

if __name__ == "__main__":
    if not engine._market_window_open():
        print("V7 outside 09:35-16:05 America/New_York; no paper signal/state mutation.")
        raise SystemExit(0)
    raise SystemExit(engine.main())
