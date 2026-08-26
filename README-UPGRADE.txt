Trading Decision Upgrade

Replace these files:
- config/universe.yml
- src/trading_bot/decision.py
- src/trading_bot/position_manager.py
- src/trading_bot/telegram.py
- src/trading_bot/dashboard.py

Then:
git add .
git commit -m "Upgrade trade decision and exit management"
git pull --rebase origin main
git push origin main

Changes:
- removes legacy SQ ticker
- adds explicit BUY_NOW / WAIT_FOR_ENTRY / DO_NOT_CHASE / NO_TRADE actions
- prevents zero-share recommendations
- improves position management: partial profit, breakeven stop, exit triggers
- makes Telegram alerts actionable
- shows action/instruction prominently on dashboard
