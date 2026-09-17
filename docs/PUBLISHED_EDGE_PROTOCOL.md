# Published Edge Protocol

Goal: find a research candidate for the trading bot without inventing or tuning a strategy after seeing results.

## Rules

1. Strategy rules come from published/long-established research ideas before the test runs.
2. No parameter search, optimizer, or post-result threshold changes in this benchmark.
3. Signals use information available at the prior close; fills occur at the next trading day's open.
4. Transaction costs are charged and stressed.
5. Results are split by time and by an independent symbol set.
6. A positive backtest is only a research candidate; it never authorizes live trading.
7. Any strategy that fails a predeclared gate is rejected rather than patched.

## First benchmark set

- 12-1 cross-sectional momentum, long-only winner portfolio.
- 52-week-high proximity, long-only leader portfolio.
- 12-month time-series momentum, long-only stock adaptation.
- 10-month moving-average trend filter on each stock, long-only.

## Stage 1 research-candidate gate

A strategy must have, after baseline costs:
- at least 24 monthly observations in validation and holdout where applicable,
- positive validation return and positive holdout return,
- positive independent-universe holdout return,
- holdout Sharpe > 0.50,
- independent holdout Sharpe > 0.25,
- remain positive under the higher cost stress,
- no single symbol may account for more than 35% of P&L.

Passing Stage 1 means only that the strategy deserves a harder test.

## Stage 2 benchmark-relative gate — locked before benchmark-relative results

Stage 1 can be fooled by a strong bull market. Therefore every Stage-1 candidate is compared against a simple equal-weight portfolio of the same available universe over the same months.

To survive Stage 2, a candidate must:
- beat equal-weight in validation on cumulative relative wealth,
- beat equal-weight in the 2024+ time holdout,
- beat equal-weight in the independent-symbol 2024+ holdout,
- have positive excess-return Sharpe in both 2024+ holdouts,
- still beat equal-weight in the 60 bps stress test,
- retain the Stage-1 concentration limit.

No parameters or strategy rules may be changed after seeing Stage-2 results.

## Stage 3 cross-fold robustness gate — locked after Stage 2, before cross-fold results

Only a Stage-2 survivor is tested. The combined stock list is sorted and deterministically divided into five symbol folds. The exact same fixed strategy is compared with equal-weight inside each fold over three time windows: 2016-2020, 2021-2023, and 2024+.

To survive Stage 3, the candidate must:
- beat equal-weight on relative wealth in at least 12 of the 15 fold-period cells,
- beat equal-weight in at least 4 of 5 folds during 2024+,
- have positive median excess Sharpe across folds in every time window,
- still beat equal-weight at 60 bps in at least 4 of 5 folds during 2024+.

Stage 3 is still not paper/live approval. It only justifies higher-fidelity point-in-time and forward testing.
