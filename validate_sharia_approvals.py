"""Validate an independently supplied Sharia-screening JSON export.

Usage: python validate_sharia_approvals.py
This never grants approval or contacts a provider.
"""
import json
from datetime import date
from pathlib import Path

p=Path("config/sharia_approved.json")
records=json.loads(p.read_text(encoding="utf-8"))
if not isinstance(records,dict):
    raise SystemExit("Approval registry must be a JSON object")
approved=[]
for symbol,item in records.items():
    if not isinstance(symbol,str) or not symbol or not isinstance(item,dict):
        raise SystemExit("Malformed approval entry")
    if item.get("approved") is not True:
        continue
    source=item.get("source")
    review=item.get("review_date")
    if not isinstance(source,str) or not source.strip() or not isinstance(review,str):
        raise SystemExit(f"Missing independent source or date for {symbol}")
    try:
        age=(date.today()-date.fromisoformat(review)).days
    except ValueError:
        raise SystemExit(f"Invalid review date for {symbol}")
    if not 0<=age<=90:
        raise SystemExit(f"Stale or future Sharia screening for {symbol}")
    approved.append(symbol)
print("Current reviewed symbols:",len(approved))
if not approved:
    print("FAIL CLOSED: No independently reviewed stock approvals available")
