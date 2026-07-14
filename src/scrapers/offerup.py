"""OfferUp — parse search results from embedded __NEXT_DATA__ (no login required)."""

import json
import re
from typing import List

import requests

from scrapers.craigslist import HEADERS, RawListing
from scoring import is_trailer_listing

_session = requests.Session()
_session.trust_env = False

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)

_BROWSER_HEADERS = {
    **HEADERS,
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _price_label(raw_price) -> str:
    if raw_price in (None, "", "0", 0, "0000", "00", "0.00"):
        return "free"
    return f"${raw_price}"


def _is_free_listing(title: str, price) -> bool:
    return _price_amount(price) == 0.0


def _parse_search_html(html: str) -> List[dict]:
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    feed = (data.get("props") or {}).get("pageProps", {}).get("searchFeedResponse") or {}
    listings = []
    for tile in feed.get("looseTiles") or []:
        if tile.get("__typename") != "ModularFeedTileListing":
            continue
        listing = tile.get("listing")
        if listing:
            listings.append(listing)
    return listings


def _price_amount(raw_price) -> float:
    if raw_price in (None, "", "0", 0, "0000", "00", "0.00"):
        return 0.0
    try:
        return float(str(raw_price).replace(",", "").replace("$", "").strip())
    except ValueError:
        return 999999.0


def fetch_offers(cfg: dict) -> List[RawListing]:
    markets = cfg.get("markets") or [
        {
            "zip": cfg.get("zip", "81040"),
            "radius": cfg.get("radius", 50),
            "label": "",
        }
    ]
    free_terms = cfg.get("search_terms") or ["free", "garden", "farm", "pallet", "dirt"]
    paid_terms = cfg.get("paid_search_terms") or []
    max_price = float(cfg.get("max_price_usd", 2500))
    trailer_hunt = bool(cfg.get("trailer_hunt"))
    out: List[RawListing] = []
    seen = set()

    for market in markets:
        zip_code = str(market.get("zip", cfg.get("zip", "81040")))
        radius = int(market.get("radius", cfg.get("radius", 50)))
        mlabel = market.get("label") or zip_code

        def _search(term: str, *, paid_ok: bool) -> None:
            url = (
                f"https://offerup.com/search?q={requests.utils.quote(term)}"
                f"&zip={zip_code}&radius={radius}"
            )
            try:
                r = _session.get(url, headers=_BROWSER_HEADERS, timeout=35)
                if not r.ok:
                    print(f"  offerup {mlabel} q={term!r}: HTTP {r.status_code}", flush=True)
                    return
                batch = _parse_search_html(r.text)
            except Exception as e:
                print(f"  offerup {mlabel} q={term!r}: {e}", flush=True)
                return

            added = 0
            for item in batch:
                title = (item.get("title") or "").strip()
                lid = item.get("listingId")
                if not title or not lid:
                    continue
                price = item.get("price")
                amount = _price_amount(price)
                if paid_ok:
                    if not trailer_hunt and amount > max_price:
                        continue
                elif not _is_free_listing(title, price):
                    continue
                if trailer_hunt:
                    desc = (item.get("conditionText") or "").strip()
                    if not is_trailer_listing(title, desc):
                        continue
                listing_url = f"https://offerup.com/item/detail/{lid}"
                if listing_url in seen:
                    continue
                seen.add(listing_url)
                image = item.get("image") or {}
                image_url = image.get("url") or ""
                loc = (item.get("locationName") or zip_code).strip()
                out.append(
                    RawListing(
                        title=title,
                        url=listing_url,
                        price=_price_label(price),
                        location=f"OfferUp · {mlabel} · {loc}",
                        posting_id=str(lid),
                        image_url=image_url,
                        description=(item.get("conditionText") or "").strip(),
                        reply_url=listing_url,
                    )
                )
                added += 1
            if added:
                label = f"≤${int(max_price)}" if paid_ok else "free"
                print(f"  offerup {mlabel} q={term!r}: {added} ({label})", flush=True)

        for term in free_terms:
            _search(term, paid_ok=False)
        for term in paid_terms:
            _search(term, paid_ok=True)

    if out:
        print(f"  offerup: {len(out)} listings total", flush=True)
    return out
