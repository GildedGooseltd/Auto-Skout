"""
Facebook Marketplace + group monitoring.

One-time setup:
  .venv/bin/pip install playwright
  .venv/bin/playwright install chromium
  .venv/bin/python src/scrapers/facebook.py --login
"""

import json
import re
import sys
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from scrapers.craigslist import RawListing

STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "facebook_state.json"
_GROUP_ID_RE = re.compile(r"/groups/(\d+)")


def _session_valid(context) -> bool:
    cookies = {c["name"] for c in context.cookies()}
    return "c_user" in cookies and "xs" in cookies


def _group_id(url: str) -> str:
    m = _GROUP_ID_RE.search(url or "")
    return m.group(1) if m else ""


def _search_terms(cfg: dict) -> list[str]:
    groups_cfg = cfg.get("facebook_groups", {})
    mp = cfg.get("platforms", {}).get("facebook_marketplace", {})
    terms = groups_cfg.get("marketplace", {}).get("search_terms") or mp.get("search_terms")
    if terms:
        return terms
    return ["free", "garden", "farm", "pallet", "dirt", "lumber", "plants"]


def _marketplace_zip(cfg: dict) -> str:
    groups_cfg = cfg.get("facebook_groups", {})
    mp = cfg.get("platforms", {}).get("facebook_marketplace", {})
    return (
        groups_cfg.get("marketplace", {}).get("location")
        or mp.get("location_zip")
        or cfg.get("profile", {}).get("home", {}).get("zip", "81040")
    )


def _marketplace_markets(cfg: dict) -> list[dict]:
    mp = cfg.get("platforms", {}).get("facebook_marketplace", {})
    markets = mp.get("markets")
    if markets:
        return markets
    return [{"zip": _marketplace_zip(cfg), "label": ""}]


def _unescape_json_str(s: str) -> str:
    try:
        return json.loads(f'"{s}"').strip()
    except json.JSONDecodeError:
        return s.replace('\\"', '"').strip()


def _parse_marketplace_listings_from_html(html: str, seen: set[str]) -> List[RawListing]:
    """Fallback when Playwright evaluate finds nothing — parse embedded JSON."""
    listings: List[RawListing] = []
    patterns = [
        re.compile(
            r'"listing":\{"id":"(\d+)"[^}]*\}.*?"marketplace_listing_title":"((?:[^"\\]|\\.)*)"',
            re.DOTALL,
        ),
        re.compile(
            r'"marketplace_listing_title":"((?:[^"\\]|\\.)*)".*?"listing":\{"id":"(\d+)"',
            re.DOTALL,
        ),
    ]
    for pattern in patterns:
        for m in pattern.finditer(html):
            if pattern.pattern.startswith('"listing"'):
                lid, title_raw = m.group(1), m.group(2)
            else:
                title_raw, lid = m.group(1), m.group(2)
            if lid in seen:
                continue
            title = _unescape_json_str(title_raw)
            if len(title) < 3:
                continue
            seen.add(lid)
            price_m = re.search(
                rf'"id":"{lid}"[\s\S]{{0,800}}?"formatted_price":"([^"]*)"',
                html,
            )
            price = price_m.group(1) if price_m else "ask"
            loc_m = re.search(
                rf'"id":"{lid}"[\s\S]{{0,1200}}?"city":"([^"]*)"',
                html,
            )
            loc = loc_m.group(1) if loc_m else ""
            cat_m = re.search(
                rf'"id":"{lid}"[\s\S]{{0,1200}}?"category_name":"([^"]*)"',
                html,
            )
            category = _unescape_json_str(cat_m.group(1)) if cat_m else ""
            desc_parts = [p for p in (category, loc) if p]
            listings.append(
                RawListing(
                    title=title,
                    url=f"https://www.facebook.com/marketplace/item/{lid}/",
                    price=price or "ask",
                    location=f"Facebook · {loc}".strip(" ·") if loc else "Facebook",
                    posting_id=f"fb:{lid}",
                    description=" · ".join(desc_parts),
                )
            )
    return listings


def _parse_titles_from_html(html: str, seen: set[str]) -> List[RawListing]:
    listings: List[RawListing] = []
    patterns = [
        r'"marketplace_listing_title":"((?:[^"\\]|\\.)*)"',
        r'"message":{"text":"((?:[^"\\]|\\.)*)"',
        r'"story_message":{"text":"((?:[^"\\]|\\.)*)"',
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, html):
            title = _unescape_json_str(m.group(1))
            if len(title) < 4:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            listings.append(
                RawListing(
                    title=title,
                    url="",
                    price="ask",
                    location="facebook",
                    posting_id=f"fb:{key}",
                )
            )
    return listings


