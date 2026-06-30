"""Trash Nothing — Freecycle + Buy Nothing aggregator (requires API key)."""

import os
from typing import List

import requests

from scrapers.craigslist import HEADERS, RawListing

_session = requests.Session()
_session.trust_env = False


def fetch_offers(cfg: dict) -> List[RawListing]:
    api_key = os.getenv("TRASHNOTHING_API_KEY", "").strip()
    if not api_key:
        print("  trash_nothing: set TRASHNOTHING_API_KEY in .env (trashnothing.com/developer)", flush=True)
        return []

    lat = cfg.get("latitude", 38.8339)
    lng = cfg.get("longitude", -104.8214)
    radius = cfg.get("radius", 75)
    # Trash Nothing API uses meters (max ~50 mi / 80500 m); profile yaml uses miles when value is small.
    radius_m = int(radius * 1609.34) if radius < 1000 else int(radius)
    radius_m = min(radius_m, 80500)
    terms = cfg.get("search_terms") or ["free", "garden", "farm"]
    out: List[RawListing] = []
    seen = set()

    for term in terms:
        try:
            r = _session.get(
                "https://trashnothing.com/api/v1.4/posts/search",
                params={
                    "api_key": api_key,
                    "search": term,
                    "types": "offer",
                    "sources": "trashnothing,open_archive_groups",
                    "latitude": lat,
                    "longitude": lng,
                    "radius": radius_m,
                    "per_page": 40,
                    "page": 1,
                },
                headers={**HEADERS, "Accept": "application/json"},
                timeout=25,
            )
            if not r.ok:
                print(f"  trash_nothing q={term!r}: HTTP {r.status_code}", flush=True)
                continue
            data = r.json()
        except Exception as e:
            print(f"  trash_nothing q={term!r}: {e}", flush=True)
            continue

        for post in data.get("posts") or []:
            if (post.get("type") or "").lower() != "offer":
                continue
            title = (post.get("title") or "").strip()
            pid = post.get("post_id")
            if not title or not pid:
                continue
            url = f"https://trashnothing.com/post/{pid}"
            if url in seen:
                continue
            seen.add(url)
            photos = post.get("photos") or []
            image_urls: list[str] = []
            for photo in photos:
                if isinstance(photo, dict):
                    url = photo.get("url") or photo.get("thumb_url") or ""
                else:
                    url = str(photo)
                if url and url not in image_urls:
                    image_urls.append(url)
            image_url = image_urls[0] if image_urls else ""
            out.append(
                RawListing(
                    title=title,
                    url=url,
                    price="free",
                    location=f"Trash Nothing · {term}",
                    posting_id=str(pid),
                    image_url=image_url,
                    description=(post.get("content") or "").strip(),
                    reply_url=url,
                    image_urls=image_urls,
                )
            )

    if out:
        print(f"  trash_nothing: {len(out)} offers", flush=True)
    return out
