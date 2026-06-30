"""Nextdoor For Sale & Free — Content API (OAuth) or logged-in Playwright scrape."""

import os
import sys
from pathlib import Path
from typing import List

import requests

from scrapers.craigslist import HEADERS, RawListing

STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "nextdoor_state.json"
FSF_URL = "https://nextdoor.com/content_api/v2/search_sale_item"
TOKEN_URL = "https://auth.nextdoor.com/v2/token"

_session = requests.Session()
_session.trust_env = False


def _get_access_token() -> str:
    cached = os.getenv("NEXTDOOR_ACCESS_TOKEN", "").strip()
    if cached:
        return cached

    client_id = os.getenv("NEXTDOOR_CLIENT_ID", "").strip()
    client_secret = os.getenv("NEXTDOOR_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return ""

    scope = (
        "openid content_api content_api.search_sale_item "
        "content_api.search_post content_api.search_business"
    )
    try:
        r = _session.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": scope},
            auth=(client_id, client_secret),
            timeout=20,
        )
        if not r.ok:
            print(f"  nextdoor: token HTTP {r.status_code}", flush=True)
            return ""
        return (r.json().get("access_token") or "").strip()
    except Exception as e:
        print(f"  nextdoor: token error: {e}", flush=True)
        return ""


def _fetch_api(cfg: dict, token: str) -> List[RawListing]:
    lat = cfg.get("latitude", 38.8339)
    lng = cfg.get("longitude", -104.8214)
    radius = cfg.get("radius", 50)
    terms = cfg.get("search_terms") or ["free", "garden", "farm"]
    out: List[RawListing] = []
    seen = set()

    for term in terms:
        try:
            r = _session.get(
                FSF_URL,
                params={"lat": lat, "lon": lng, "radius": radius, "query": term},
                headers={
                    **HEADERS,
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                timeout=25,
            )
            if not r.ok:
                print(f"  nextdoor api q={term!r}: HTTP {r.status_code}", flush=True)
                continue
            items = r.json()
            if not isinstance(items, list):
                continue
        except Exception as e:
            print(f"  nextdoor api q={term!r}: {e}", flush=True)
            continue

        added = 0
        for item in items:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url:
                continue
            price = item.get("price", 0)
            if price not in (0, None) and "free" not in title.lower():
                continue
            if url in seen:
                continue
            seen.add(url)
            photos = item.get("photos") or []
            image_url = photos[0] if photos else ""
            city = (item.get("city") or "Nextdoor").strip()
            out.append(
                RawListing(
                    title=title,
                    url=url,
                    price="free" if price in (0, None) else f"${price}",
                    location=f"Nextdoor · {city}",
                    posting_id=item.get("id", url),
                    image_url=image_url,
                    description=(item.get("description") or "").strip(),
                    reply_url=url,
                )
            )
            added += 1
        if added:
            print(f"  nextdoor api q={term!r}: {added}", flush=True)

    return out


def _session_valid(context) -> bool:
    cookies = {c["name"] for c in context.cookies()}
    return bool(cookies & {"NDAS", "ndbr_at", "ndbr_adt", "ndbr_sid"} or "c_user" in cookies)


def _fetch_playwright(cfg: dict) -> List[RawListing]:
    if not STATE_FILE.exists():
        return []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    lat = cfg.get("latitude", 38.8339)
    lng = cfg.get("longitude", -104.8214)
    terms = cfg.get("search_terms") or ["free"]
    out: List[RawListing] = []
    seen = set()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=str(STATE_FILE))
            page = context.new_page()

            if not _session_valid(context):
                print(
                    "  nextdoor: session expired — run: "
                    ".venv/bin/python src/scrapers/nextdoor.py --login",
                    flush=True,
                )
                browser.close()
                return []

            captured: list[dict] = []

            def on_response(resp):
                if resp.status != 200 or "json" not in (resp.headers.get("content-type") or ""):
                    return
                if "search_sale_item" not in resp.url and "for_sale" not in resp.url:
                    return
                try:
                    data = resp.json()
                    if isinstance(data, list):
                        captured.extend(data)
                except Exception:
                    pass

            page.on("response", on_response)

            for term in terms[:4]:
                page.goto(
                    f"https://nextdoor.com/for_sale_and_free/?query={term.replace(' ', '%20')}",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                page.wait_for_timeout(3000)

            for item in captured:
                title = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                if not title or not url or url in seen:
                    continue
                seen.add(url)
                photos = item.get("photos") or []
                out.append(
                    RawListing(
                        title=title,
                        url=url,
                        price="free",
                        location=f"Nextdoor · {(item.get('city') or 'local')}",
                        posting_id=item.get("id", url),
                        image_url=photos[0] if photos else "",
                        description=(item.get("description") or "").strip(),
                        reply_url=url,
                    )
                )

            browser.close()
    except Exception as e:
        print(f"  nextdoor playwright: {e}", flush=True)

    if out:
        print(f"  nextdoor: {len(out)} listings (logged-in scrape)", flush=True)
    return out


def fetch_offers(cfg: dict) -> List[RawListing]:
    token = _get_access_token()
    if token:
        results = _fetch_api(cfg, token)
        if results:
            print(f"  nextdoor: {len(results)} listings (API)", flush=True)
            return results
        print("  nextdoor: API returned nothing — trying logged-in scrape", flush=True)

    if not token and not STATE_FILE.exists():
        print(
            "  nextdoor: set NEXTDOOR_CLIENT_ID + NEXTDOOR_CLIENT_SECRET in .env "
            "(developer.nextdoor.com) or run: .venv/bin/python src/scrapers/nextdoor.py --login",
            flush=True,
        )
        return []

    return _fetch_playwright(cfg)


def _run_login() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium")
        raise SystemExit(1)

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    print("Browser opening — log into Nextdoor (verify your neighborhood if prompted).")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://nextdoor.com/login/", wait_until="domcontentloaded")

        for _ in range(360):
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


if __name__ == "__main__":
    if "--login" in sys.argv:
        _run_login()
    else:
        print("Usage: python src/scrapers/nextdoor.py --login")
        raise SystemExit(1)
