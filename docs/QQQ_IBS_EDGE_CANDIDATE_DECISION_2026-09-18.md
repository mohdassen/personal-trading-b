# QQQ IBS Edge Candidate Decision — 2026-09-18

## Decision

**Research edge candidate found. Forward validation started. Live execution remains locked.**

Strategy: `QQQ_IBS_LOWER_BAND`  
Frozen rule version: `2026-09-18-r1`  
Forward epoch: `QQQ_IBS_FORWARD_2026-09-18`

## Why this candidate survives research

### Published/source validation (Round 13)

- 367 stress-tested trades since 2000.
- 63.2% winning trades.
- Average net return per trade after 30 bps round-trip cost: +0.427%.
- Profit factor: 1.545.
- Bootstrap 95% confidence interval for average trade: +0.143% to +0.712%.
- Five historical stress eras passed.
- Recent 2021+ stress period remained positive.

### Negative evidence retained (Round 14)

Round 14 was **REJECT**, not hidden or overwritten.

At deliberately extreme assumptions:
- 100 bps round-trip cost plus 10 bps slippage per side: expectancy turned negative.
- One-day delayed entry plus heavier cost/slippage also turned negative.
- The tactical strategy beat passive QQQ in only 15.9% of rolling five-year windows.
- Block bootstrap at 50% allocation had a lower 95% CAGR bound below zero.

These failures mean the strategy must be executed near its defined next-open timing and is not a replacement for buy-and-hold.

### Independent broker-data validation (Round 15)

Independent Alpaca IEX daily bars, 2022-01-03 through 2026-09-17:

- 1,181 daily bars.
- 40 completed strategy trades from 2023-04-25 onward.
- At 15 bps round-trip stress:
  - Win rate: 72.5%
  - Average net return/trade: +0.411%
  - Profit factor: 1.602
- At 30 bps round-trip stress:
  - Win rate: 65.0%
  - Average net return/trade: +0.261%
  - Profit factor: 1.358

A 500-quote Alpaca IEX sample on 2026-09-17 showed:
- Median quoted spread: 1.26 bps
- 90th percentile: 1.40 bps
- 95th percentile: 1.54 bps
- Maximum in sample: 3.21 bps

The 30 bps research cost assumption is therefore materially above the observed quote spread sample, although future slippage can differ.

## Frozen trading logic

Entry decision after daily close:
- Close < 10-day high - 2.5 × 25-day average daily range
- IBS < 0.30
- Shadow entry at next trading-day open

Exit decision after daily close:
- Close > prior-day high, **or**
- Close < 300-day moving average
- Shadow exit at next trading-day open

No parameter changes are allowed during the forward epoch.

## Forward state

The scheduled GitHub workflow `QQQ IBS Forward Shadow` is active.

- Mode: `FORWARD_SHADOW_ONLY_NO_ORDERS`
- No broker orders are submitted.
- Live execution: locked.
- First clean forward evidence begins only after the 2026-09-18 epoch.

## Promotion gate

Do not promote to any real-money pilot until:

1. At least 30 clean closed forward trades.
2. Forward realized profit factor >= 1.20.
3. Forward average net return after 30 bps assumed round-trip cost > 0.
4. Chronological forward drawdown is acceptable.
5. No rule or threshold was changed after the forward epoch began.

## Important limitation

QQQ is being used as the research instrument. This document does **not** certify QQQ as Sharia-compliant. Any actual implementation must separately satisfy the user's investment-compliance constraints.

## Current verdict

**The project now has its first evidence-backed trading edge candidate. It is not yet a live-money-approved strategy.**
