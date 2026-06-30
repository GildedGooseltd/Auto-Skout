"""Pickup inquiry email / copy text for Craigslist sellers."""

import re


def item_phrase(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return "this item"
    lower = t.lower()
    if re.search(r"\bplants?\b", lower):
        return "the plants"
    if re.search(r"\btrees?\b", lower):
        return "the trees"
    if re.search(r"\blumber\b|\b2x4\b|\bplywood\b", lower):
        return "the lumber"
    if "free" in lower:
        cleaned = re.sub(r"\b(free|curb alert|must go)\b", "", lower, flags=re.I).strip(" -–—")
        if cleaned:
            return f"the {cleaned[:80]}"
    return f'"{t[:100]}"'


def build_pickup_message(template: str, title: str) -> str:
    item = item_phrase(title)
    msg = template.replace("{item}", item).replace("{title}", title or "this item")
    msg = msg.replace("{Item}", item[0].upper() + item[1:] if item else "")
    return msg.strip()
