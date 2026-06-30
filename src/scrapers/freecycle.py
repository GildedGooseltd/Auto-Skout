import html as htmlmod
import json
import re
from typing import List

import requests

from scrapers.craigslist import HEADERS, RawListing

_session = requests.Session()
_session.trust_env = False

# Town slug → display name (slug is in URL: /town/{slug})
TOWN_URLS = {
    "PuebloCO": "https://www.freecycle.org/town/PuebloCO",
    "ColoradoSpringsCO": "https://www.freecycle.org/town/ColoradoSpringsCO",
    "DenverCO": "https://www.freecycle.org/town/DenverCO",
    "North_Denver_CO": "https://www.freecycle.org/town/North_Denver_CO",
    "ArvadaCO": "https://www.freecycle.org/town/ArvadaCO",
    "AuroraCO": "https://www.freecycle.org/town/AuroraCO",
    "CanonCityCO": "https://www.freecycle.org/town/CanonCityCO",
    "coloradocity": "https://www.freecycle.org/town/coloradocity",
    "WestcliffeCO": "https://www.freecycle.org/town/WestcliffeCO",
    "ElPasoCountySE_CO": "https://www.freecycle.org/town/ElPasoCountySE_CO",
}


def _parse_embedded_posts(html: str) -> list[dict]:
    match = re.search(r':data="(\{.*?)"\s+:viewer="0"', html, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(htmlmod.unescape(match.group(1)))
    except json.JSONDecodeError:
        return []
    return data.get("posts", [])


def fetch_offers(town_id: str) -> List[RawListing]:
    url = TOWN_URLS.get(town_id)
    if not url:
        print(f"  freecycle {town_id}: unknown town (add to TOWN_URLS)", flush=True)
        return []

    try:
        r = _session.get(url, headers=HEADERS, timeout=20)
        if not r.ok:
            print(f"  freecycle {town_id}: HTTP {r.status_code}", flush=True)
            return []
        html = r.text
    except Exception as e:
        print(f"  freecycle {town_id}: {e}", flush=True)
        return []

    out: List[RawListing] = []
    seen = set()

    for post in _parse_embedded_posts(html):
        post_type = (post.get("type") or {}).get("const", "")
        if post_type != "FC_POST_OFFER":
            continue

        title = (post.get("subject") or "").strip()
        post_id = post.get("id")
        if not title or not post_id:
            continue

        full = f"https://www.freecycle.org/posts/{post_id}"
        if full in seen:
            continue
        seen.add(full)

        location = (post.get("location") or town_id).strip()
        description = htmlmod.unescape((post.get("description") or "").strip())
        image_url = post.get("thumb") or ""
        thumbs = post.get("thumbs") or []
        image_urls: list[str] = []
        for thumb in thumbs:
            url = thumb if isinstance(thumb, str) else (thumb.get("url") or "")
            if url and "/thumb" in url:
                url = url.replace("/thumb", "/medium")
            if url and url not in image_urls:
                image_urls.append(url)
        if image_url and image_url not in image_urls:
            if "/thumb" in image_url:
                image_url = image_url.replace("/thumb", "/medium")
            image_urls.insert(0, image_url)
        if not image_url and image_urls:
            image_url = image_urls[0]

        out.append(
            RawListing(
                title=title,
                url=full,
                price="free",
                location=f"{location} ({town_id})",
                posting_id=full,
                    image_url=image_url,
                    description=description,
                    reply_url=full,
                    image_urls=image_urls,
                )
        )

    if out:
        print(f"  freecycle {town_id}: {len(out)} offers", flush=True)
    else:
        print(f"  freecycle {town_id}: 0 offers on town page", flush=True)
    return out
