"""Conservative, recency-aware headline catalyst classifier for V7 PAPER research."""
from __future__ import annotations

from datetime import datetime, timezone

POSITIVE = {
    "raises guidance": ("GUIDANCE_UP", 4), "raised guidance": ("GUIDANCE_UP", 4),
    "beats estimates": ("EARNINGS_BEAT", 3), "beat estimates": ("EARNINGS_BEAT", 3),
    "wins contract": ("CONTRACT", 4), "major contract": ("CONTRACT", 4), "awarded contract": ("CONTRACT", 4),
    "fda approval": ("APPROVAL", 5), "approved by the fda": ("APPROVAL", 5), "regulatory clearance": ("APPROVAL", 4),
    "strategic partnership": ("PARTNERSHIP", 3), "partnership": ("PARTNERSHIP", 2),
    "price target raised": ("ANALYST_UPGRADE", 2), "upgraded to": ("ANALYST_UPGRADE", 2),
    "share buyback": ("BUYBACK", 3), "stock buyback": ("BUYBACK", 3),
    "new ai agent": ("PRODUCT", 2), "launches ai": ("PRODUCT", 2), "unveils ai": ("PRODUCT", 2),
}
NEGATIVE = {
    "cuts guidance": ("GUIDANCE_DOWN", -8), "cut guidance": ("GUIDANCE_DOWN", -8), "guidance cuts": ("GUIDANCE_DOWN", -8), "guidance cut": ("GUIDANCE_DOWN", -8),
    "misses estimates": ("EARNINGS_MISS", -5), "missed estimates": ("EARNINGS_MISS", -5),
    "secondary offering": ("DILUTION", -8), "stock offering": ("DILUTION", -7), "dilution": ("DILUTION", -7),
    "investigation": ("INVESTIGATION", -7), "recall": ("RECALL", -7),
    "downgraded to": ("ANALYST_DOWNGRADE", -4), "downgrade": ("ANALYST_DOWNGRADE", -4),
    "lawsuit": ("LITIGATION", -4), "delays launch": ("DELAY", -5), "accounting irregularities": ("ACCOUNTING", -8),
}


def _headline(item):
    if not isinstance(item, dict): return None
    if item.get("title"): return str(item["title"])
    content = item.get("content")
    if isinstance(content, dict) and content.get("title"): return str(content["title"])
    return None


def _age_hours(item):
    if not isinstance(item, dict): return None
    values = [item.get("providerPublishTime"), item.get("pubDate")]
    content = item.get("content")
    if isinstance(content, dict): values += [content.get("pubDate"), content.get("displayTime")]
    for value in values:
        if value is None: continue
        try:
            if isinstance(value, (int, float)):
                ts = datetime.fromtimestamp(value, tz=timezone.utc)
            else:
                ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 3600)
        except Exception: pass
    return None


def classify_catalyst(items):
    titles, categories, contributions, ages = [], [], [], []
    for item in items or []:
        title = _headline(item)
        if not title or title in titles: continue
        age = _age_hours(item)
        if age is not None and age > 168: continue
        titles.append(title); ages.append(round(age, 1) if age is not None else None)
        low = title.lower(); matched = None
        for term, val in NEGATIVE.items():
            if term in low: matched = val; break
        if matched is None:
            for term, val in POSITIVE.items():
                if term in low: matched = val; break
        if matched is not None:
            category, weight = matched
            # Fresh news carries full weight. 1-3 day old news is attenuated.
            factor = 1.0 if age is None or age <= 24 else 0.75 if age <= 72 else 0.5
            contribution = int(round(weight * factor))
            categories.append(category); contributions.append(contribution)
        if len(titles) >= 5: break
    score = max(-10, min(5, sum(contributions)))
    sentiment = "POSITIVE" if score > 0 else "NEGATIVE" if score < 0 else "NEUTRAL_OR_UNKNOWN"
    return {
        "score": int(score), "sentiment": sentiment,
        "categories": list(dict.fromkeys(categories))[:4], "headlines": titles[:3],
        "headline_ages_hours": ages[:3], "contributions": contributions,
        "positive_hits": sum(1 for x in contributions if x > 0), "negative_hits": sum(1 for x in contributions if x < 0),
    }
