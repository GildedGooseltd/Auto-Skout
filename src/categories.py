def categorize(title: str, categories: list[dict], *, is_paid_wanted: bool = False) -> dict:
    if is_paid_wanted:
        for cat in categories:
            if cat["id"] == "paid_wanted":
                return cat

    t = title.lower()
    best = None
    best_hits = 0
    for cat in categories:
        if cat["id"] in ("other", "paid_wanted"):
            continue
        hits = sum(1 for kw in cat.get("keywords", []) if kw.lower() in t)
        if hits > best_hits:
            best_hits = hits
            best = cat

    if best:
        return best
    for cat in categories:
        if cat["id"] == "other":
            return cat
    return {"id": "other", "label": "Other", "icon": "📌", "keywords": []}


def platform_display(source: str, platform_icons: dict) -> dict:
    if source in platform_icons:
        return platform_icons[source]
    key = source.split(":")[0] if ":" in source else source
    if key in platform_icons:
        return platform_icons[key]
    return {"label": source.replace("web:", "").replace("_", " ").title(), "icon": "🔗"}
