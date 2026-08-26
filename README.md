# Personal Trading Assistant Ultimate

Decision-support system for US stocks with manual execution in Sahm.

## Included in this single release
- Dynamic scanner across ~80 liquid US stocks
- Separate DAY and SWING strategy engines
- SPY/QQQ/VIX market-regime filter
- Earnings/news event-risk protection
- STRONG_BUY / BUY / WATCH / WAIT / BLOCKED decisions
- Exact entry zone, stop-loss, targets and risk/reward
- Position sizing from account equity and risk limits
- Portfolio exposure gates and max-open-position limits
- Position monitoring with HOLD / TAKE_PARTIAL / EXIT guidance
- Telegram alerts with duplicate suppression
- Trade journal and realized performance metrics
- Weekly simplified swing backtest
- Mobile GitHub Pages command center
- GitHub Action to record manual Sahm BUY/SELL executions

## GitHub Secrets
Keep these existing secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## GitHub Variables
Recommended:
- `ACCOUNT_EQUITY_USD`
- `RISK_PER_TRADE_PCT`
- `MAX_POSITION_PCT`

## Workflows
- **Trading Decision Engine**: scheduled every 15 minutes on weekdays; Python skips scans outside the regular US session.
- **Record Manual Sahm Trade**: after you actually buy/sell in Sahm, record the execution so the bot can manage the position and calculate performance.
- **Weekly Backtest**: simplified historical validation for the swing engine.

## Important limitations
This is not a guaranteed-profit system. Yahoo Finance can be delayed, incomplete or rate-limited and is not execution-grade data. News/earnings data can also be incomplete. Confirm live price, corporate events and order details in Sahm before executing. Start with paper/small-size validation and evaluate the actual trade journal before increasing capital.
