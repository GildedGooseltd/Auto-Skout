"""Government surplus, police, and bank auction listings (GovDeals, Public Surplus, PropertyRoom)."""

from __future__ import annotations

import re
from typing import List, Optional

from scrapers.craigslist import RawListing
from vehicle_fields import is_truck_listing, parse_price_usd

_PRICE_RE = re.compile(r"\$[\d,]+")

# Public Surplus state IDs (browse filter)
_PS_STATE = {"CO": "6", "FL": "9", "FLORIDA": "9", "COLORADO": "6"}


def _price_ok(price: str, title: str, max_price: int) -> bool:
    amount = parse_price_usd(price, title)
    if amount is None:
        return True
    return amount <= max_price


def _parse_rows(rows: list[dict], *, source: str, label: str, max_price: int) -> List[RawListing]:
    out: List[RawListing] = []
    for row in rows:
        title = (row.get("title") or "").strip()
        url = (row.get("url") or "").strip()
        if not title or not url:
            continue
        price = (row.get("price") or "").strip() or "ask"
        if not _price_ok(price, title, max_price):
            continue
        desc = (row.get("text") or "")[:400]
        if not is_truck_listing(title, desc):
            continue
        loc = row.get("location") or label
        raw = RawListing(
            title=title,
            url=url,
            price=price,
            location=f"{label} · {loc}".strip(" ·"),
            posting_id=url,
            image_url=row.get("image") or "",
            description=desc,
        )
        raw._skout_source = source  # type: ignore[attr-defined]
        out.append(raw)
    return out


def _playwright_scrape(
    pages: list[tuple[str, str, str]],
    *,
    max_price: int,
) -> List[RawListing]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  auctions: skipped — pip install playwright && playwright install chromium", flush=True)
        return []

    out: List[RawListing] = []
    seen: set[str] = set()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            for url, source, label in pages:
                try:
                    page.goto(url, wait_until="networkidle", timeout=90000)
                    page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"  auctions {label}: load failed — {e}", flush=True)
                    continue

                rows = page.evaluate(
                    """() => {
                      const out = [];
                      const seen = new Set();
                      const push = (title, url, text, price, loc, image) => {
                        if (!title || !url || seen.has(url)) return;
                        seen.add(url);
                        out.push({ title, url, text: text || '', price: price || '', location: loc || '', image: image || '' });
                      };
                      document.querySelectorAll('a[href*="/asset/"], a[href*="/en/asset/"]').forEach(a => {
                        const card = a.closest('[class*="card"], [class*="result"], li, article, tr') || a.parentElement;
                        const text = card ? card.innerText : a.innerText;
                        push(a.textContent.trim(), a.href, text, '', '', '');
                      });
                      document.querySelectorAll('a[href*="/sms/"][href*="auction/view"]').forEach(a => {
                        const card = a.closest('tr, .card, article, li') || a.parentElement;
                        const text = card ? card.innerText : a.innerText;
                        push(a.textContent.trim(), a.href, text, '', '', '');
                      });
                      document.querySelectorAll('a[href*="/l/"][href*="/s/listings"]').forEach(a => {
                        const card = a.closest('[class*="listing"], li, article') || a.parentElement;
                        const text = card ? card.innerText : a.innerText;
                        push(a.textContent.trim(), a.href, text, '', '', '');
                      });
                      document.querySelectorAll('a[href*="propertyroom.com"]').forEach(a => {
                        if (!/listing|auction|item/i.test(a.href)) return;
                        const card = a.closest('[class*="listing"], li, article') || a.parentElement;
                        const text = card ? card.innerText : a.innerText;
                        push(a.textContent.trim(), a.href, text, '', '', '');
                      });
                      return out;
                    }"""
                )
                batch = _parse_rows(rows, source=source, label=label, max_price=max_price)
                added = 0
                for raw in batch:
                    if raw.url in seen:
                        continue
                    seen.add(raw.url)
                    for m in _PRICE_RE.finditer(raw.description or raw.title):
                        raw.price = m.group(0)
                        break
                    out.append(raw)
                    added += 1
                print(f"  auctions {label}: {added} trucks", flush=True)
            browser.close()
    except Exception as e:
        print(f"  auctions: {e}", flush=True)
    return out


def fetch_auction_listings(
    *,
    keywords: list[str] | None = None,
    markets: Optional[list[dict]] = None,
    max_price_usd: int = 20000,
    sites: Optional[list[str]] = None,
    quick: bool = False,
) -> List[RawListing]:
    del quick
    terms = [t.strip() for t in (keywords or ["truck", "box truck", "work truck"]) if t.strip()]
    if not terms:
        return []

    market_list = markets or [{"state": "CO", "label": "Colorado"}, {"state": "FL", "label": "Florida"}]
    enabled = {s.lower() for s in (sites or ["govdeals", "publicsurplus", "propertyroom"])}

    pages: list[tuple[str, str, str]] = []
    for market in market_list:
        state = str(market.get("state", "CO")).upper()
        label = market.get("label") or state
        for term in terms:
            q = term.replace(" ", "+")
            if "govdeals" in enabled:
                pages.append((
                    f"https://www.govdeals.com/en/search?kWord={q}&state={state}",
                    "auction:govdeals",
                    f"GovDeals {label}",
                ))
            if "publicsurplus" in enabled:
                ps_state = _PS_STATE.get(state, state)
                pages.append((
                    f"https://www.publicsurplus.com/sms/browse/search?keywords={q}&stateId={ps_state}",
                    "auction:publicsurplus",
                    f"Public Surplus {label}",
                ))
            if "propertyroom" in enabled:
                loc = "Colorado" if state == "CO" else "Florida"
                pages.append((
                    f"https://www.propertyroom.com/s/{q.replace('+', '-')}/location-{loc}",
                    "auction:propertyroom",
                    f"PropertyRoom {label}",
                ))

    return _playwright_scrape(pages, max_price=max_price_usd)


def listing_source(raw: RawListing) -> str:
    return getattr(raw, "_skout_source", None) or "auction:govdeals"
