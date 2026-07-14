"""Textile / sewing equipment auctions — HGP, GovPlanet, GovDeals, IRS."""

from __future__ import annotations

import re
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

_PRICE_RE = re.compile(r"\$[\d,]+")


def _raw_from_row(
    row: dict,
    *,
    source: str,
    label: str,
    keywords: list[str],
    max_price: int,
) -> Optional[RawListing]:
    title = (row.get("title") or "").strip()
    url = (row.get("url") or "").strip()
    if not title or not url:
        return None
    desc = (row.get("text") or "")[:800]
    if not is_textile_listing(title, "", keywords) and not is_textile_listing(title, desc[:240], keywords):
        return None
    price = (row.get("price") or "").strip()
    if not price:
        for m in _PRICE_RE.finditer(f"{title} {desc}"):
            price = m.group(0)
            break
    price = price or "auction"
    if not price_ok(price, title, max_price):
        return None
    loc = (row.get("location") or label).strip()
    raw = RawListing(
        title=title,
        url=url,
        price=price,
        location=f"{label} · {loc}".strip(" ·"),
        posting_id=url,
        image_url=(row.get("image") or "").strip(),
        description=desc,
    )
    raw._skout_source = source  # type: ignore[attr-defined]
    return raw


def _playwright_scrape(
    pages: list[tuple[str, str, str]],
    *,
    keywords: list[str],
    max_price: int,
) -> List[RawListing]:
    if not playwright_available():
        print_playwright_skip("textile_auctions")
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print_playwright_skip("textile_auctions")
        return []

    scrape_js = """() => {
      const out = [];
      const seen = new Set();
      const push = (title, url, text, image) => {
        if (!title || !url || seen.has(url)) return;
        seen.add(url);
        out.push({
          title: title.trim().slice(0, 200),
          url,
          text: (text || '').trim().slice(0, 800),
          image: image || '',
        });
      };
      document.querySelectorAll('a[href*="/auctions/"]').forEach(a => {
        const href = a.href || '';
        if (!/\\/auctions\\/\\d+\\//.test(href)) return;
        const card = a.closest('article, li, [class*="auction"], div') || a;
        const title = (a.textContent || card.textContent || '').trim().replace(/\\s+/g, ' ');
        if (title.length < 12) return;
        push(title, href.split('?')[0], card.innerText || title, '');
      });
      document.querySelectorAll('a[href*="/asset/"], a[href*="/en/asset/"]').forEach(a => {
        const card = a.closest('[class*="card"], li, article, tr') || a.parentElement;
        push(a.textContent.trim(), a.href, card ? card.innerText : a.textContent, '');
      });
      document.querySelectorAll('a[href*="/for-sale/"]').forEach(a => {
        const href = a.href || '';
        if (!/Textiles|Equipment|Sewing/i.test(href + ' ' + (a.textContent || ''))) return;
        const card = a.closest('li, article, tr, [class*="listing"]') || a.parentElement;
        push(
          (a.getAttribute('title') || a.textContent || '').trim(),
          href,
          card ? card.innerText : a.textContent,
          card?.querySelector('img')?.src || '',
        );
      });
      document.querySelectorAll('a[href*="irsauctions.com"]').forEach(a => {
        const href = a.href || '';
        if (!/\\/lots\\//.test(href) && !/\\/auctions\\//.test(href)) return;
        const card = a.closest('tr, li, article, div') || a.parentElement;
        push(a.textContent.trim(), href, card ? card.innerText : a.textContent, '');
      });
      return out;
    }"""

    out: List[RawListing] = []
    seen: set[str] = set()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=browser_user_agent())
            for url, source, label in pages:
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=75000)
                    page.wait_for_timeout(4000)
                    if resp and resp.status >= 400:
                        print(f"  {label}: HTTP {resp.status}", flush=True)
                        continue
                except Exception as e:
                    print(f"  {label}: load failed — {e}", flush=True)
                    continue

                try:
                    rows = page.evaluate(scrape_js) or []
                except Exception as e:
                    print(f"  {label}: parse failed — {e}", flush=True)
                    continue

                added = 0
                for row in rows:
                    raw = _raw_from_row(
                        row,
                        source=source,
                        label=label,
                        keywords=keywords,
                        max_price=max_price,
                    )
                    if not raw or raw.url in seen:
                        continue
                    seen.add(raw.url)
                    out.append(raw)
                    added += 1
                print(f"  {label}: {added} textile auction hits", flush=True)
            browser.close()
    except Exception as e:
        print(f"  textile_auctions: {e}", flush=True)
    return out


def fetch_textile_auctions(
    cfg: dict,
    *,
    search: Optional[dict] = None,
    quick: bool = False,
) -> List[RawListing]:
    if not cfg.get("enabled"):
        return []

    keywords = textile_keywords(cfg, search)
    terms = [t.strip() for t in (cfg.get("keywords") or keywords[:5]) if t.strip()]
    if quick:
        terms = terms[:2]
    max_price = int(cfg.get("max_price_usd") or 25000)
    enabled = {s.lower() for s in (cfg.get("sites") or ["hgp", "govplanet", "govdeals", "irs"])}
    markets = cfg.get("markets") or [{"state": "CO", "label": "Colorado"}]

    pages: list[tuple[str, str, str]] = []
    if "hgp" in enabled:
        pages.append((
            "https://www.hgpauction.com/auction-category/textiles-apparel/",
            "auction:hgp",
            "HGP Textile Auctions",
        ))

    for term in terms:
        q = quote_plus(term)
        if "govplanet" in enabled:
            pages.append((
                f"https://www.govplanet.com/jsp/s/search.ips?kw={q}",
                "auction:govplanet",
                f"GovPlanet · {term}",
            ))
        for market in markets:
            state = str(market.get("state", "CO")).upper()
            label = market.get("label") or state
            if "govdeals" in enabled:
                pages.append((
                    f"https://www.govdeals.com/en/search?kWord={q}&state={state}",
                    "textile_auction:govdeals",
                    f"GovDeals {label} · {term}",
                ))
        if "irs" in enabled:
            pages.append((
                f"https://www.irsauctions.com/auctions/?search={q}",
                "auction:irs",
                f"IRS Auctions · {term}",
            ))

    return _playwright_scrape(pages, keywords=keywords, max_price=max_price)


def listing_source(raw: RawListing) -> str:
    return getattr(raw, "_skout_source", None) or "auction:hgp"
