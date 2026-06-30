import re
from dataclasses import dataclass, field
from typing import Optional

from vehicle_fields import is_vehicle_listing as _is_vehicle_listing

_ISO_TITLE_PATTERNS = [
    r"\biso\b",
    r"\bin search of\b",
    r"\blooking for\b",
    r"\bi want to buy\b",
    r"\bwtb\b",
    r"\bwanted:\b",
    r"\[wanted\]",
    r"\[wtb\]",
    r"\[iso\]",
]
_ISO_COMPILED = [re.compile(p, re.IGNORECASE) for p in _ISO_TITLE_PATTERNS]


@dataclass
class Listing:
    title: str
    url: str
    source: str
    price: str = "free"
    location: str = ""
    is_paid_wanted: bool = False
    paid_item_name: str = ""
    category_id: str = "other"
    category_label: str = "Other"
    category_icon: str = "📌"
    platform_label: str = ""
    platform_icon: str = "🔗"
    image_url: str = ""
    image_urls: list = field(default_factory=list)
    description: str = ""
    reply_email: str = ""
    reply_url: str = ""
    also_on: list = field(default_factory=list)


def _matches(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(str(k).lower() in t for k in keywords)


def _is_machinery(title: str, rules: dict) -> bool:
    t = title.lower()
    return any(k in t for k in rules.get("machinery", {}).get("applies_to", []))


def is_priority_match(title: str, search: dict) -> bool:
    return _matches(title, search.get("priority_keywords", []))


def trailer_keywords(search: dict) -> list[str]:
    kws: set[str] = set()
    for key in ("priority_keywords", "current_focus"):
        for k in search.get(key, []):
            if "trailer" in k.lower() or k.lower() in ("gooseneck",):
                kws.add(k)
    for wanted in search.get("paid_wanted", []):
        if wanted.get("name") == "trailer":
            kws.update(wanted.get("keywords", []))
    if not kws:
        kws.update({"trailer", "utility trailer", "cargo trailer"})
    return sorted(kws)


def title_mentions_trailer(title: str) -> bool:
    """Plain title check — no scoring, no paid/free rules."""
    t = (title or "").lower()
    if "trailer" not in t:
        return False
    if any(x in t for x in ("movie trailer", "film trailer", "trailer park")):
        return False
    return True


_DEFAULT_TRAILER_EXCLUDE = (
    "bike", "bicycle", "motorcycle", "dirt bike", "e-bike", "ebike",
    "atv", "utv", "quad", "four wheeler", "4 wheeler", "side by side", "sxs",
    "boat", "pontoon", "jet ski", "jetski", "kayak", "canoe",
    "enclosed trailer", "enclosed cargo", "enclosed utility", "car hauler",
    "motorcycle trailer", "bike trailer", "bicycle trailer",
)


def trailer_exclude_keywords(search: Optional[dict]) -> list[str]:
    if not search:
        return list(_DEFAULT_TRAILER_EXCLUDE)
    custom = search.get("trailer_exclude") or []
    base = list(_DEFAULT_TRAILER_EXCLUDE)
    for kw in custom:
        k = str(kw).lower().strip()
        if k and k not in base:
            base.append(k)
    return base


def is_excluded_trailer_listing(
    title: str,
    description: str = "",
    search: Optional[dict] = None,
) -> bool:
    """Drop bikes/ATV/boat/enclosed trailer listings from trailer hunt."""
    blob = f"{title} {description}".lower()
    for kw in trailer_exclude_keywords(search):
        if kw in blob:
            return True
    return False


def is_trailer_listing(title: str, description: str = "", search: Optional[dict] = None) -> bool:
    if not title_mentions_trailer(title):
        return False
    if is_excluded_trailer_listing(title, description, search):
        return False
    return True


def matches_trailer(listing: Listing, search: dict) -> bool:
    if title_mentions_trailer(listing.title):
        return True
    if listing.is_paid_wanted and listing.paid_item_name == "trailer":
        return True
    text = f"{listing.title} {listing.description}".lower()
    return _matches(text, trailer_keywords(search))


def is_vehicle_listing(title: str, description: str, category_id: str, search: dict) -> bool:
    return _is_vehicle_listing(title, description, category_id, search)


def is_iso_post(title: str, search: Optional[dict] = None) -> bool:
    """In Search Of / wanted posts — people looking for items, not offering."""
    t = title.lower().strip()
    for pat in _ISO_COMPILED:
        if pat.search(t):
            return True
    if search:
        for kw in search.get("exclude", {}).get("iso_keywords", []):
            if kw.lower() in t:
                return True
    # "Wanted wood" style — starts with wanted (not "unwanted")
    if re.match(r"^wanted\b", t):
        return True
    return False


def is_free_by_price(price: str, *, is_paid_wanted: bool = False) -> bool:
    """True when listing price field is free/$0 — not based on title/description."""
    if is_paid_wanted:
        return False
    p = (price or "").strip().lower()
    if p in ("free", "$0", "0", ""):
        return True
    if p.startswith("$"):
        try:
            return float(p.replace("$", "").replace(",", "")) == 0
        except ValueError:
            pass
    return False


def is_free_listing(price: str, title: str, *, is_paid_wanted: bool = False) -> bool:
    if is_paid_wanted:
        return False
    if is_free_by_price(price, is_paid_wanted=False):
        return True
    if "free" in (title or "").lower():
        return True
    return False


def score_listing(listing: Listing, cfg: dict) -> int:
    search = cfg["search"]
    scoring = cfg["scoring"]
    title = listing.title
    score = 0

    if listing.category_id in search.get("exclude_categories", []):
        if listing.source != "freecycle" and not listing.is_paid_wanted and not is_priority_match(title, search):
            return -100

    if is_iso_post(title, search):
        return -100

    for kw in search["exclude"].get("title_keywords", []):
        if kw.lower() in title.lower():
            return -100

    for kw in search["exclude"].get("penalize_keywords", []):
        if kw.lower() in title.lower():
            score += scoring["penalties"].get("penalize_keyword", -20)

    rules = search.get("condition_rules", {})
    if _is_machinery(title, rules):
        bad = ["for parts", "not working", "needs repair", "project", "non-working", "doesn't work", "wont start", "won't start"]
        if any(b in title.lower() for b in bad):
            return -100

    if listing.is_paid_wanted:
        score += scoring["weights"].get("paid_wanted_match", 50)

    if is_priority_match(title, search):
        score += scoring["weights"].get("priority_match", 45)

    if _matches(title, search.get("current_focus", [])):
        score += scoring["weights"].get("current_focus_match", 35)
    if _matches(title, search.get("farm_garden", [])):
        score += scoring["weights"].get("farm_garden_match", 20)
    if _matches(title, search.get("resale_high_value", [])):
        score += scoring["weights"].get("resale_value", 25)
    if _matches(title, search.get("strong_signals", [])):
        score += scoring["weights"].get("strong_signal", 15)

    pref = search.get("make_preference") or {}
    pref_make = (pref.get("make") or "").lower()
    if pref_make:
        kws = pref.get("keywords") or []
        if _matches(f"{listing.title} {listing.description}", kws):
            score += scoring["weights"].get("make_preference_match", 30)

    t_lower = title.lower()
    base = listing.source.split(":")[0]
    platform_boost = {
        "freecycle": 50,
        "facebook": 45,
        "facebook_group": 50,
        "offerup": 45,
        "trash_nothing": 45,
        "nextdoor": 45,
    }
    score += platform_boost.get(base, 0)
    if not platform_boost.get(base) and (
        "free" in t_lower or is_free_by_price(listing.price, is_paid_wanted=listing.is_paid_wanted)
    ):
        score += 15

    return score


def tier_for(listing: Listing, score: int, cfg: dict) -> str:
    if listing.is_paid_wanted:
        return "paid_wanted"
    if score >= cfg["scoring"]["tiers"]["worth_the_drive"]["min_score"]:
        return "worth_the_drive"
    if score >= cfg["scoring"]["tiers"]["must_email_min_score"]:
        return "everything_else"
    return "skip"
