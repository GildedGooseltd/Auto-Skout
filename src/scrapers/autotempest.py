"""AutoTempest — aggregates Cars.com, CarGurus, TrueCar, eBay, Autotrader-class."""

from __future__ import annotations

import re
from typing import List, Optional

from scrapers.craigslist import RawListing
from scoring import is_trailer_listing
from vehicle_fields import is_truck_listing

SITE_CODES = {
    "cm": ("cars_com", "Cars.com"),
    "cmp": ("cars_com", "Cars.com"),
    "at": ("autotrader", "Autotrader"),
    "cg": ("cargurus", "CarGurus"),
    "cgu": ("cargurus", "CarGurus"),
    "tc": ("truecar", "TrueCar"),
    "eb": ("ebay_motors", "eBay Motors"),
    "pa": ("privateauto", "PrivateAuto"),
    "fb": ("facebook", "Facebook"),
    "is": ("iseecars", "iSeeCars"),
    "al": ("autolist", "Autolist"),
    "hemc": ("hemmings", "Hemmings"),
    "vast": ("autotrader", "Autotrader"),
}

_PRICE_RE = re.compile(r"\$[\d,]+")
_MILES_RE = re.compile(r"([\d,]+)\s*mi\.?", re.I)


def _parse_card(
    el_handle: dict,
    *,
    listing_mode: str = "truck",
    search: Optional[dict] = None,
) -> tuple[RawListing, str] | None:
    title = (el_handle.get("title") or "").strip()
    url = (el_handle.get("url") or "").strip()
    if not title or not url:
        return None
    text = el_handle.get("text") or ""
    price_m = _PRICE_RE.search(text)
    price = price_m.group(0) if price_m else ""
    miles_m = _MILES_RE.search(text)
    miles = miles_m.group(0) if miles_m else ""
    loc = el_handle.get("market_label") or ""
    for line in text.split("\n"):
        line = line.strip()
        if " mi. from " in line or re.search(r",\s*[A-Z]{2}\b", line):
            loc = (loc + " · " if loc else "") + (line.split("(")[0].strip() or line)
            break
    desc_parts = [p for p in (miles, loc) if p]
    description = " · ".join(desc_parts)
    blob = f"{title} {description} {text[:280]}".strip()
    if listing_mode == "trailer":
        if not is_trailer_listing(title, blob, search):
            return None
    elif not is_truck_listing(title, blob):
        return None
    sitecode = el_handle.get("sitecode") or "web"
    listing_id = el_handle.get("listing_id") or url
    return RawListing(
        title=title,
        url=url,
        price=price or "ask",
        location=loc,
        posting_id=listing_id,
        image_url=el_handle.get("image") or "",
        description=description,
    ), sitecode


def fetch_listings(
    *,
    zip_code: str = "81040",
    radius: int = 200,
    max_price_usd: int = 20000,
    min_price_usd: int = 1000,
    keywords: list[str] | None = None,
    commercial_keywords: list[str] | None = None,
    markets: Optional[list[dict]] = None,
    bodystyle: str = "truck",
    listing_mode: str = "truck",
    search: Optional[dict] = None,
    quick: bool = False,
) -> List[RawListing]:
    del quick  # always full sweep
    if listing_mode == "trailer":
        terms = [t for t in (keywords or ["trailer", "utility trailer"]) if t.strip()]
        search_passes = [(t, "") for t in terms]
    else:
        pickup_terms = [t for t in (keywords or ["2500hd", "f250", "silverado"]) if t.strip()]
        commercial_terms = [t for t in (commercial_keywords or []) if t.strip()]
        search_passes = []
        for t in pickup_terms:
            search_passes.append((t, bodystyle or "truck"))
        for t in commercial_terms:
            search_passes.append((t, ""))
    if not search_passes:
        return []

    market_list = markets or [{"zip": zip_code, "radius": radius, "label": ""}]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  autotempest: skipped — pip install playwright && playwright install chromium", flush=True)
        return []

    out: List[RawListing] = []
    seen: set[str] = set()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            for market in market_list:
                mzip = str(market.get("zip", zip_code))
                mradius = int(market.get("radius", radius))
                mlabel = market.get("label") or market.get("state") or mzip
                for term, body in search_passes:
                    url = (
                        f"https://www.autotempest.com/results?zip={mzip}"
                        f"&radius={mradius}&maxprice={max_price_usd}&minprice={min_price_usd}"
                        f"&keywords={term.replace(' ', '+')}"
                    )
                    if body:
                        url += f"&bodystyle={body}"
                    try:
                        page.goto(url, wait_until="networkidle", timeout=90000)
                        page.wait_for_selector(".search-result", timeout=45000)
                    except Exception as e:
                        print(f"  autotempest {mlabel} q={term!r}: {e}", flush=True)
                        continue

                    cards = page.eval_on_selector_all(
                        ".search-result",
                        """(els, marketLabel) => els.map(el => {
                          const titleWrap = el.querySelector('.listing-title, .title-wrap');
                          const titleA = titleWrap ? titleWrap.querySelector('a') : el.querySelector('a.source-link');
                          const img = el.querySelector('img');
                          return {
                            title: titleA ? titleA.textContent.trim() : '',
                            url: titleA ? titleA.href : '',
                            sitecode: el.getAttribute('data-backend-sitecode') || '',
                            listing_id: el.getAttribute('data-listing-id') || '',
                            image: img ? img.src : '',
                            text: el.innerText || '',
                            market_label: marketLabel
                          };
                        })""",
                        mlabel,
                    )
                    added = 0
                    label = "trailers" if listing_mode == "trailer" else "trucks"
                    for card in cards:
                        parsed = _parse_card(card, listing_mode=listing_mode, search=search)
                        if not parsed:
                            continue
                        raw, sitecode = parsed
                        if raw.url in seen:
                            continue
                        seen.add(raw.url)
                        site_id, _ = SITE_CODES.get(sitecode, ("web", "Web"))
                        raw._skout_source = f"web:{site_id}"  # type: ignore[attr-defined]
                        out.append(raw)
                        added += 1
                    print(
                        f"  autotempest {mlabel} q={term!r}: {added} {label}",
                        flush=True,
                    )
            browser.close()
    except Exception as e:
        print(f"  autotempest: {e}", flush=True)
    return out


def listing_source(raw: RawListing) -> str:
    return getattr(raw, "_skout_source", None) or "web:autotempest"
