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


def env_number(name, config_value, default, cast=float):
    """
    Read a numeric value from an environment variable.

    If the environment variable is missing or empty,
    use the value from config. If config is also empty,
    use the supplied default.
    """
    value = os.getenv(name)

    if value is None or not value.strip():
        value = config_value

    if value is None or (
        isinstance(value, str) and not value.strip()
    ):
        value = default

    return cast(value)


def load_config():
    path = Path(
        os.getenv(
            "BOT_CONFIG",
            ROOT / "config/watchlist.yml"
        )
    )

    return yaml.safe_load(
        path.read_text(encoding="utf-8")
    )


def run() -> int:

    cfg = load_config()
    settings = cfg["settings"]

    # --------------------------------------------------
    # Trading account settings
    # --------------------------------------------------

    account_equity = env_number(
        "ACCOUNT_EQUITY_USD",
        settings.get("account_equity_usd"),
        10000
    )

    risk_pct = env_number(
        "RISK_PER_TRADE_PCT",
        settings.get("risk_per_trade_pct"),
        0.5
    )

    max_position_pct = env_number(
        "MAX_POSITION_PCT",
        settings.get("max_position_pct"),
        15
    )

    min_score_alert = int(
        settings.get("min_score_alert", 80)
    )

    results = []

    # --------------------------------------------------
    # Scan watchlist
    # --------------------------------------------------

    for symbol in cfg["watchlist"]:

        try:

            daily = fetch_daily(symbol)

            if daily.empty:
                print(
                    f"SKIP {symbol}: no daily data"
                )
                continue

            last_price = float(
                daily["Close"].iloc[-1]
            )

            avg_vol = float(
                daily["Volume"]
                .tail(20)
                .mean()
            )

            # Minimum stock price filter
            if last_price < float(
                settings.get(
                    "min_price",
                    5
                )
            ):
                continue

            # Minimum liquidity filter
            if avg_vol < float(
                settings.get(
                    "min_avg_daily_volume",
                    1_000_000
                )
            ):
                continue

            # ------------------------------------------
            # Get intraday market data
            # ------------------------------------------

            intraday = fetch_intraday(
                symbol,
                settings.get(
                    "period",
                    "5d"
                ),
                settings.get(
                    "interval",
                    "15m"
                )
            )

            # ------------------------------------------
            # Analyze stock
            # ------------------------------------------

            sig = analyze(
                symbol,
                intraday,
                account_equity,
                risk_pct,
                max_position_pct
            )

            if sig:

                results.append(
                    sig.to_dict()
                )

                print(
                    f"{symbol}: "
                    f"{sig.signal} "
                    f"{sig.score}/100"
                )

        except Exception as exc:

            print(
                f"ERROR {symbol}: {exc}"
            )

    # --------------------------------------------------
    # Save scanner results
    # --------------------------------------------------

    data_dir = ROOT / "data"
    docs_dir = ROOT / "docs"

    data_dir.mkdir(
        exist_ok=True
    )

    docs_dir.mkdir(
        exist_ok=True
    )

    signals_file = (
        data_dir / "signals.json"
    )

    signals_file.write_text(
        json.dumps(
            results,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    # --------------------------------------------------
    # Generate dashboard
    # --------------------------------------------------

    render(
        results,
        str(
            docs_dir / "index.html"
        ),
        settings.get(
            "timezone",
            "Asia/Riyadh"
        )
    )

    # --------------------------------------------------
    # Telegram alerts
    # --------------------------------------------------

    if telegram_enabled():

        top = [
            s
            for s in results
            if (
                s["score"]
                >= min_score_alert
                and
                s["signal"]
                == "BUY"
            )
        ]

        for s in sorted(
            top,
            key=lambda x: x["score"],
            reverse=True
        )[:3]:

            try:

                send_message(
                    format_signal(s)
                )

            except Exception as exc:

                print(
                    f"Telegram error "
                    f"for {s['symbol']}: "
                    f"{exc}"
                )

    print(
        f"Scan completed. "
        f"{len(results)} signals generated."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        run()
    )