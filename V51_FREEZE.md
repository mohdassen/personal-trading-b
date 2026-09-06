# V5.1 Challenger — Frozen Forward Candidate

Status: FROZEN_FOR_FORWARD_VALIDATION

The V5.1 strategy definition is frozen after historical validation and stress testing passed.

Frozen components:
- v51_active_challenger.py signal qualification and scoring
- Threshold = 60
- Existing portfolio/risk constraints inherited from V4.9
- Conservative Sharia pre-check for forward shadow only

Validation summary:
- Historical holdout expectancy: +0.501R
- Historical holdout profit factor: 2.06
- Historical holdout max drawdown: 3.468R
- Stress test: PASS
- 50 bps/side expectancy: +0.246R
- 50 bps/side profit factor: 1.38
- Sharia pre-check subset expectancy: +0.483R
- Sharia pre-check subset profit factor: 1.89

Rules during forward validation:
1. Do not change signal logic, score formula, threshold, exits, or risk rules.
2. Do not reset forward state unless a genuine technical corruption is proven.
3. Any new research must be done in a separate challenger branch/version.
4. Forward shadow remains research/paper only and must never execute live trades.
5. Sharia pre-check is conservative filtering only and is not formal Sharia certification.

Promotion requires independent forward evidence and human review; historical or stress PASS never authorizes automatic live activation.
