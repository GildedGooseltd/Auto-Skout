import re
from dataclasses import dataclass, field
from typing import Optional

from vehicle_fields import is_vehicle_listing as _is_vehicle_listing
from vehicle_fields import parse_price_usd

_SEWING_HINTS = (
    "sew", "serger", "juki", "consew", "embroidery", "ultrasonic", "sonobond",
    "seammaster", "overlock", "quilting machine", "lace machine", "walking foot",
    "textile", "blindstitch", "coverstitch", "union special", "merrow", "barudan",
)

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
    return (
        _matches(title, search.get("priority_keywords", []))
        or _matches(title, search.get("lift_focus", []))
        or _matches(title, search.get("crafts_focus", []))
        or _matches(title, search.get("mixer_focus", []))
        or _matches(title, search.get("plants_focus", []))
        or _matches(title, search.get("textile_sewing", []))
    )


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


def avion_comp_keywords(search: dict) -> list[str]:
    for bucket in search.get("paid_wanted", []) or []:
        if bucket.get("name") == "avion_comps":
            return list(bucket.get("keywords") or ["avion"])
    return ["avion", "aluminum travel trailer", "vintage travel trailer"]


def is_avion_comp_listing(title: str, description: str = "", search: Optional[dict] = None) -> bool:
    """1969 Avion sale comps — travel/RV aluminum, not utility/horse trailers."""
    blob = f"{title} {description}".lower()
    if is_excluded_trailer_listing(title, description, search):
        return False
    if "avion" in blob:
        return True
    travel_signals = (
        "travel trailer", "camper", "rv", "motorhome", "airstream", "argosy",
        "aluminum trailer", "vintage trailer", "classic trailer",
    )
    if any(s in blob for s in travel_signals):
        if any(x in blob for x in ("utility trailer", "horse trailer", "flatbed trailer",
                                   "enclosed trailer", "cargo trailer", "car hauler")):
            return False
        return True
    if title_mentions_trailer(title) and _matches(blob, avion_comp_keywords(search or {})):
        return True
    return False


def matches_trailer(listing: Listing, search: dict) -> bool:
    if title_mentions_trailer(listing.title):
        return True
    if listing.is_paid_wanted and listing.paid_item_name == "trailer":
        return True
    text = f"{listing.title} {listing.description}".lower()
    return _matches(text, trailer_keywords(search))


def is_vehicle_listing(title: str, description: str, category_id: str, search: dict) -> bool:
    return _is_vehicle_listing(title, description, category_id, search)


def is_iso_post(title: str, search: Optional[dict] = None, description: str = "") -> bool:
    """In Search Of / wanted posts — people looking for items, not offering."""
    t = title.lower().strip()
    blob = f"{title} {description}".lower()
    for pat in _ISO_COMPILED:
        if pat.search(t):
            return True
    if search:
        for kw in search.get("exclude", {}).get("iso_keywords", []):
            if kw.lower() in blob:
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


def is_free_listing(price: str, title: str = "", *, is_paid_wanted: bool = False) -> bool:
    """Price-only free check — title/description 'free' does not qualify."""
    return is_free_by_price(price, is_paid_wanted=is_paid_wanted)


def _is_sewing_related(title: str, description: str = "", search: Optional[dict] = None) -> bool:
    blob = f"{title} {description}".lower()
    if any(h in blob for h in _SEWING_HINTS):
        return True
    if search and _matches(title, search.get("textile_sewing", [])):
        return True
    return False


def textile_sewing_hard_reject(listing: Listing, search: dict) -> bool:
    """True when a sewing-related listing fails industrial / price / parts rules."""
    rules = search.get("textile_sewing_rules") or {}
    if not rules:
        return False
    title = listing.title or ""
    desc = getattr(listing, "description", "") or ""
    if not _is_sewing_related(title, desc, search):
        return False
    blob = f"{title} {desc}".lower()
    for kw in rules.get("exclude_any") or []:
        if str(kw).lower() in blob:
            return True
    require = rules.get("require_any") or []
    if require and not any(str(r).lower() in blob for r in require):
        return True
    min_price = rules.get("min_price_usd")
    if min_price is not None:
        amount = parse_price_usd(listing.price, title)
        if amount is None:
            pl = (listing.price or "").strip().lower()
            if pl in ("free", "0", "$0") or pl.startswith("free"):
                return True
        elif amount < int(min_price):
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

    if is_iso_post(title, search, getattr(listing, "description", "") or ""):
        return -100

    if textile_sewing_hard_reject(listing, search):
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

    blob = f"{title} {listing.description}".lower()
    base = listing.source.split(":")[0]
    focus_text = blob if base == "estate_sales" else title

    if _matches(focus_text, search.get("lift_focus", [])):
        score += scoring["weights"].get("lift_focus_match", 50)
    if _matches(focus_text, search.get("crafts_focus", [])):
        score += scoring["weights"].get("crafts_focus_match", 50)
    if _matches(focus_text, search.get("mixer_focus", [])):
        score += scoring["weights"].get("mixer_focus_match", 50)
    if _matches(focus_text, search.get("plants_focus", [])):
        score += scoring["weights"].get("plants_focus_match", 45)
    if _matches(focus_text, search.get("current_focus", [])):
        score += scoring["weights"].get("current_focus_match", 35)
    if _matches(focus_text, search.get("textile_sewing", [])):
        score += scoring["weights"].get("textile_sewing_match", 35)
    if _matches(focus_text, search.get("farm_garden", [])):
        score += scoring["weights"].get("farm_garden_match", 20)
    if _matches(focus_text, search.get("resale_high_value", [])):
        score += scoring["weights"].get("resale_value", 25)
    if _matches(title, search.get("strong_signals", [])):
        score += scoring["weights"].get("strong_signal", 15)

    pref = search.get("make_preference") or {}
    pref_make = (pref.get("make") or "").lower()
    if pref_make:
        kws = pref.get("keywords") or []
        if _matches(f"{listing.title} {listing.description}", kws):
            score += scoring["weights"].get("make_preference_match", 30)

    platform_boost = {
        "freecycle": 50,
        "facebook": 45,
        "facebook_group": 50,
        "offerup": 45,
        "trash_nothing": 45,
        "nextdoor": 45,
        "estate_sales": 40,
        "dealer": 50,
        "marketplace": 45,
        "auction": 40,
    }
    score += platform_boost.get(base, 0)
    if not platform_boost.get(base) and is_free_by_price(
        listing.price, is_paid_wanted=listing.is_paid_wanted
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
