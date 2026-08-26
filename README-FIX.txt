Replace the three included files, then:
git add .
git commit -m "Fix market data reliability and block unsafe signals"
git push origin main

Primary price source is now Yahoo's chart endpoint, with yfinance fallback.
The bot stops with DATA_ERROR and sends NO TRADING SIGNALS if data validation fails.
