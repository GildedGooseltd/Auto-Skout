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


def also_entry(listing: Listing) -> dict:
    return {
        "platform": listing.platform_label or _source_base(listing.source),
        "source": listing.source,
        "url": listing.url or "",
        "platform_icon": listing.platform_icon or "🔗",
    }


def _also_has_url(also_on: list, url: str) -> bool:
    if not url:
        return True
    for entry in also_on:
        if isinstance(entry, dict) and entry.get("url") == url:
            return True
    return False


def append_also(keep: Listing, listing: Listing) -> None:
    entry = also_entry(listing)
    if not entry["url"] or entry["url"] == keep.url:
        return
    if _also_has_url(keep.also_on, entry["url"]):
        return
    keep.also_on.append(entry)


def normalize_also_on(raw: list, *, primary_url: str = "") -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for entry in raw or []:
        if isinstance(entry, str):
            continue
        url = (entry.get("url") or "").strip()
        if not url or url in seen or url == primary_url:
            continue
        seen.add(url)
        out.append({
            "platform": entry.get("platform") or _source_base(entry.get("source", "")),
            "source": entry.get("source") or "",
            "url": url,
            "platform_icon": entry.get("platform_icon") or "🔗",
        })
    return out


def pick_open_url(
    url: str,
    source: str,
    platform: str,
    also_on: list,
    *,
    platform_icon: str = "🔗",
) -> dict:
    """Prefer non-Craigslist links when duplicates exist."""
    primary = {
        "platform": platform or _source_base(source),
        "source": source,
        "url": url or "",
        "platform_icon": platform_icon or "🔗",
    }
    alts = normalize_also_on(also_on, primary_url=url)
    candidates = [primary] + [a for a in alts if a["url"] != url]

    def sort_key(entry: dict) -> tuple:
        base = _source_base(entry.get("source") or "")
        is_cl = 1 if base == "craigslist" else 0
        return (is_cl, -_source_rank(entry.get("source") or ""))

    for entry in sorted(candidates, key=sort_key):
        if entry.get("url"):
            return entry
    return primary


def verify_url(url: str, session=None) -> bool:
    """HEAD/GET check — Craigslist always assumed OK (often blocks bots)."""
    if not url or not str(url).startswith("http"):
        return False
    if "craigslist.org" in url.lower():
        return True
    try:
        import requests
    except ImportError:
        return True
    sess = session or requests.Session()
    if session is None:
        sess.headers.update({"User-Agent": "Mozilla/5.0 (compatible; AutoSkout/1.0)"})
    try:
        resp = sess.head(url, timeout=8, allow_redirects=True)
        if resp.status_code < 400:
            return True
        if resp.status_code == 405:
            resp = sess.get(url, timeout=8, stream=True, allow_redirects=True)
            return resp.status_code < 400
    except Exception:
        return False
    return False


def pick_verified_open_url(item: dict, session=None) -> dict:
    """Pick best open target; drop dead non-CL alternates when duplicates exist."""
    url = item.get("url") or ""
    source = item.get("source") or ""
    platform = item.get("platform") or ""
    icon = item.get("platform_icon") or "🔗"
    alts = normalize_also_on(item.get("also_on"), primary_url=url)
    check_links = session is not None and bool(alts)
    if check_links:
        alts = [a for a in alts if verify_url(a["url"], session)]
        item["also_on"] = alts
    primary = {
        "platform": platform,
        "source": source,
        "url": url,
        "platform_icon": icon,
    }
    if not check_links:
        return pick_open_url(url, source, platform, alts, platform_icon=icon)
    candidates = [primary] + alts

    def sort_key(entry: dict) -> tuple:
        base = _source_base(entry.get("source") or "")
        is_cl = 1 if base == "craigslist" else 0
        return (is_cl, -_source_rank(entry.get("source") or ""))

    for entry in sorted(candidates, key=sort_key):
        link = entry.get("url") or ""
        if not link:
            continue
        if not verify_url(link, session):
            continue
        return entry
    return primary


def _merge_into(keep: Listing, drop: Listing) -> None:
    if _source_rank(drop.source) > _source_rank(keep.source):
        old = also_entry(keep)
        keep.source = drop.source
        keep.platform_label = drop.platform_label
        keep.platform_icon = drop.platform_icon
        keep.url = drop.url or keep.url
        if old["url"] and old["url"] != keep.url and not _also_has_url(keep.also_on, old["url"]):
            keep.also_on.append(old)
    else:
        append_also(keep, drop)
    if drop.image_url and not keep.image_url:
        keep.image_url = drop.image_url
        keep.image_urls = list(drop.image_urls or [])
    if drop.description and len(drop.description) > len(getattr(keep, "description", "") or ""):
        keep.description = drop.description


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