def _marketplace_cards_from_page(page) -> list[dict]:
    try:
        return page.evaluate(
            """() => {
              const out = [];
              const seen = new Set();
              const push = (id, url, text) => {
                if (!id || seen.has(id)) return;
                seen.add(id);
                out.push({
                  id,
                  url: (url || '').split('?')[0],
                  text: (text || '').trim(),
                });
              };
              document.querySelectorAll('a[href*="/marketplace/item/"]').forEach(a => {
                const m = a.href.match(/\\/marketplace\\/item\\/(\\d+)/);
                if (!m) return;
                const label = a.getAttribute('aria-label') || a.innerText || '';
                push(m[1], a.href, label);
              });
              return out;
            }"""
        )
    except Exception:
        return []


def _card_to_listing(card: dict, *, mlabel: str, seen: set[str]) -> Optional[RawListing]:
    lid = str(card.get("id") or "").strip()
    if not lid or lid in seen:
        return None
    text = (card.get("text") or "").strip()
    url = (card.get("url") or f"https://www.facebook.com/marketplace/item/{lid}/").strip()
    if not url.startswith("http"):
        url = f"https://www.facebook.com/marketplace/item/{lid}/"

    title = text
    category = ""
    location = ""
    price = "ask"

    # aria-label: "Title, $price, City, ST, listing 123"
    if text:
        parts = [p.strip() for p in text.split(",")]
        if parts:
            title = parts[0]
        for p in parts[1:]:
            if p.startswith("$") or p.lower().startswith("free"):
                price = p
            elif "listing" in p.lower():
                continue
            elif re.search(r"\b[A-Z]{2}\b", p) or re.search(r",\s*[A-Z]{2}\b", p):
                location = p
            elif re.search(r"truck", p, re.I):
                category = p

    # page title style: "Title - Commercial Trucks - Clifton, Colorado"
    if " - " in text and not category:
        chunks = [c.strip() for c in text.split(" - ")]
        if len(chunks) >= 2:
            title = chunks[0]
            for chunk in chunks[1:]:
                if re.search(r"truck", chunk, re.I):
                    category = chunk
                elif re.search(r",\s*[A-Z]{2}\b|colorado|florida", chunk, re.I):
                    location = chunk

    if not title:
        return None
    seen.add(lid)
    desc_parts = [p for p in (category, location) if p]
    loc_label = f"Facebook · {mlabel}"
    if location:
        loc_label = f"{loc_label} · {location}"
    return RawListing(
        title=title,
        url=url,
        price=price,
        location=loc_label,
        posting_id=f"fb:{lid}",
        description=" · ".join(desc_parts),
    )


def _scrape_groups(page, groups: list, seen: set[str], *, quick: bool, always_full: bool = False) -> Iterator[Tuple[RawListing, str]]:
    batch_groups = groups if (always_full or not quick) else groups[:2]
    for group in batch_groups:
        gid = group.get("id") or _group_id(group.get("url", ""))
        if not gid:
            continue
        name = group.get("name", gid)
        url = f"https://www.facebook.com/groups/{gid}"
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        batch = _parse_titles_from_html(page.content(), seen)
        for raw in batch:
            raw.url = url
            raw.location = group.get("region", name)
            yield raw, "facebook_group"
        if batch:
            print(f"  facebook group {name}: {len(batch)}", flush=True)


def _marketplace_urls(zip_code: str, term: str, *, vehicles: bool) -> list[str]:
    q = term.replace(" ", "%20")
    urls = [
        f"https://www.facebook.com/marketplace/{zip_code}/search?query={q}",
    ]
    if vehicles:
        urls.append(f"https://www.facebook.com/marketplace/{zip_code}/vehicles?query={q}")
        urls.append(
            f"https://www.facebook.com/marketplace/{zip_code}/search"
            f"?query={q}&topLevelVehicleType=truck"
        )
    return urls


