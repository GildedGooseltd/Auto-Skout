"""Cross-source duplicate detection — same item on Craigslist + Facebook, etc."""

import re
from difflib import SequenceMatcher

from scoring import Listing

_STOP = frozenset({
    "a", "an", "the", "and", "or", "for", "with", "free", "obo", "new", "used",
    "sale", "pick", "up", "available", "still", "your", "our", "my", "in", "on",
    "at", "to", "of", "is", "it", "be", "as", "from", "all", "set", "lot",
})

_CITY_TOKENS = (
    "pueblo", "denver", "springs", "colorado springs", "cos", "gardner",
    "walsenburg", "canon city", "trinidad", "aurora", "arvada", "fountain",
    "monument", "castle rock", "la veta", "westcliffe",
    "miami", "tampa", "orlando", "jacksonville", "fort lauderdale", "tallahassee",
    "naples", "sarasota", "gainesville", "pensacola", "west palm", "boca raton",
)


def _tokens(title: str) -> frozenset[str]:
    words = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).split()
    return frozenset(w for w in words if len(w) > 2 and w not in _STOP)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _title_ratio(a: str, b: str) -> float:
    na = re.sub(r"[^a-z0-9]+", " ", (a or "").lower()).strip()
    nb = re.sub(r"[^a-z0-9]+", " ", (b or "").lower()).strip()
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _location_overlap(a: str, b: str) -> bool:
    la = (a or "").lower()
    lb = (b or "").lower()
    for city in _CITY_TOKENS:
        if city in la and city in lb:
            return True
    if la and lb and (la in lb or lb in la):
        return True
    return False


def _source_base(source: str) -> str:
    return source.split(":")[0] if ":" in source else source


def are_duplicates(a: Listing, b: Listing) -> bool:
    if a.url == b.url:
        return True
    a_web = _source_base(a.source) == "web"
    b_web = _source_base(b.source) == "web"
    # Different dealer sites (Cars.com vs TrueCar) — keep both rows
    if a_web and b_web and a.source != b.source:
        return False
    ta, tb = _tokens(a.title), _tokens(b.title)
    jac = _jaccard(ta, tb)
    ratio = _title_ratio(a.title, b.title)
    if jac >= 0.88 or ratio >= 0.92:
        return True
    if jac >= 0.72 and ratio >= 0.78 and _location_overlap(a.location, b.location):
        return True
    return False


def _source_rank(source: str) -> int:
    base = source.split(":")[0] if ":" in source else source
    return {
        "web": 6,
        "freecycle": 5,
        "facebook_group": 4,
        "facebook": 4,
        "trash_nothing": 4,
        "nextdoor": 3,
        "offerup": 3,
        "auction": 6,
        "craigslist": 1,
    }.get(base, 1)


def _merge_into(keep: Listing, drop: Listing) -> None:
    if drop.platform_label and drop.platform_label not in keep.also_on:
        keep.also_on.append(drop.platform_label)
    if drop.image_url and not keep.image_url:
        keep.image_url = drop.image_url
        keep.image_urls = list(drop.image_urls or [])
    if drop.description and len(drop.description) > len(getattr(keep, "description", "") or ""):
        keep.description = drop.description
    if _source_rank(drop.source) > _source_rank(keep.source):
        keep.source = drop.source
        keep.platform_label = drop.platform_label
        keep.platform_icon = drop.platform_icon
        keep.url = drop.url or keep.url


def _bucket_key(title: str) -> str:
    words = sorted(_tokens(title))
    if not words:
        return ""
    return words[0][:12]


def dedupe_listings(listings: list[Listing]) -> tuple[list[Listing], int]:
    kept: list[Listing] = []
    removed = 0
    buckets: dict[str, list[int]] = {}
    for listing in listings:
        merged = False
        candidates: list[int] = []
        bkey = _bucket_key(listing.title)
        if bkey:
            candidates.extend(buckets.get(bkey, []))
            for w in _tokens(listing.title):
                candidates.extend(buckets.get(w[:12], []))
        seen_idx: set[int] = set()
        for i in candidates:
            if i in seen_idx:
                continue
            seen_idx.add(i)
            if are_duplicates(kept[i], listing):
                _merge_into(kept[i], listing)
                merged = True
                removed += 1
                break
        if not merged:
            idx = len(kept)
            kept.append(listing)
            if bkey:
                buckets.setdefault(bkey, []).append(idx)
            for w in _tokens(listing.title):
                buckets.setdefault(w[:12], []).append(idx)
    return kept, removed
