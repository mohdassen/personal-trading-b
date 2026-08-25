from __future__ import annotations
import json
import os
from pathlib import Path
import yaml
from .market import fetch_intraday, fetch_daily
from .strategy import analyze
from .dashboard import render
from .telegram import enabled as telegram_enabled, send_message, format_signal

ROOT = Path(__file__).resolve().parents[2]


def load_config():
    path = Path(os.getenv("BOT_CONFIG", ROOT / "config/watchlist.yml"))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run() -> int:
    cfg = load_config()
    settings = cfg["settings"]
    account_equity = float(
        os.getenv("ACCOUNT_EQUITY_USD")
        or settings.get("account_equity_usd")
        or 10000
    )
    risk_pct = float(
        os.getenv("RISK_PER_TRADE_PCT")
        or settings.get("risk_per_trade_pct")
        or 0.5
    )
    max_position_pct = float(os.getenv("MAX_POSITION_PCT", settings.get("max_position_pct", 15)))
    min_score_alert = int(settings.get("min_score_alert", 80))
    results = []

    for symbol in cfg["watchlist"]:
        try:
            daily = fetch_daily(symbol)
            if daily.empty:
                print(f"SKIP {symbol}: no daily data")
                continue
            last_price = float(daily["Close"].iloc[-1])
            avg_vol = float(daily["Volume"].tail(20).mean())
            if last_price < float(settings.get("min_price", 5)):
                continue
            if avg_vol < float(settings.get("min_avg_daily_volume", 1_000_000)):
                continue

            intraday = fetch_intraday(symbol, settings.get("period", "5d"), settings.get("interval", "15m"))
            sig = analyze(symbol, intraday, account_equity, risk_pct, max_position_pct)
            if sig:
                results.append(sig.to_dict())
                print(f"{symbol}: {sig.signal} {sig.score}/100")
        except Exception as exc:
            print(f"ERROR {symbol}: {exc}")

    data_dir = ROOT / "data"
    docs_dir = ROOT / "docs"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "signals.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    render(results, str(docs_dir / "index.html"), settings.get("timezone", "Asia/Riyadh"))

    if telegram_enabled():
        top = [s for s in results if s["score"] >= min_score_alert and s["signal"] == "BUY"]
        for s in sorted(top, key=lambda x: x["score"], reverse=True)[:3]:
            try:
                send_message(format_signal(s))
            except Exception as exc:
                print(f"Telegram error for {s['symbol']}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
