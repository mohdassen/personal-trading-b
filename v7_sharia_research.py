"""Free AAOIFI-style research screen for V7 PAPER candidates.

This is NOT a Sharia certification or fatwa. It implements a conservative
research layer using free Yahoo/yfinance fundamentals. Missing critical data
fails closed to REVIEW_REQUIRED. AAOIFI SS(21) reference thresholds used here:
interest-bearing debt / market cap <30%, interest-taking deposits <30%, and
impermissible income <5%. Free data cannot reliably identify every impermissible
income source, so a passing result is explicitly PARTIAL.
"""
from __future__ import annotations

import math
import yfinance as yf

DEBT_LIMIT = 0.30
CASH_PROXY_LIMIT = 0.30
IMPURE_INCOME_LIMIT = 0.05
CACHE = {}

PROHIBITED_TERMS = (
    "conventional bank", "banks—", "banking", "insurance", "gambling", "casino",
    "alcohol", "brewery", "breweries", "tobacco", "pork", "adult entertainment",
    "weapons", "firearms", "defense contractor", "credit services",
)


def _f(v, default=None):
    try:
        x = float(v)
        if math.isnan(x): return default
        return x
    except Exception:
        return default


def _line(frame, names):
    try:
        if frame is None or frame.empty:
            return None
        for name in names:
            if name in frame.index:
                s = frame.loc[name].dropna()
                if len(s): return _f(s.iloc[0])
    except Exception:
        pass
    return None


def _business_status(info, sector_hint=""):
    text = " ".join(str(x or "") for x in (
        sector_hint, info.get("sector"), info.get("industry"), info.get("longBusinessSummary")
    )).lower()
    hits = sorted({term for term in PROHIBITED_TERMS if term in text})
    return ("FAIL", hits) if hits else ("PASS", [])


def research_screen(symbol, sector_hint=""):
    key = (str(symbol).upper(), str(sector_hint or ""))
    if key in CACHE:
        return CACHE[key]
    out = {
        "method": "AAOIFI_SS21_STYLE_RESEARCH_PARTIAL",
        "certified": False,
        "symbol": key[0],
        "status": "REVIEW_REQUIRED",
        "reasons": [],
        "thresholds": {"debt_to_market_cap": DEBT_LIMIT, "cash_proxy_to_market_cap": CASH_PROXY_LIMIT, "impure_income_to_revenue": IMPURE_INCOME_LIMIT},
    }
    try:
        t = yf.Ticker(key[0])
        try: info = t.info or {}
        except Exception: info = {}
        business, hits = _business_status(info, sector_hint)
        out["business_activity"] = {"status": business, "prohibited_term_hits": hits}
        if business == "FAIL":
            out["status"] = "FAIL"
            out["reasons"].append("PROHIBITED_OR_REVIEW_SENSITIVE_CORE_ACTIVITY")
            CACHE[key] = out; return out

        market_cap = None
        try:
            fi = t.fast_info
            market_cap = _f(fi.get("market_cap") if hasattr(fi, "get") else fi["market_cap"])
        except Exception:
            market_cap = _f(info.get("marketCap"))
        bs = t.quarterly_balance_sheet
        debt = _line(bs, ("Total Debt",))
        if debt is None:
            long_debt = _line(bs, ("Long Term Debt", "Long Term Debt And Capital Lease Obligation")) or 0.0
            current_debt = _line(bs, ("Current Debt", "Current Debt And Capital Lease Obligation")) or 0.0
            debt = long_debt + current_debt if long_debt or current_debt else None
        cash_proxy = _line(bs, (
            "Cash Cash Equivalents And Short Term Investments",
            "Cash And Cash Equivalents",
            "Cash Financial",
        ))

        debt_ratio = debt / market_cap if debt is not None and market_cap else None
        cash_ratio = cash_proxy / market_cap if cash_proxy is not None and market_cap else None
        out["financials"] = {
            "market_cap": market_cap,
            "interest_bearing_debt_proxy": debt,
            "cash_interest_deposit_proxy": cash_proxy,
            "debt_to_market_cap": round(debt_ratio, 4) if debt_ratio is not None else None,
            "cash_proxy_to_market_cap": round(cash_ratio, 4) if cash_ratio is not None else None,
        }
        if debt_ratio is not None and debt_ratio >= DEBT_LIMIT:
            out["status"] = "FAIL"; out["reasons"].append("DEBT_RATIO_GE_30PCT")
        if cash_ratio is not None and cash_ratio >= CASH_PROXY_LIMIT:
            out["status"] = "FAIL"; out["reasons"].append("CASH_INTEREST_DEPOSIT_PROXY_GE_30PCT")
        if out["status"] == "FAIL":
            CACHE[key] = out; return out
        if debt_ratio is None or cash_ratio is None:
            out["reasons"].append("MISSING_CRITICAL_FREE_FINANCIAL_DATA")
            CACHE[key] = out; return out

        income = t.quarterly_income_stmt
        revenue = _line(income, ("Total Revenue", "Operating Revenue"))
        interest_income = _line(income, ("Interest Income Non Operating", "Interest Income"))
        impure_ratio = abs(interest_income) / revenue if interest_income is not None and revenue and revenue > 0 else None
        out["financials"]["interest_income_proxy"] = interest_income
        out["financials"]["total_revenue"] = revenue
        out["financials"]["impure_income_proxy_ratio"] = round(impure_ratio, 4) if impure_ratio is not None else None
        if impure_ratio is not None and impure_ratio >= IMPURE_INCOME_LIMIT:
            out["status"] = "FAIL"; out["reasons"].append("INTEREST_INCOME_PROXY_GE_5PCT")
        else:
            out["status"] = "RESEARCH_PASS_PARTIAL"
            if impure_ratio is None:
                out["reasons"].append("IMPURE_INCOME_NOT_FULLY_DISCLOSED_IN_FREE_DATA")
            out["reasons"].append("NOT_A_FORMAL_SHARIA_CERTIFICATION")
    except Exception as exc:
        out["reasons"].append(f"FREE_DATA_ERROR:{type(exc).__name__}")
    CACHE[key] = out
    return out


def install(engine):
    original_pick = engine._pick_diverse
    def sharia_screened_picks(rows, n=3):
        # Ask the existing diversification logic for a wider shortlist, then
        # fail closed on explicit Fail/Review Required and keep up to n.
        shortlist = original_pick(rows, max(n * 2, n))
        selected = []
        for candidate in shortlist:
            screen = research_screen(candidate.get("symbol"), candidate.get("sector", ""))
            candidate["sharia_research"] = screen
            if screen.get("status") != "RESEARCH_PASS_PARTIAL":
                continue
            selected.append(candidate)
            if len(selected) >= n:
                break
        return selected
    engine._pick_diverse = sharia_screened_picks
