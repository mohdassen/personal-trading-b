"""Alpaca integration for market data and PAPER execution.

Safety invariants:
- Live order placement is intentionally disabled in code.
- Missing credentials never fall back to another account or endpoint.
- Secrets are never returned by diagnostics.
- Paper orders require an explicit environment unlock in addition to mode=paper.

This module exists to create a real broker/data path without changing V7.2
production strategy rules.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
import requests


DATA_URL = "https://data.alpaca.markets"
PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
LIVE_TRADING_URL = "https://api.alpaca.markets"
PAPER_ORDER_UNLOCK = "YES_I_WANT_PAPER_ORDERS"


class AlpacaError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlpacaCredentials:
    key_id: str
    secret_key: str

    @classmethod
    def from_env(cls) -> "AlpacaCredentials":
        key = os.getenv("APCA_API_KEY_ID", "").strip()
        secret = os.getenv("APCA_API_SECRET_KEY", "").strip()
        if not key or not secret:
            raise AlpacaError(
                "Missing APCA_API_KEY_ID/APCA_API_SECRET_KEY. "
                "Use paper credentials first; never paste secrets into source code."
            )
        return cls(key, secret)

    def headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
        }


class _Http:
    def __init__(self, credentials: AlpacaCredentials, timeout: int = 30):
        self.credentials = credentials
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(credentials.headers())
        self.session.headers.update({"User-Agent": "personal-trading-b/edge-validation"})

    def request(self, method: str, url: str, *, params=None, json=None) -> Any:
        for attempt in range(4):
            r = self.session.request(method, url, params=params, json=json, timeout=self.timeout)
            if r.status_code == 429 and attempt < 3:
                retry_after = float(r.headers.get("Retry-After", "1") or 1)
                time.sleep(min(max(retry_after, 1), 10))
                continue
            if r.status_code >= 400:
                req_id = r.headers.get("X-Request-ID")
                body = (r.text or "")[:400]
                raise AlpacaError(f"Alpaca HTTP {r.status_code}; request_id={req_id}; body={body}")
            if not r.content:
                return None
            try:
                return r.json()
            except Exception as exc:
                raise AlpacaError(f"Alpaca returned non-JSON response: {exc}") from exc
        raise AlpacaError("Alpaca request retry limit exceeded")


class AlpacaMarketData:
    """Historical/current US-equity bar access using official Alpaca REST API."""

    def __init__(self, credentials: Optional[AlpacaCredentials] = None, feed: Optional[str] = None):
        self.credentials = credentials or AlpacaCredentials.from_env()
        self.http = _Http(self.credentials)
        self.feed = (feed or os.getenv("ALPACA_DATA_FEED", "iex")).strip().lower()
        if self.feed not in {"iex", "sip", "delayed_sip"}:
            raise AlpacaError(f"Unsupported ALPACA_DATA_FEED={self.feed}")

    def bars(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
        *,
        adjustment: str = "all",
        limit: int = 10000,
    ) -> pd.DataFrame:
        """Return a UTC-indexed OHLCV frame for one symbol, with pagination."""
        symbol = str(symbol).upper().strip()
        if not symbol:
            raise ValueError("symbol is required")
        rows: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        while True:
            params: Dict[str, Any] = {
                "timeframe": timeframe,
                "start": start,
                "end": end,
                "limit": int(limit),
                "adjustment": adjustment,
                "feed": self.feed,
                "sort": "asc",
            }
            if page_token:
                params["page_token"] = page_token
            payload = self.http.request(
                "GET", f"{DATA_URL}/v2/stocks/{symbol}/bars", params=params
            ) or {}
            rows.extend(payload.get("bars") or [])
            page_token = payload.get("next_page_token")
            if not page_token:
                break

        if not rows:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "TradeCount", "VWAP"])

        frame = pd.DataFrame(rows)
        frame["timestamp"] = pd.to_datetime(frame["t"], utc=True)
        frame = frame.set_index("timestamp").sort_index()
        out = pd.DataFrame(index=frame.index)
        out["Open"] = pd.to_numeric(frame["o"], errors="coerce")
        out["High"] = pd.to_numeric(frame["h"], errors="coerce")
        out["Low"] = pd.to_numeric(frame["l"], errors="coerce")
        out["Close"] = pd.to_numeric(frame["c"], errors="coerce")
        out["Volume"] = pd.to_numeric(frame["v"], errors="coerce").fillna(0)
        out["TradeCount"] = pd.to_numeric(frame.get("n"), errors="coerce") if "n" in frame else 0
        out["VWAP"] = pd.to_numeric(frame.get("vw"), errors="coerce") if "vw" in frame else float("nan")
        return out.dropna(subset=["Open", "High", "Low", "Close"])

    def latest_bar(self, symbol: str) -> Dict[str, Any]:
        payload = self.http.request(
            "GET",
            f"{DATA_URL}/v2/stocks/{str(symbol).upper().strip()}/bars/latest",
            params={"feed": self.feed},
        ) or {}
        return dict(payload.get("bar") or {})


class AlpacaBroker:
    """Minimal account/order adapter. Live orders are hard-disabled."""

    def __init__(
        self,
        mode: str = "read_only",
        credentials: Optional[AlpacaCredentials] = None,
    ):
        self.mode = str(mode).strip().lower()
        if self.mode not in {"read_only", "paper", "live"}:
            raise ValueError("mode must be read_only, paper, or live")
        if self.mode == "live":
            raise AlpacaError(
                "LIVE EXECUTION IS LOCKED. Current project state authorizes only read_only/paper."
            )
        self.credentials = credentials or AlpacaCredentials.from_env()
        self.http = _Http(self.credentials)
        self.base_url = PAPER_TRADING_URL

    def _get(self, path: str, params=None) -> Any:
        return self.http.request("GET", f"{self.base_url}{path}", params=params)

    def _post(self, path: str, payload: Dict[str, Any]) -> Any:
        return self.http.request("POST", f"{self.base_url}{path}", json=payload)

    def account(self) -> Dict[str, Any]:
        return dict(self._get("/v2/account") or {})

    def positions(self) -> List[Dict[str, Any]]:
        data = self._get("/v2/positions") or []
        return list(data) if isinstance(data, list) else []

    def orders(self, status: str = "open") -> List[Dict[str, Any]]:
        data = self._get("/v2/orders", params={"status": status, "direction": "desc"}) or []
        return list(data) if isinstance(data, list) else []

    def diagnostics(self) -> Dict[str, Any]:
        acct = self.account()
        return {
            "mode": self.mode,
            "account_status": acct.get("status"),
            "currency": acct.get("currency"),
            "equity": acct.get("equity"),
            "cash": acct.get("cash"),
            "buying_power": acct.get("buying_power"),
            "trading_blocked": acct.get("trading_blocked"),
            "account_blocked": acct.get("account_blocked"),
            "pattern_day_trader": acct.get("pattern_day_trader"),
            "open_positions": len(self.positions()),
            "open_orders": len(self.orders("open")),
            "live_execution_authorized": False,
            "secrets_exposed": False,
        }

    def submit_paper_bracket_buy(
        self,
        *,
        symbol: str,
        qty: float,
        limit_price: float,
        stop_price: float,
        target_price: float,
        client_order_id: str,
    ) -> Dict[str, Any]:
        if self.mode != "paper":
            raise AlpacaError("Order submission requires mode=paper")
        if os.getenv("ALPACA_PAPER_ORDER_UNLOCK", "") != PAPER_ORDER_UNLOCK:
            raise AlpacaError(
                "Paper order blocked. Set ALPACA_PAPER_ORDER_UNLOCK=" + PAPER_ORDER_UNLOCK
            )
        if qty <= 0:
            raise ValueError("qty must be > 0")
        if not (0 < stop_price < limit_price < target_price):
            raise ValueError("Expected 0 < stop < limit entry < target")
        symbol = str(symbol).upper().strip()
        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": "buy",
            "type": "limit",
            "time_in_force": "day",
            "limit_price": f"{limit_price:.2f}",
            "order_class": "bracket",
            "take_profit": {"limit_price": f"{target_price:.2f}"},
            "stop_loss": {"stop_price": f"{stop_price:.2f}"},
            "client_order_id": str(client_order_id)[:128],
            "extended_hours": False,
        }
        return dict(self._post("/v2/orders", payload) or {})
