# V7.2 Historical Edge Research Decision — 2026-09-17

## Decision

**Overall V7.2 verdict: FAIL**

The current V7.2 hypothesis set does not show a stable, tradeable historical edge after conservative execution friction and the existing portfolio risk constraints. This result does **not** authorize live trading or small-capital real-money testing.

`SWING_CONTINUATION` is the only setup worth retaining as a focused research hypothesis, but it is **not** classified PROMISING yet because its Train and Validation results are flat/negative and its Holdout bootstrap expectancy interval crosses zero.

## Combined V7.2 portfolio

| Split | Trades | Win rate | Expectancy | PF | Total R | Max DD | Losing streak |
|---|---:|---:|---:|---:|---:|---:|---:|
| Train through 2022 | 499 | 45.3% | +0.011R | 1.02 | +5.50R | 18.60R | 10 |
| Validation 2023–2024 | 271 | 43.5% | -0.045R | 0.93 | -12.33R | 24.29R | 9 |
| Holdout 2025+ | 223 | 41.3% | -0.041R | 0.93 | -9.12R | 20.72R | 11 |

Holdout bootstrap 95% interval for expectancy: **[-0.197R, +0.120R]**.

Cost stress on Holdout:
- 30 bps round trip: -0.041R expectancy, PF 0.93
- 60 bps: -0.126R, PF 0.81
- 100 bps: -0.240R, PF 0.67

Independent-symbol Holdout: 209 trades, +0.030R expectancy, PF 1.05, Max DD 10.96R. This is not strong enough to rescue the combined strategy.

## Strategy portfolios under the same V7 risk limits

| Strategy | Validation Exp / PF | Holdout trades | Holdout Exp | Holdout PF | Holdout DD | Decision evidence |
|---|---:|---:|---:|---:|---:|---|
| SESSION_LEADER | -0.071R / 0.89 | 155 | -0.081R | 0.87 | 18.88R | Fail |
| MOMENTUM_LEADER | -0.031R / 0.95 | 121 | -0.056R | 0.91 | 14.62R | Fail |
| BREAKOUT | +0.083R / 1.15 | 108 | -0.071R | 0.89 | 18.77R | Fails Holdout |
| VWAP_PULLBACK | +0.011R / 1.02 | 254 | -0.116R | 0.82 | 32.00R | Fail |
| SWING_CONTINUATION | -0.018R / 0.97 | 185 | +0.139R | 1.27 | 9.96R | Research lead only |

For `SWING_CONTINUATION`, Holdout bootstrap 95% expectancy interval is **[-0.045R, +0.329R]**. At 60 bps friction it remains slightly positive (+0.065R, PF 1.12); at 100 bps it turns negative (-0.034R, PF 0.94). Train expectancy was -0.003R/PF 0.99 and Validation -0.018R/PF 0.97, so the 2025+ strength is not yet a stable cross-period edge.

## Regime / time robustness

Combined Holdout:
- RISK_ON: -0.042R, PF 0.93
- MIXED: +0.007R, PF 1.01
- RISK_OFF: -0.224R, PF 0.70
- HIGH_VOLATILITY: no Holdout trades selected by the daily proxy

Yearly combined results deteriorate after the early sample: 2019 +0.115R/PF 1.24, 2020 +0.085R/PF 1.16, 2021 +0.007R/PF 1.01, 2022 -0.067R/PF 0.89, 2023 -0.077R/PF 0.87, 2024 -0.016R/PF 0.97, 2025 -0.011R/PF 0.98, 2026 -0.078R/PF 0.88.

Threshold sensitivity on Train+Validation only did not reveal a robust plateau: offsets -4 / 0 / +4 produced expectancy -0.019R / -0.009R / -0.002R and PF 0.97 / 0.98 / 1.00.

## Audit of prior backtests

The previous `v7_edge_research.py` used the next day's **Close** as entry and then evaluated that same day's High/Low against stop/target. That ordering is temporally invalid because those intraday extremes occurred before the assumed close entry. Its results should not be treated as reliable evidence.

The hardened harness uses a completed signal bar, enters at the next session Open, checks adverse stop before target on ambiguous OHLC bars, fills gap-through-stop at the Open, and converts transaction friction into R.

## Limitations

This is deliberately a **daily proxy** research harness. Multi-year 15m/60m history, point-in-time catalyst news, and point-in-time Sharia financial-ratio history are not available from the free Yahoo data source used here. Therefore SESSION_LEADER, MOMENTUM_LEADER and VWAP_PULLBACK are not claimed as fill-perfect historical replays. Catalyst and positive MTF bonuses were not fabricated. The current-universe sample also has survivorship/selection bias risk.

## Action implied by the evidence

Do not spend another month waiting for the unchanged combined V7.2 system to prove itself, and do not move to real-money testing. Keep V7.2 production rules frozen for the existing forward record. Stop prioritizing V8 and the other failing setup families.

The only research question still justified by these results is whether `SWING_CONTINUATION` survives a **full-fidelity, point-in-time test** with better historical data and exact MTF/entry semantics. If that focused test does not produce positive Validation and Holdout with robust stress performance, the current strategy family should be retired rather than re-engineered.
