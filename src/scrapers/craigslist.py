import json
import re
import subprocess
import time
import html as html_module
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin

import requests

from scoring import is_trailer_listing

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_session = requests.Session()
_session.trust_env = False


_LD_JSON_RE = re.compile(
    r'<script type="application/ld\+json" id="ld_posting_data"\s*>\s*(.*?)\s*</script>',
    re.DOTALL | re.IGNORECASE,
)
_OG_IMAGE_RE = re.compile(
    r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_ALT_RE = re.compile(
    r'<meta\s+(?:property=["\']og:image["\']\s+content=["\']([^"\']+)["\']|content=["\']([^"\']+)["\']\s+property=["\']og:image["\'])',
    re.IGNORECASE,
)
_META_DESC_RE = re.compile(
    r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_DESC_RE = re.compile(
    r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_POSTINGBODY_RE = re.compile(r'<section id="postingbody">(.*?)</section>', re.DOTALL | re.IGNORECASE)
_MAILTO_REPLY_RE = re.compile(
    r'href=["\']mailto:([a-zA-Z0-9._%+-]+@sale\.craigslist\.org)["\']',
    re.IGNORECASE,
)
_CL_RELAY_EMAIL_RE = re.compile(
    r'([a-zA-Z0-9._%+-]+@sale\.craigslist\.org)',
    re.IGNORECASE,
)
_REPLY_PAGE_RE = re.compile(
    r'href=["\'](https?://[^"\']+/reply/[^"\']+)["\']',
    re.IGNORECASE,
)
_LISTING_ID_RE = re.compile(r"/(\d+)\.html(?:\?|$)")


def normalize_reply_url(reply_url: str, listing_url: str = "") -> str:
    """Craigslist reply links often include a __SERVICE_ID__ placeholder."""
    url = (reply_url or "").strip()
    if url:
        url = url.replace("/__SERVICE_ID__", "").rstrip("/")
        if "__SERVICE_ID__" in url:
            url = url.split("__SERVICE_ID__")[0].rstrip("/")
        return url
    if not listing_url:
        return ""
    m = _LISTING_ID_RE.search(listing_url)
    if not m:
        return listing_url
    pid = m.group(1)
    parts = re.match(r"https?://([^.]+)\.craigslist\.org/([^/]+)/", listing_url)
    if not parts:
        return listing_url
    sub, cat = parts.group(1), parts.group(2)
    area_map = {"denver": "den", "cosprings": "cos", "pueblo": "pub", "eastco": "eco", "rockies": "rck"}
    area = area_map.get(sub, sub[:3])
    return f"https://{sub}.craigslist.org/reply/{area}/{cat}/{pid}"


@dataclass
class RawListing:
    title: str
    url: str
    price: str
    location: str
    posting_id: str
    image_url: str = ""
    image_urls: list = field(default_factory=list)
    description: str = ""
    reply_email: str = ""
    reply_url: str = ""


def _parse_static_results(html: str, region: str) -> List[RawListing]:
    """Parse Craigslist no-JS fallback list (cl-static-search-result)."""
    out: List[RawListing] = []
    seen = set()

    for block in re.findall(
        r'<li class="cl-static-search-result"[^>]*title="([^"]*)"[^>]*>(.*?)</li>',
        html,
        re.DOTALL,
    ):
        title_hint, inner = block
        m_url = re.search(r'<a href="([^"]+)"', inner)
        m_title = re.search(r'<div class="title">([^<]+)</div>', inner)
        m_price = re.search(r'<div class="price">([^<]+)</div>', inner)
        m_loc = re.search(r'<div class="location">\s*([^<]+?)\s*</div>', inner)

        title = (m_title.group(1) if m_title else title_hint).strip()
        title = html_module.unescape(re.sub(r"\s+", " ", title))
        url = m_url.group(1).strip() if m_url else ""
        price = (m_price.group(1) if m_price else "free").strip()
        loc = (m_loc.group(1) if m_loc else region).strip()

        if not title or not url or url in seen:
            continue
        seen.add(url)
        out.append(
            RawListing(
                title=title,
                url=url,
                price=price,
                location=loc,
                posting_id=url,
            )
        )
    return out


def _parse_json(data: dict, region: str) -> List[RawListing]:
    out = []
    for item in data.get("data", []):
        title = (item.get("PostingTitle") or "").strip()
        if not title:
            continue
        pid = str(item.get("PostingID", title))
        post_url = item.get("url") or urljoin(
            f"https://{region}.craigslist.org", item.get("PostingURL", "")
        )
        price = str(item.get("Price", "free") or "free")
        loc = (item.get("PostingLocation") or item.get("Neighborhood") or region).strip()
        out.append(RawListing(title=title, url=post_url, price=price, location=loc, posting_id=pid))
    return out


def _curl_get(url: str) -> str:
    try:
        proc = subprocess.run(
            [
                "curl", "-sL", "-A", HEADERS["User-Agent"],
                "--compressed", "--max-time", "25", url,
            ],
            capture_output=True,
            text=True,
            timeout=25,
        )
        if proc.returncode == 0:
            return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _fetch(
    region: str,
    path: str,
    query: str = "",
    max_price: Optional[int] = None,
    min_price: Optional[int] = None,
) -> List[RawListing]:
    base = f"https://{region}.craigslist.org/search/{path}"
    params = ["sort=date"]
    if query:
        params.append(f"query={requests.utils.quote(query)}")
    if max_price is not None:
        params.append(f"max_price={max_price}")
    if min_price is not None:
        params.append(f"min_price={min_price}")
    html_url = f"{base}?{'&'.join(params)}"

    html = _curl_get(html_url)
    if _is_blocked(html):
        html = ""
    if not html:
        try:
            r = _session.get(html_url, headers=HEADERS, timeout=20)
            if r.ok and not _is_blocked(r.text):
                html = r.text
        except Exception as e:
            print(f"  fetch {region}/{path} q={query!r}: {e}", flush=True)

    if not html:
        return []

    items = _parse_static_results(html, region)
    if items:
        return items

    # Legacy JSON blob fallback
    try:
        json_url = html_url + ("&" if "?" in html_url else "?") + "format=json"
        r = _session.get(json_url, headers={**HEADERS, "Accept": "application/json"}, timeout=15)
        if r.ok and r.text.strip().startswith("{"):
            items = _parse_json(r.json(), region)
            if items:
                return items
    except Exception:
        pass

    return []


def fetch_free(region: str, category: str, query: str = "") -> List[RawListing]:
    return _fetch(region, category, query)


def fetch_paid(
    region: str,
    category: str,
    query: str,
    max_price: int,
    min_price: Optional[int] = None,
) -> List[RawListing]:
    return _fetch(region, category, query, max_price=max_price, min_price=min_price)


TRAILER_CATEGORIES = ("tra", "cta", "sss", "tla")
TRAILER_QUERIES = ("trailer", "utility trailer")


def fetch_trailers(region: str, *, max_price: Optional[int] = None) -> List[RawListing]:
    """Search CL trailer-relevant categories; keep only titles mentioning trailer."""
    seen: set[str] = set()
    out: List[RawListing] = []
    for path in TRAILER_CATEGORIES:
        for query in TRAILER_QUERIES:
            batch = _fetch(region, path, query, max_price=max_price)
            for raw in batch:
                if raw.url in seen:
                    continue
                if not is_trailer_listing(raw.title, getattr(raw, "description", "") or ""):
                    continue
                seen.add(raw.url)
                out.append(raw)
            time.sleep(0.35)
    return out


def _is_blocked(html: str) -> bool:
    if not html or len(html) < 400:
        return "blocked" in (html or "").lower() or "Your request has been blocked" in (html or "")
    return "Your request has been blocked" in html


def _fetch_listing_html(url: str) -> str:
    html = _curl_get(url)
    if html and not _is_blocked(html):
        return html
    time.sleep(0.45)
    try:
        r = _session.get(url, headers=HEADERS, timeout=15)
        if r.ok and not _is_blocked(r.text):
            return r.text
    except Exception:
        pass
    if html and not _is_blocked(html):
        return html
    return html or ""


def _strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_listing_details(html: str) -> dict:
    image_urls: list[str] = []
    description = ""

    for match in _OG_IMAGE_ALT_RE.finditer(html):
        url = (match.group(1) or match.group(2) or "").strip()
        if url and url not in image_urls:
            image_urls.append(url)

    ld_match = _LD_JSON_RE.search(html)
    if ld_match:
        try:
            data = json.loads(ld_match.group(1))
            if not description:
                description = (data.get("description") or "").strip()
            images = data.get("image") or []
            if isinstance(images, str) and images:
                images = [images]
            if isinstance(images, list):
                for img in images:
                    url = str(img).strip()
                    if url and url not in image_urls:
                        image_urls.append(url)
        except (json.JSONDecodeError, TypeError, IndexError):
            pass

    image_url = image_urls[0] if image_urls else ""

    body = _POSTINGBODY_RE.search(html)
    if body:
        raw = body.group(1)
        raw = re.sub(r'<div class="print-information.*?</div>', " ", raw, flags=re.DOTALL)
        posting = _strip_html(raw)
        if posting and len(posting) > len(description):
            description = posting

    if not description:
        desc_match = _OG_DESC_RE.search(html)
        if desc_match:
            description = desc_match.group(1).strip()
    if not description:
        meta = _META_DESC_RE.search(html)
        if meta:
            description = meta.group(1).strip()

    reply_email = ""
    reply_url = ""
    mailto = _MAILTO_REPLY_RE.search(html)
    if mailto:
        reply_email = mailto.group(1)
    if not reply_email:
        relay = _CL_RELAY_EMAIL_RE.search(html)
        if relay:
            reply_email = relay.group(1)
    reply = _REPLY_PAGE_RE.search(html)
    if reply:
        reply_url = normalize_reply_url(reply.group(1))

    return {
        "image_url": image_url,
        "image_urls": image_urls,
        "description": description,
        "reply_email": reply_email,
        "reply_url": reply_url,
    }


def fetch_listing_details(url: str) -> dict:
    for attempt in range(3):
        html = _fetch_listing_html(url)
        if html and not _is_blocked(html):
            return parse_listing_details(html)
        time.sleep(1.5 * (attempt + 1))
    return {"image_url": "", "image_urls": [], "description": "", "reply_email": "", "reply_url": ""}


def fetch_reply_email(reply_url: str, listing_url: str = "") -> str:
    """Load Craigslist reply page and extract the relay @sale.craigslist.org address."""
    url = normalize_reply_url(reply_url, listing_url)
    if not url or "/reply/" not in url:
        return ""
    html = _fetch_listing_html(url)
    if not html:
        return ""
    mailto = _MAILTO_REPLY_RE.search(html)
    if mailto:
        return mailto.group(1)
    relay = _CL_RELAY_EMAIL_RE.search(html)
    return relay.group(1) if relay else ""


def fetch_listing_image(url: str) -> str:
    return fetch_listing_details(url).get("image_url", "")


def enrich_listing_details(
    listings: list,
    *,
    cache_get,
    cache_set,
    max_fetch: int = 500,
    workers: int = 2,
    delay_sec: float = 0.35,
) -> None:
    """Attach image_url and description (mutates listing fields)."""
    to_fetch: list = []
    for listing in listings:
        has_img = bool(listing.image_url)
        has_desc = bool(getattr(listing, "description", ""))
        has_reply = bool(getattr(listing, "reply_email", ""))
        if has_img and has_desc and has_reply:
            continue
        cached = cache_get(listing.url)
        if cached.get("image_url") and not listing.image_url:
            listing.image_url = cached["image_url"]
        if cached.get("description") and not getattr(listing, "description", ""):
            listing.description = cached["description"]
        if cached.get("reply_email") and not getattr(listing, "reply_email", ""):
            listing.reply_email = cached["reply_email"]
        if cached.get("reply_url") and not getattr(listing, "reply_url", ""):
            listing.reply_url = cached["reply_url"]
        if listing.image_url and getattr(listing, "description", "") and getattr(listing, "reply_email", ""):
            continue
        to_fetch.append(listing)

    if not to_fetch:
        return

    if max_fetch > 0:
        to_fetch = to_fetch[:max_fetch]

    def _one(item):
        time.sleep(delay_sec)
        details = fetch_listing_details(item.url)
        if not details.get("reply_email"):
            reply_url = details.get("reply_url") or normalize_reply_url("", item.url)
            if reply_url:
                email = fetch_reply_email(reply_url, item.url)
                if email:
                    details["reply_email"] = email
        if any(details.values()):
            cache_set(
                item.url,
                details.get("image_url", ""),
                details.get("description", ""),
                details.get("reply_email", ""),
                details.get("reply_url", ""),
            )
        return item, details

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, item): item for item in to_fetch}
        for fut in as_completed(futures):
            try:
                item, details = fut.result()
                if details.get("image_url"):
                    item.image_url = details["image_url"]
                if details.get("image_urls"):
                    item.image_urls = details["image_urls"]
                elif details.get("image_url"):
                    item.image_urls = [details["image_url"]]
                if details.get("description"):
                    item.description = details["description"]
                if details.get("reply_email"):
                    item.reply_email = details["reply_email"]
                if details.get("reply_url"):
                    item.reply_url = details["reply_url"]
            except Exception:
                pass


def enrich_listing_images(listings, **kwargs):
    """Backward-compatible alias."""
    enrich_listing_details(listings, **kwargs)