def fetch_facebook_all(
    cfg: dict,
    *,
    quick: bool = False,
    focus: str = "",
    always_full: bool = False,
) -> Iterator[Tuple[RawListing, str]]:
    """Yield (listing, source_key) from Marketplace and configured groups."""
    if not STATE_FILE.exists():
        print("  facebook: skipped — run: .venv/bin/python src/scrapers/facebook.py --login", flush=True)
        return

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  facebook: skipped — pip install playwright", flush=True)
        return

    from scrapers.facebook_groups_discovery import build_group_list, cache_stale, load_cache

    platforms = cfg.get("platforms", {})
    mp_enabled = platforms.get("facebook_marketplace", {}).get("enabled")
    gr_enabled = platforms.get("facebook_groups", {}).get("enabled")
    vertical = (cfg.get("profile") or {}).get("vertical", "")
    vehicles = vertical == "vehicles"
    terms = _search_terms(cfg)
    if focus == "trailer":
        terms = [t for t in terms if "trailer" in t.lower()] or ["trailer"]
    if quick and not always_full:
        terms = terms[:4]
    markets = _marketplace_markets(cfg)
    seen: set[str] = set()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=str(STATE_FILE))
            page = context.new_page()

            if not _session_valid(context):
                print(
                    "  facebook: session expired — run: .venv/bin/python src/scrapers/facebook.py --login",
                    flush=True,
                )
                browser.close()
                return

            if mp_enabled:
                for market in markets:
                    zip_code = str(market.get("zip", _marketplace_zip(cfg)))
                    mlabel = market.get("label") or zip_code
                    for term in terms:
                        for url in _marketplace_urls(zip_code, term, vehicles=vehicles):
                            try:
                                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                                page.wait_for_timeout(2500)
                            except Exception as e:
                                print(f"  facebook {mlabel} q={term!r}: {e}", flush=True)
                                continue

                            added = 0
                            cards = _marketplace_cards_from_page(page)
                            if cards:
                                for card in cards:
                                    raw = _card_to_listing(card, mlabel=mlabel, seen=seen)
                                    if not raw:
                                        continue
                                    yield raw, "facebook"
                                    added += 1
                            else:
                                for raw in _parse_marketplace_listings_from_html(page.content(), seen):
                                    raw.location = f"{mlabel} · {raw.location}".strip(" ·")
                                    yield raw, "facebook"
                                    added += 1
                            if added:
                                print(f"  facebook {mlabel} q={term!r}: {added}", flush=True)

            if gr_enabled:
                groups = load_cache().get("groups", [])
                if not groups or cache_stale(cfg):
                    groups = build_group_list(cfg, page)
                    print(f"  facebook: discovered {len(groups)} groups", flush=True)
                if not groups:
                    print("  facebook groups: none resolved — re-run --discover-groups after login", flush=True)
                else:
                    yield from _scrape_groups(page, groups, seen, quick=quick, always_full=always_full)

            browser.close()
    except Exception as e:
        print(f"  facebook: {e}", flush=True)


def fetch_marketplace() -> List[RawListing]:
    """Backward-compatible wrapper."""
    return []


def _run_login() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium")
        raise SystemExit(1)

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    print("Browser opening — log into Facebook.")
    print("Skout saves once it sees your account cookies (c_user).")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")

        for _ in range(300):
            if _session_valid(context):
                break
            page.wait_for_timeout(1000)
        else:
            print("Timed out — finish logging in, then run --login again.")
            browser.close()
            raise SystemExit(1)

        context.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"Saved session to {STATE_FILE}")
    print("Next: .venv/bin/python src/scrapers/facebook.py --discover-groups")


def _run_discover_groups() -> None:
    if not STATE_FILE.exists():
        print("No session — run: .venv/bin/python src/scrapers/facebook.py --login")
        raise SystemExit(1)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium")
        raise SystemExit(1)

    from config_loader import load_all
    from scrapers.facebook_groups_discovery import build_group_list

    cfg = load_all()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(STATE_FILE))
        page = context.new_page()
        if not _session_valid(context):
            print("Session expired — run --login first.")
            browser.close()
            raise SystemExit(1)
        groups = build_group_list(cfg, page, force=True)
        browser.close()

    print(f"Discovered {len(groups)} scrapeable groups:")
    for g in groups:
        print(f"  {g['name']} -> {g['url']} ({g.get('source', '')})")
    print("Cache: data/facebook_groups_cache.json")


if __name__ == "__main__":
    if "--login" in sys.argv:
        _run_login()
    elif "--discover-groups" in sys.argv:
        _run_discover_groups()
    else:
        print("Usage:")
        print("  python src/scrapers/facebook.py --login")
        print("  python src/scrapers/facebook.py --discover-groups")
        raise SystemExit(1)
