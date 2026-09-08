"""Conservative headline catalyst classifier for V7 research/paper decisions."""
from __future__ import annotations

POSITIVE = {
    "raises guidance": ("GUIDANCE_UP", 4),
    "raised guidance": ("GUIDANCE_UP", 4),
    "beats estimates": ("EARNINGS_BEAT", 3),
    "beat estimates": ("EARNINGS_BEAT", 3),
    "wins contract": ("CONTRACT", 4),
    "major contract": ("CONTRACT", 4),
    "awarded contract": ("CONTRACT", 4),
    "fda approval": ("APPROVAL", 5),
    "approved by the fda": ("APPROVAL", 5),
    "strategic partnership": ("PARTNERSHIP", 3),
    "partnership": ("PARTNERSHIP", 2),
    "price target raised": ("ANALYST_UPGRADE", 2),
    "upgraded to": ("ANALYST_UPGRADE", 2),
    "share buyback": ("BUYBACK", 3),
    "stock buyback": ("BUYBACK", 3),
}

NEGATIVE = {
    "cuts guidance": ("GUIDANCE_DOWN", -8),
    "cut guidance": ("GUIDANCE_DOWN", -8),
    "guidance cuts": ("GUIDANCE_DOWN", -8),
    "guidance cut": ("GUIDANCE_DOWN", -8),
    "misses estimates": ("EARNINGS_MISS", -5),
    "missed estimates": ("EARNINGS_MISS", -5),
    "secondary offering": ("DILUTION", -8),
    "stock offering": ("DILUTION", -7),
    "dilution": ("DILUTION", -7),
    "investigation": ("INVESTIGATION", -7),
    "recall": ("RECALL", -7),
    "downgraded to": ("ANALYST_DOWNGRADE", -4),
    "downgrade": ("ANALYST_DOWNGRADE", -4),
    "lawsuit": ("LITIGATION", -4),
    "delays launch": ("DELAY", -5),
}


def _headline(item):
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    if title:
        return str(title)
    content = item.get("content")
    if isinstance(content, dict) and content.get("title"):
        return str(content["title"])
    return None


def classify_catalyst(items):
    titles, categories, contributions = [], [], []
    for item in items or []:
        title = _headline(item)
        if not title or title in titles:
            continue
        titles.append(title)
        low = title.lower()
        matched = False
        # Negative evidence is checked first and dominates ambiguous headlines.
        for term, (category, weight) in NEGATIVE.items():
            if term in low:
                categories.append(category); contributions.append(weight); matched = True; break
        if not matched:
            for term, (category, weight) in POSITIVE.items():
                if term in low:
                    categories.append(category); contributions.append(weight); break
        if len(titles) >= 5:
            break
    score = max(-10, min(5, sum(contributions)))
    sentiment = "POSITIVE" if score > 0 else "NEGATIVE" if score < 0 else "NEUTRAL_OR_UNKNOWN"
    return {
        "score": int(score),
        "sentiment": sentiment,
        "categories": list(dict.fromkeys(categories))[:4],
        "headlines": titles[:3],
        "contributions": contributions,
        "positive_hits": sum(1 for x in contributions if x > 0),
        "negative_hits": sum(1 for x in contributions if x < 0),
    }
