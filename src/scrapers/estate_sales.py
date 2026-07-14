"""Estate / yard / moving sale listings from aggregator sites (EstateSales.net v1)."""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlparse

from scrapers.craigslist import RawListing

# City browse pages on EstateSales.net (state/city slug paths).
DEFAULT_CITIES = [
    ("Pueblo", "https://www.estatesales.net/CO/Pueblo"),
    ("Colorado Springs", "https://www.estatesales.net/CO/Colorado-Springs"),
    ("Denver", "https://www.estatesales.net/CO/Denver"),
    ("Canon City", "https://www.estatesales.net/CO/Canon-City"),
    ("Littleton", "https://www.estatesales.net/CO/Littleton"),
]


def _keywords(cfg: dict, search: Optional[dict]) -> list[str]:
    explicit = list(cfg.get("match_keywords") or [])
    if explicit:
        return explicit
    if not search:
        return []
    out: list[str] = []
    for key in ("current_focus", "farm_garden", "resale_high_value"):
        out.extend(search.get(key) or [])
    return list(dict.fromkeys(kw.strip() for kw in out if kw and kw.strip()))


def _text_match(blob: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    text = blob.lower()
    return any(kw.lower() in text for kw in keywords)


def _location_from_url(url: str, fallback: str = "") -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    # /CO/Castle-Rock/80109/4972290
    if len(parts) >= 3 and parts[0] == "CO":
        city = parts[1].replace("-", " ")
        zip_code = parts[2] if parts[2].isdigit() else ""
        if zip_code:
            return f"{city}, CO {zip_code}"
        return f"{city}, CO"
    return fallback


def _sale_id(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if parts and parts[-1].isdigit():
        return parts[-1]
    return url


def _city_url_pairs(raw, default: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if not raw:
        return list(default)
    if isinstance(raw[0], dict):
        return [(c.get("name") or "Sale", c["url"]) for c in raw if c.get("url")]
    return list(raw)


def _city_slug_pairs(raw, default: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if not raw:
        return list(default)
    if isinstance(raw[0], dict):
        return [(c.get("name") or "Sale", c["slug"]) for c in raw if c.get("slug")]
    return list(raw)


def _playwright_scrape(
    cities: list[tuple[str, str]],
    *,
    keywords: list[str],
    max_sales_per_city: int,
    max_detail_fetches: int,
    require_keyword_match: bool,
    site: str = "estatesales_net",
) -> List[RawListing]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  estate_sales: skipped — pip install playwright && playwright install chromium", flush=True)
        return []

    list_js = """() => {
      const out = [];
      const seen = new Set();
      document.querySelectorAll('a[href]').forEach(el => {
        const href = el.href || '';
        if (!href.includes('estatesales.net')) return;
        if (!/\\/\\d{6,}$/.test(href)) return;
        if (seen.has(href)) return;
        seen.add(href);
        const card = el.closest('article, li, [class*="sale"]') || el;
        const title = (card.querySelector('h2,h3,h4') || el).innerText?.trim();
        const img = card.querySelector('img[src*="picturescdn"]')?.src || '';
        if (title) out.push({ title, href, img });
      });
      return out;
    }"""

    detail_js = """() => {
      const title = document.querySelector('h1')?.innerText?.trim() || '';
      const imgs = [...document.querySelectorAll('img[src*="picturescdn.estatesales.net"]')];
      const image_urls = [...new Set(imgs.map(i => i.src).filter(Boolean))];
      const alt_text = imgs.map(i => (i.alt || '').trim()).filter(t => t.length > 2);
      let body = document.body.innerText.replace(/\\s+/g, ' ').trim();
      const start = body.indexOf(title);
      if (start >= 0) body = body.slice(start, start + 4000);
      const dateMatch = body.match(/Dates\\s+((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[^+]{10,180})/i);
      const dates = dateMatch ? dateMatch[1].trim() : '';
      const addrMatch = body.match(/Address\\s+([^]+?)\\s+(?:Directions|Dates|calendar)/i);
      const address = addrMatch ? addrMatch[1].replace(/\\s+/g, ' ').trim() : '';
      return { title, image_urls, alt_text, body, dates, address };
    }"""

    out: List[RawListing] = []
    seen_urls: set[str] = set()
    detail_budget = max(0, int(max_detail_fetches))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            for city_name, city_url in cities:
                try:
                    page.goto(city_url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(4000)
                except Exception as e:
                    print(f"  estate_sales {city_name}: load failed — {e}", flush=True)
                    continue

                try:
                    cards = page.evaluate(list_js) or []
                except Exception as e:
                    print(f"  estate_sales {city_name}: parse failed — {e}", flush=True)
                    continue

                print(f"  estatesales.net {city_name}: {len(cards)} sales on map/list", flush=True)
                kept = 0
                for card in cards[: max(1, int(max_sales_per_city))]:
                    url = (card.get("href") or "").strip()
                    title = (card.get("title") or "").strip()
                    if not url or not title or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    thumb = (card.get("img") or "").strip()
                    description = ""
                    image_urls: list[str] = [thumb] if thumb else []
                    dates = ""
                    location = _location_from_url(url, city_name)
                    price = "estate sale"

                    title_match = _text_match(title, keywords)

                    if detail_budget > 0:
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=60000)
                            page.wait_for_timeout(3500)
                            detail = page.evaluate(detail_js) or {}
                            detail_budget -= 1
                        except Exception:
                            detail = {}
                        if detail.get("title"):
                            title = detail["title"]
                        description = (detail.get("body") or "")[:3500]
                        dates = (detail.get("dates") or "").strip()
                        if detail.get("address"):
                            location = detail["address"]
                        imgs = list(dict.fromkeys(detail.get("image_urls") or []))
                        if imgs:
                            image_urls = imgs
                        alt_blob = " | ".join(detail.get("alt_text") or [])
                        if alt_blob:
                            description = f"{description}\n\nPhotos: {alt_blob}"[:4000]

                    match_blob = f"{title} {description}"
                    if require_keyword_match and not _text_match(match_blob, keywords):
                        if not title_match:
                            continue

                    if dates:
                        price = dates[:80]

                    raw = RawListing(
                        title=title,
                        url=url,
                        price=price,
                        location=location,
                        posting_id=_sale_id(url),
                        image_url=image_urls[0] if image_urls else "",
                        image_urls=image_urls[:12],
                        description=description,
                    )
                    raw._skout_source = f"estate_sales:{site}"  # type: ignore[attr-defined]
                    out.append(raw)
                    kept += 1

                if kept:
                    print(f"  estatesales.net {city_name}: {kept} matched", flush=True)

            browser.close()
    except Exception as e:
        print(f"  estate_sales: browser error — {e}", flush=True)

    return out


def _playwright_scrape_org(
    cities: list[tuple[str, str]],
    *,
    keywords: list[str],
    max_sales_per_city: int,
    max_detail_fetches: int,
    require_keyword_match: bool,
) -> List[RawListing]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  estate_sales.org: skipped — pip install playwright", flush=True)
        return []

    list_js = """() => {
      const out = [];
      const seen = new Set();
      document.querySelectorAll('a[href]').forEach(a => {
        let h = (a.href || '').split('?')[0].replace(/\\/gallery$/, '');
        if (!/estate-sales\\/co\\/.+\\/[^/]+-\\d{5,}$/.test(h)) return;
        if (seen.has(h)) return;
        seen.add(h);
        const card = a.closest('article,li,[class*="sale"],div') || a;
        let title = '';
        for (const el of card.querySelectorAll('h2,h3,h4,strong,a,span')) {
          const t = (el.textContent || '').trim();
          if (t.length > 12 && !/follow|photos?|register/i.test(t)) { title = t; break; }
        }
        if (!title) title = h.split('/').slice(-1)[0].replace(/-\\d+$/, '').replace(/-/g, ' ');
        out.push({ title, href: h });
      });
      return out;
    }"""

    zip_js = """() => {
      const out = [];
      const seen = new Set();
      document.querySelectorAll('a[href*="estate-sales/co"]').forEach(a => {
        const h = (a.href || '').split('?')[0];
        if (!/estate-sales\\/co\\/[^/]+\\/\\d{5}$/.test(h)) return;
        if (seen.has(h)) return;
        seen.add(h);
        out.push(h);
      });
      return out;
    }"""

    detail_js = """() => {
      const title = document.querySelector('h1')?.innerText?.trim() || '';
      const imgs = [...document.querySelectorAll('img')];
      const image_urls = [...new Set(imgs.map(i => i.src).filter(s => s && s.startsWith('http')))].slice(0, 12);
      const alt_text = imgs.map(i => (i.alt || '').trim()).filter(t => t.length > 2);
      let body = document.body.innerText.replace(/\\s+/g, ' ').trim().slice(0, 4000);
      return { title, image_urls, alt_text, body };
    }"""

    out: List[RawListing] = []
    seen_urls: set[str] = set()
    detail_budget = max(0, int(max_detail_fetches))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            for city_name, city_url in cities:
                try:
                    page.goto(city_url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(4000)
                except Exception as e:
                    print(f"  estatesales.org {city_name}: load failed — {e}", flush=True)
                    continue
                try:
                    cards = page.evaluate(list_js) or []
                    if not cards:
                        zip_pages = page.evaluate(zip_js) or []
                        for zp in zip_pages[:8]:
                            try:
                                page.goto(zp, wait_until="domcontentloaded", timeout=60000)
                                page.wait_for_timeout(2500)
                                cards.extend(page.evaluate(list_js) or [])
                            except Exception:
                                continue
                except Exception as e:
                    print(f"  estatesales.org {city_name}: parse failed — {e}", flush=True)
                    continue
                print(f"  estatesales.org {city_name}: {len(cards)} sales", flush=True)
                kept = 0
                for card in cards[: max(1, int(max_sales_per_city))]:
                    url = (card.get("href") or "").strip()
                    title = (card.get("title") or "").strip()
                    if not url or not title or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    description = ""
                    image_urls: list[str] = []
                    dates = ""
                    location = city_name
                    price = "estate sale"
                    title_match = _text_match(title, keywords)
                    if detail_budget > 0:
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=60000)
                            page.wait_for_timeout(3000)
                            detail = page.evaluate(detail_js) or {}
                            detail_budget -= 1
                        except Exception:
                            detail = {}
                        if detail.get("title"):
                            title = detail["title"]
                        description = (detail.get("body") or "")[:3500]
                        imgs = list(dict.fromkeys(detail.get("image_urls") or []))
                        if imgs:
                            image_urls = imgs
                        alt_blob = " | ".join(detail.get("alt_text") or [])
                        if alt_blob:
                            description = f"{description}\n\nPhotos: {alt_blob}"[:4000]
                    match_blob = f"{title} {description}"
                    if require_keyword_match and not _text_match(match_blob, keywords) and not title_match:
                        continue
                    raw = RawListing(
                        title=title,
                        url=url,
                        price=price,
                        location=location,
                        posting_id=_sale_id(url),
                        image_url=image_urls[0] if image_urls else "",
                        image_urls=image_urls[:12],
                        description=description,
                    )
                    raw._skout_source = "estate_sales:estatesales_org"  # type: ignore[attr-defined]
                    out.append(raw)
                    kept += 1
                if kept:
                    print(f"  estatesales.org {city_name}: {kept} matched", flush=True)
            browser.close()
    except Exception as e:
        print(f"  estatesales.org: browser error — {e}", flush=True)
    return out


def _playwright_scrape_gsalr(
    cities: list[tuple[str, str]],
    *,
    keywords: list[str],
    max_sales_per_city: int,
    max_detail_fetches: int,
    require_keyword_match: bool,
) -> List[RawListing]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  gsalr: skipped — pip install playwright", flush=True)
        return []

    list_js = """() => {
      const out = [];
      const seen = new Set();
      document.querySelectorAll('a[href]').forEach(a => {
        const h = (a.href || '').split('?')[0];
        if (!/gsalr\\.com\\/.+-\\d+\\.html$/.test(h)) return;
        if (seen.has(h)) return;
        seen.add(h);
        const title = (a.textContent || '').trim();
        if (title.length < 6) return;
        out.push({ title, href: h });
      });
      return out;
    }"""

    detail_js = """() => {
      const title = document.querySelector('h1')?.innerText?.trim() || '';
      const imgs = [...document.querySelectorAll('img[src*="gsalr"], img[src*="cloudfront"]')];
      const image_urls = [...new Set(imgs.map(i => i.src).filter(Boolean))].slice(0, 12);
      const body = document.body.innerText.replace(/\\s+/g, ' ').trim().slice(0, 3500);
      return { title, image_urls, body };
    }"""

    out: List[RawListing] = []
    seen_urls: set[str] = set()
    detail_budget = max(0, int(max_detail_fetches))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            for city_name, slug in cities:
                city_url = f"https://gsalr.com/garage-sales-{slug}.html"
                try:
                    page.goto(city_url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(3500)
                except Exception as e:
                    print(f"  gsalr {city_name}: load failed — {e}", flush=True)
                    continue
                try:
                    cards = page.evaluate(list_js) or []
                except Exception as e:
                    print(f"  gsalr {city_name}: parse failed — {e}", flush=True)
                    continue
                print(f"  gsalr {city_name}: {len(cards)} sales", flush=True)
                kept = 0
                for card in cards[: max(1, int(max_sales_per_city))]:
                    url = (card.get("href") or "").strip()
                    title = (card.get("title") or "").strip()
                    if not url or not title or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    description = ""
                    image_urls: list[str] = []
                    location = city_name
                    price = "yard sale"
                    title_match = _text_match(title, keywords)
                    if detail_budget > 0:
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=60000)
                            page.wait_for_timeout(2500)
                            detail = page.evaluate(detail_js) or {}
                            detail_budget -= 1
                        except Exception:
                            detail = {}
                        if detail.get("title"):
                            title = detail["title"]
                        description = (detail.get("body") or "")[:3000]
                        imgs = list(dict.fromkeys(detail.get("image_urls") or []))
                        if imgs:
                            image_urls = imgs
                    match_blob = f"{title} {description}"
                    if require_keyword_match and not _text_match(match_blob, keywords) and not title_match:
                        continue
                    raw = RawListing(
                        title=title,
                        url=url,
                        price=price,
                        location=location,
                        posting_id=_sale_id(url),
                        image_url=image_urls[0] if image_urls else "",
                        image_urls=image_urls[:12],
                        description=description,
                    )
                    raw._skout_source = "estate_sales:gsalr"  # type: ignore[attr-defined]
                    out.append(raw)
                    kept += 1
                if kept:
                    print(f"  gsalr {city_name}: {kept} matched", flush=True)
            browser.close()
    except Exception as e:
        print(f"  gsalr: browser error — {e}", flush=True)
    return out


def fetch_estate_sales(
    cfg: dict,
    *,
    search: Optional[dict] = None,
    quick: bool = False,
) -> List[RawListing]:
    """Fetch estate sales from configured aggregator sites."""
    if not cfg.get("enabled"):
        return []

    sites = {s.lower() for s in (cfg.get("sites") or ["estatesales_net"])}
    keywords = _keywords(cfg, search)
    max_per_city = int(cfg.get("max_sales_per_city") or (12 if quick else 25))
    max_details = int(cfg.get("max_detail_fetches") or (15 if quick else 50))
    require_match = bool(cfg.get("require_keyword_match", True))

    out: List[RawListing] = []
    if "estatesales_net" in sites:
        cities_cfg = cfg.get("cities") or []
        if cities_cfg:
            cities = [(c.get("name") or "Sale", c["url"]) for c in cities_cfg if c.get("url")]
        else:
            cities = list(DEFAULT_CITIES)
        out.extend(_playwright_scrape(
            cities,
            keywords=keywords,
            max_sales_per_city=max_per_city,
            max_detail_fetches=max_details,
            require_keyword_match=require_match,
            site="estatesales_net",
        ))

    if "estatesales_org" in sites:
        org_default = [
            ("Pueblo", "https://www.estatesales.org/estate-sales/co/pueblo"),
            ("Colorado Springs", "https://www.estatesales.org/estate-sales/co/colorado-springs"),
            ("Denver", "https://www.estatesales.org/estate-sales/co/denver"),
            ("Canon City", "https://www.estatesales.org/estate-sales/co/canon-city"),
            ("Littleton", "https://www.estatesales.org/estate-sales/co/littleton"),
        ]
        org_cities = _city_url_pairs(cfg.get("org_cities"), org_default)
        out.extend(_playwright_scrape_org(
            org_cities,
            keywords=keywords,
            max_sales_per_city=max_per_city,
            max_detail_fetches=max_details,
            require_keyword_match=require_match,
        ))

    if "gsalr" in sites:
        gsalr_default = [
            ("Pueblo", "pueblo-co"),
            ("Colorado Springs", "colorado-springs-co"),
            ("Denver", "denver-co"),
            ("Canon City", "canon-city-co"),
            ("Littleton", "littleton-co"),
        ]
        gsalr_cities = _city_slug_pairs(cfg.get("gsalr_cities"), gsalr_default)
        out.extend(_playwright_scrape_gsalr(
            gsalr_cities,
            keywords=keywords,
            max_sales_per_city=max_per_city,
            max_detail_fetches=max_details,
            require_keyword_match=require_match,
        ))

    if not out and sites - {"estatesales_net", "estatesales_org", "gsalr"}:
        print("  estate_sales: no supported sites enabled", flush=True)
    return out


def listing_source(raw: RawListing) -> str:
    return getattr(raw, "_skout_source", None) or "estate_sales:estatesales_net"
