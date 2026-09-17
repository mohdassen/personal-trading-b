# Published Edge Protocol

Goal: find a research candidate for the trading bot without inventing or tuning a strategy after seeing results.

## Rules

1. Strategy rules come from published/long-established research ideas before the test runs.
2. No parameter search, optimizer, or post-result threshold changes in this benchmark.
3. Signals use information available at the prior close; fills occur at the next trading day's open.
4. Transaction costs are charged and stressed.
5. Results are split by time and by an independent symbol set.
6. A positive backtest is only a research candidate; it never authorizes live trading.
7. Any strategy that fails the predeclared gate is rejected rather than patched.

## First benchmark set

- 12-1 cross-sectional momentum, long-only winner portfolio. Based on the documented stock momentum effect associated with Jegadeesh & Titman: prior 3-12 month winners tend to continue outperforming over subsequent months.
- 52-week-high proximity, long-only leader portfolio. A long-established momentum variant that ranks stocks by closeness to their prior 52-week high.
- 12-month time-series momentum, long-only. Based on the sign of the asset's own trailing 12-month return; tested as a stock-market adaptation, not claimed as an exact replication of the futures literature.
- 10-month moving-average trend filter on the stock itself, long-only. Included as a simple transparent trend benchmark, not as a claim of a proprietary edge.

## Predeclared research-candidate gate

A strategy must have, after baseline costs:
- at least 24 monthly observations in validation and holdout where applicable,
- positive validation return and positive holdout return,
- positive independent-universe holdout return,
- holdout Sharpe > 0.50,
- independent holdout Sharpe > 0.25,
- remain positive under the higher cost stress,
- no single symbol may account for more than 35% of holdout P&L.

This gate is intentionally strict. Passing means only that the strategy deserves a second, higher-fidelity test.
