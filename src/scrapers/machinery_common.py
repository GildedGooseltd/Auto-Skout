"""Shared helpers for textile / industrial sewing scrapers."""

from __future__ import annotations

import re
from typing import List, Optional

from vehicle_fields import parse_price_usd

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_TEXTILE_KEYWORDS = [
    "sewing machine",
    "industrial sewing",
    "commercial sewing",
    "ultrasonic sewing",
    "ultrasonic lace",
    "sonobond",
    "seammaster",
    "serger",
    "overlock",
    "embroidery machine",
    "quilting machine",
    "lace machine",
    "textile equipment",
    "walking foot",
    "blindstitch",
    "union special",
    "juki",
    "consew",
    "brother industrial",
    "pegasus sewing",
]

_PRICE_JSON_RE = re.compile(r'"price"\s*:\s*([\d.]+)')
_PRICE_ATTR_RE = re.compile(r'data-listing-price="([\d.]+)"')
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def browser_user_agent() -> str:
    return _BROWSER_UA


def textile_keywords(cfg: dict, search: Optional[dict] = None) -> list[str]:
    explicit = [str(k).strip() for k in (cfg.get("keywords") or cfg.get("search_terms") or []) if str(k).strip()]
    if explicit:
        return list(dict.fromkeys(explicit))
    if search:
        merged: list[str] = []
        merged.extend(search.get("textile_sewing") or [])
        for bucket in search.get("paid_wanted", []) or []:
            if bucket.get("name") == "machinery":
                merged.extend(bucket.get("keywords") or [])
        if merged:
            return list(dict.fromkeys(k.strip() for k in merged if k and str(k).strip()))
    return list(DEFAULT_TEXTILE_KEYWORDS)


def text_matches(blob: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    text = (blob or "").lower()
    return any(kw.lower() in text for kw in keywords)


def is_textile_listing(title: str, description: str, keywords: list[str]) -> bool:
    return text_matches(f"{title} {description}", keywords)


def price_ok(price: str, title: str, max_price: int, min_price: int = 0) -> bool:
    amount = parse_price_usd(price, title)
    if amount is None:
        return True
    if min_price and amount < min_price:
        return False
    return amount <= max_price


def strip_html(fragment: str) -> str:
    return _TAG_RE.sub(" ", fragment or "").replace("&nbsp;", " ").strip()


def parse_price_from_html(html: str, *, fallback_title: str = "") -> str:
    for blob in (html, fallback_title):
        m = _PRICE_ATTR_RE.search(blob or "")
        if m:
            try:
                val = float(m.group(1))
                if val > 0:
                    return f"${int(val):,}" if val == int(val) else f"${val:,.2f}"
            except ValueError:
                pass
        m = _PRICE_JSON_RE.search(blob or "")
        if m:
            try:
                val = float(m.group(1))
                if val > 0:
                    return f"${int(val):,}" if val == int(val) else f"${val:,.2f}"
            except ValueError:
                pass
    amount = parse_price_usd("", fallback_title)
    if amount:
        return f"${amount:,}"
    return "ask"


def parse_h1_from_html(html: str) -> str:
    m = _H1_RE.search(html or "")
    if not m:
        return ""
    return strip_html(m.group(1))


def og_image_from_html(html: str) -> str:
    m = re.search(
        r'<meta\s+(?:property=["\']og:image["\']\s+content=["\']([^"\']+)["\']'
        r'|content=["\']([^"\']+)["\']\s+property=["\']og:image["\'])',
        html or "",
        re.I,
    )
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def print_playwright_skip(label: str) -> None:
    print(f"  {label}: skipped — pip install playwright && playwright install chromium", flush=True)
