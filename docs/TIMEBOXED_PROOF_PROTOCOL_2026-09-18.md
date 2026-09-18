# Time-boxed proof protocol — 2026-09-18

## Objective
Reach a real, auditable trading decision in a bounded time. Passage of time alone is not progress.

## Frozen candidate
IBS Lower-Band rule version `IBS-FROZEN-2026-09-18-r1`.

## Parallel prospective basket
QQQ, TSLA, NOW, PANW, CRWD, SNOW, DDOG, NET, SHOP, UBER, ABNB, BKNG, MCD.

The basket and gate are frozen before any post-2026-09-18 outcomes are observed. No symbol may be removed because it loses and no threshold may be changed because results disappoint.

## Execution assumption
Signal uses completed daily close only. Entry and exit shadow fills occur at the next trading day's open. Round-trip cost assumption: 30 bps.

## Promotion gate
All conditions must be true:
- at least 20 closed forward trades;
- at least 12 unique signal-date clusters;
- at least 8 symbols represented by a closed trade;
- trade-level profit factor >= 1.25;
- average net trade return >= +0.10%;
- signal-cluster profit factor >= 1.20;
- cluster equity max drawdown <= 12%.

## Deadline
2026-11-06.

If the gate is not met by the deadline, status becomes `TIMEBOX_EXPIRED_NO_PROOF`. The deadline is not extended. The candidate is rejected/replaced for the user's stated objective rather than waiting indefinitely.

## Safety
This is shadow evidence only. Live execution remains locked.
