"""Dealer used-inventory feeds — Pleasant Street, MD Equipment, Cutsew."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Optional
from urllib.parse import urlparse

import requests

from scrapers.craigslist import HEADERS, RawListing
from scrapers.machinery_common import (
    browser_user_agent,
    is_textile_listing,
    og_image_from_html,
    parse_h1_from_html,
    parse_price_from_html,
    playwright_available,
    price_ok,
    print_playwright_skip,
    textile_keywords,
)

_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.headers.update(HEADERS)

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_LISTING_SLUG_RE = re.compile(
    r"sewing|serger|overlock|embroidery|quilting|ultrasonic|sonobond|seammaster|textile|"
    r"juki-|consew|union-special|blindstitch|pegasus|walking-foot|lace-machine|mo-6|ddl-|lu-",
    re.I,
)


def _fetch_html(url: str) -> str:
    try:
        r = _SESSION.get(url, timeout=30)
        if r.status_code >= 400:
            return ""
        return r.text
    except requests.RequestException:
        return ""


def _sitemap_urls(site_base: str, *, path_filter: Optional[re.Pattern] = None) -> list[str]:
    try:
        r = _SESSION.get(f"{site_base.rstrip('/')}/sitemap.xml", timeout=30)
        if r.status_code >= 400:
            return []
        root = ET.fromstring(r.text)
    except (requests.RequestException, ET.ParseError):
        return []
    urls: list[str] = []
    for loc in root.findall(".//sm:loc", _SITEMAP_NS):
        href = (loc.text or "").strip()
        if not href:
            continue
        if path_filter and not path_filter.search(href):
            continue
        urls.append(href)
    return urls


def _listing_from_page(
    url: str,
    *,
    source: str,
    label: str,
    keywords: list[str],
    max_price: int,
    min_price: int = 0,
) -> Optional[RawListing]:
    html = _fetch_html(url)
    if not html:
        return None
    title = parse_h1_from_html(html)
    if not title:
        slug = urlparse(url).path.rsplit("/", 1)[-1]
        title = slug.replace("-", " ").strip()
    desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))[:600]
    if not is_textile_listing(title, desc, keywords):
        return None
    price = parse_price_from_html(html, fallback_title=title)
    if not price_ok(price, title, max_price, min_price=min_price):
        return None
    loc_match = re.search(r"Location[:\s]+([A-Za-z .,'-]+(?:,\s*[A-Z]{2})?)", html, re.I)
    location = loc_match.group(1).strip() if loc_match else label
    raw = RawListing(
        title=title,
        url=url,
        price=price,
        location=location,
        posting_id=url,
        image_url=og_image_from_html(html),
        description=desc[:500],
    )
    raw._skout_source = source  # type: ignore[attr-defined]
    return raw


def _pleasant_street(cfg: dict, keywords: list[str], max_price: int, quick: bool, min_price: int = 0) -> List[RawListing]:
    urls = _sitemap_urls("https://www.pleasantstmachinery.com", path_filter=re.compile(r"/listings/"))
    urls = [u for u in urls if _LISTING_SLUG_RE.search(u)]
    limit = int(cfg.get("max_listings_per_site") or (20 if quick else 60))
    urls = urls[:limit]
    out: List[RawListing] = []
    for url in urls:
        raw = _listing_from_page(
            url,
            source="dealer:pleasant_street",
            label="Pleasant Street Machinery",
            keywords=keywords,
            max_price=max_price,
            min_price=min_price,
        )
        if raw:
            out.append(raw)
    print(f"  Pleasant Street Machinery: {len(out)} listings", flush=True)
    return out


def _md_equipment(cfg: dict, keywords: list[str], max_price: int, quick: bool, min_price: int = 0) -> List[RawListing]:
    if not playwright_available():
        print_playwright_skip("dealer:md_equipment")
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print_playwright_skip("dealer:md_equipment")
        return []

    search_urls = cfg.get("md_equipment_urls") or [
        "https://mdequipmentservices.com/types/50507-ultrasonic-welding-slash-bonding",
        "https://mdequipmentservices.com/equipment?search=sewing",
        "https://mdequipmentservices.com/equipment?search=sonobond",
    ]
    if quick:
        search_urls = search_urls[:1]

    out: List[RawListing] = []
    seen: set[str] = set()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=browser_user_agent())
            for search_url in search_urls:
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2500)
                except Exception as e:
                    print(f"  MD Equipment ({search_url}): {e}", flush=True)
                    continue
                links = page.evaluate(
                    """() => [...new Set([...document.querySelectorAll('a[href*="/equipment/"]')]
                      .map(a => a.href.split('?')[0]))]"""
                ) or []
                for url in links:
                    if url in seen:
                        continue
                    seen.add(url)
                    raw = _listing_from_page(
                        url,
                        source="dealer:md_equipment",
                        label="MD Equipment Services",
                        keywords=keywords,
                        max_price=max_price,
                        min_price=min_price,
                    )
                    if raw:
                        out.append(raw)
            browser.close()
    except Exception as e:
        print(f"  MD Equipment: {e}", flush=True)
    print(f"  MD Equipment Services: {len(out)} listings", flush=True)
    return out


def _cutsew(cfg: dict, keywords: list[str], max_price: int, quick: bool, min_price: int = 0) -> List[RawListing]:
    if not playwright_available():
        print_playwright_skip("dealer:cutsew")
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print_playwright_skip("dealer:cutsew")
        return []

    out: List[RawListing] = []
    seen: set[str] = set()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=browser_user_agent())
            page.goto("https://www.cutsew.com/used-sewing-machines", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            links = page.evaluate(
                """() => [...new Set([...document.querySelectorAll('a[href]')]
                  .map(a => a.href)
                  .filter(h => /cutsew\\.com\\/.+/.test(h) && !/used-sewing-machines$/.test(h)))]"""
            ) or []
            if quick:
                links = links[:15]
            for url in links:
                if url in seen:
                    continue
                seen.add(url)
                raw = _listing_from_page(
                    url,
                    source="dealer:cutsew",
                    label="Cutsew.com",
                    keywords=keywords,
                    max_price=max_price,
                    min_price=min_price,
                )
                if raw:
                    out.append(raw)
            browser.close()
    except Exception as e:
        print(f"  Cutsew: {e}", flush=True)
    print(f"  Cutsew.com: {len(out)} listings", flush=True)
    return out


def fetch_dealer_inventory(
    cfg: dict,
    *,
    search: Optional[dict] = None,
    quick: bool = False,
) -> List[RawListing]:
    if not cfg.get("enabled"):
        return []

    keywords = textile_keywords(cfg, search)
    max_price = int(cfg.get("max_price_usd") or 25000)
    min_price = int(cfg.get("min_price_usd") or 0)
    enabled = {s.lower() for s in (cfg.get("sites") or ["pleasant_street", "md_equipment", "cutsew"])}

    out: List[RawListing] = []
    if "pleasant_street" in enabled:
        out.extend(_pleasant_street(cfg, keywords, max_price, quick, min_price=min_price))
    if "md_equipment" in enabled:
        out.extend(_md_equipment(cfg, keywords, max_price, quick, min_price=min_price))
    if "cutsew" in enabled:
        out.extend(_cutsew(cfg, keywords, max_price, quick, min_price=min_price))
    return out


def listing_source(raw: RawListing) -> str:
    return getattr(raw, "_skout_source", None) or "dealer:pleasant_street"
