# Sharia screening data contract

The trading system must not infer Sharia compliance from sector, stock symbol, or model output.

## Approved provider import
Use an independently obtained, dated screening report from a qualified Sharia-screening provider. For each ticker, record:
- `approved`: boolean, explicitly confirmed by the provider
- `source`: provider name and report or verification URL
- `review_date`: ISO date YYYY-MM-DD

Store records in `config/sharia_approved.json`, e.g.:
```json
{
  "EXAMPLE": {
    "approved": false,
    "source": "Example only - NOT a verification",
    "review_date": "2026-10-10"
  }
}
```

No stocks are pre-approved. Reviews expire after 90 days. The trading assistant does not place live orders. A provider's membership/screening status may change between reviews; users must independently verify before acting. Exit risk alerts for already-held positions should not be suppressed by an expired approval.
