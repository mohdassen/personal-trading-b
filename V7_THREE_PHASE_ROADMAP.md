# V7 Three-Phase Hardening Roadmap

## Phase 1 — Execution Safety & Measurement
Status: IMPLEMENTED

- Hard market-session guard for every invocation mode.
- Technical pending revalidation; no Top-3 invalidation shortcut.
- Score de-saturation.
- Portfolio risk sizing: 0.5% per trade, 1.5% total open risk, 1.0% group risk, 25% max position.
- Legacy open-position sizing backfill for analytics only.
- Portfolio ledger with marked equity and dollar P&L.
- MAE/MFE and unrealized R tracking.
- Max drawdown metric.
- Interleaved lightweight monitor for effective ~15-minute lifecycle checks.

## Phase 2 — Signal Quality, Compliance Research & Concentration Control
Status: IMPLEMENTED

- Setup-specific entry-quality gate.
- Recency-aware catalyst classification and stronger event taxonomy.
- AAOIFI SS(21)-style free-data research screen (not certification/fatwa): business activity, debt/market-cap proxy, cash/interest-deposit proxy, impure-income proxy when disclosed.
- Fail-closed Review Required when critical free data is missing.
- Pair-correlation risk limit (0.85) before new Paper positions.
- Existing group-diversification/risk limits retained.

## Phase 3 — Clean Forward Proof & Promotion Governance
Status: IMPLEMENTED

- New clean forward epoch: V7.2_FORWARD_2026-09-09.
- Legacy/pre-epoch trades retained for audit but excluded from clean promotion statistics.
- Per-setup and per-market-regime performance breakdown.
- Objective promotion gate after >=30 clean closed trades.
- Required: positive expectancy, Profit Factor >=1.30, Max Drawdown <=8R.
- SHADOW_CANDIDATE is the highest automated promotion state; live execution remains OFF.
- Lightweight monitor alerts only meaningful Paper open/close events with forward scorecard.

## Deliberately Not Enabled

- Broker/live order execution.
- Formal Sharia certification claims.
- Profit guarantees.
- ML/self-modifying strategy logic before sufficient clean forward evidence.
