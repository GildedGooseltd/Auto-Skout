"""Industrial sewing marketplaces — Machinio, eBay."""

from __future__ import annotations

from typing import List, Optional
from urllib.parse import quote_plus

from scrapers.craigslist import RawListing
from scrapers.machinery_common import (
    browser_user_agent,
    is_textile_listing,
    playwright_available,
    price_ok,
    print_playwright_skip,
    textile_keywords,
)

_LISTING_CARD_JS = """() => {
  const out = [];
  const seen = new Set();
  const push = (title, url, text, price, image) => {
    if (!title || !url || seen.has(url)) return;
    seen.add(url);
    out.push({
      title: title.trim(),
      url,
      text: (text || '').trim().slice(0, 500),
      price: (price || '').trim(),
      image: image || '',
    });
  };
  document.querySelectorAll('a[href*="/listings/"]').forEach(a => {
    const href = a.href || '';
    if (!href.includes('/listings/')) return;
    const card = a.closest('article, li, [class*="listing"], [class*="result"]') || a.parentElement;
    const titleEl = card ? (card.querySelector('h2,h3,h4,[class*="title"]') || a) : a;
    const title = (titleEl.textContent || '').trim();
    const text = card ? card.innerText : title;
    const img = card ? (card.querySelector('img')?.src || '') : '';
    const priceMatch = (text || '').match(/\\$[\\d,]+(?:\\.\\d{2})?/);
    push(title, href.split('?')[0], text, priceMatch ? priceMatch[0] : '', img);
  });
  document.querySelectorAll('.s-item, [class*="srp-results"] li').forEach(card => {
    const a = card.querySelector('a[href*="ebay.com/itm/"]');
    if (!a) return;
    const title = (card.querySelector('.s-item__title, [class*="title"]') || a).textContent || '';
    if (/shop on ebay/i.test(title)) return;
    const text = card.innerText || title;
    const img = card.querySelector('img')?.src || '';
    const priceMatch = text.match(/\\$[\\d,]+(?:\\.\\d{2})?/);
    push(title, a.href.split('?')[0], text, priceMatch ? priceMatch[0] : '', img);
  });
  return out;
}"""


def _raw_from_row(row: dict, *, source: str, label: str, keywords: list[str], max_price: int, min_price: int = 0) -> Optional[RawListing]:
    title = (row.get("title") or "").strip()
    url = (row.get("url") or "").strip()
    if not title or not url:
        return None
    desc = (row.get("text") or "")[:500]
    term_hint = (row.get("search_term") or "").lower()
    if term_hint and any(k in term_hint for k in ("sew", "serger", "juki", "consew", "ultrasonic", "embroidery", "textile", "industrial")):
        pass  # search already targeted — keep listing
    elif not is_textile_listing(title, desc, keywords):
        return None
    price = (row.get("price") or "").strip() or "ask"
    if not price_ok(price, title, max_price, min_price=min_price):
        return None
    loc = (row.get("location") or label).strip()
    raw = RawListing(
        title=title,
        url=url,
        price=price,
        location=loc,
        posting_id=url,
        image_url=(row.get("image") or "").strip(),
        description=desc,
    )
    raw._skout_source = source  # type: ignore[attr-defined]
    return raw


def _playwright_search_pages(
    pages: list[tuple[str, str, str]],
    *,
    keywords: list[str],
    max_price: int,
    min_price: int = 0,
) -> List[RawListing]:
    if not playwright_available():
        print_playwright_skip("textile_marketplaces")
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print_playwright_skip("textile_marketplaces")
        return []

    out: List[RawListing] = []
    seen: set[str] = set()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=browser_user_agent())
            for url, source, label in pages:
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=75000)
                    page.wait_for_timeout(3500)
                    if resp and resp.status >= 400:
                        print(f"  {label}: HTTP {resp.status} (may block bots — try scan from home network)", flush=True)
                        continue
                except Exception as e:
                    print(f"  {label}: load failed — {e}", flush=True)
                    continue

                try:
                    rows = page.evaluate(_LISTING_CARD_JS) or []
                except Exception as e:
                    print(f"  {label}: parse failed — {e}", flush=True)
                    continue

                added = 0
                for row in rows:
                    row["location"] = label
                    row["search_term"] = label
                    raw = _raw_from_row(
                        row,
                        source=source,
                        label=label,
                        keywords=keywords,
                        max_price=max_price,
                        min_price=min_price,
                    )
                    if not raw or raw.url in seen:
                        continue
                    seen.add(raw.url)
                    out.append(raw)
                    added += 1
                print(f"  {label}: {added} textile listings", flush=True)
            browser.close()
    except Exception as e:
        print(f"  textile_marketplaces: {e}", flush=True)
    return out


def fetch_textile_marketplaces(
    cfg: dict,
    *,
    search: Optional[dict] = None,
    quick: bool = False,
) -> List[RawListing]:
    if not cfg.get("enabled"):
        return []

    keywords = textile_keywords(cfg, search)
    terms = [t.strip() for t in (cfg.get("search_terms") or keywords[:6]) if t.strip()]
    if quick:
        terms = terms[:3]
    max_price = int(cfg.get("max_price_usd") or 25000)
    min_price = int(cfg.get("min_price_usd") or 0)
    zip_code = str(cfg.get("zip") or "81040")
    enabled = {s.lower() for s in (cfg.get("sites") or ["machinio", "ebay"])}

    pages: list[tuple[str, str, str]] = []
    for term in terms:
        q = quote_plus(term)
        if "machinio" in enabled:
            pages.append((
                f"https://www.machinio.com/cat/industrial-sewing-machines?query={q}&location=Colorado",
                "marketplace:machinio",
                f"Machinio · {term}",
            ))
        if "ebay" in enabled:
            pages.append((
                "https://www.ebay.com/sch/i.html"
                f"?_nkw={q}&_sop=10&LH_PrefLoc=1&_stpos={zip_code}&_fspt=1&rt=nc",
                "marketplace:ebay",
                f"eBay · {term}",
            ))

    return _playwright_search_pages(
        pages, keywords=keywords, max_price=max_price, min_price=min_price,
    )


def listing_source(raw: RawListing) -> str:
    return getattr(raw, "_skout_source", None) or "marketplace:machinio"
