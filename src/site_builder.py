import hashlib
import json
import re
import shutil
import subprocess
from datetime import date, datetime
from html import escape, unescape
from pathlib import Path
from typing import Optional

from date_format import format_range, trip_labels
from pickup_message import build_pickup_message
from route_matcher import match_destination, match_routes
from scrapers.craigslist import normalize_reply_url
from dedupe import normalize_also_on, pick_verified_open_url
from scoring import is_free_by_price, is_priority_match
from vehicle_fields import compute_vehicle_fit, is_vehicle_listing, parse_price_usd

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

# Gilded Goose Limited — luxe minimalist palette (shared across Skout apps)
GGL_THEME_CSS = """
    :root {
      --gg-charcoal: #1a1a1c;
      --gg-charcoal-soft: #2c2c30;
      --gg-ivory: #f7f4ef;
      --gg-ivory-warm: #f5f0e8;
      --gg-paper: #ffffff;
      --gg-ink: #1c1917;
      --gg-muted: #78716c;
      --gg-border: #e7e5e4;
      --gg-border-soft: #e5e0d8;
      --gg-gold: #c9a962;
      --gg-gold-light: #e8d5a8;
      --gg-gold-dark: #9a7b3a;
      --gg-gold-bg: #faf6ee;
      --gg-gold-bg-strong: #f3ead6;
      --gg-accent: #c9a962;
      --gg-accent-hover: #b08d45;
      --gg-serif: Georgia, 'Times New Roman', Times, serif;
      --gg-sans: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }
"""

VERTICAL_SUB_ACCENT = {
    "vehicles": "#8a9aaa",
    "estate_sales": "#d4c4a8",
    "farm_resale": "#c9a962",
    "general": "#c9a962",
}

RECOMMENDED_PLATFORMS = [
    {"id": "offerup", "name": "OfferUp", "icon": "📦", "status": "active",
     "why": "Free listings near your zip — in-app chat to reply"},
    {"id": "buy_nothing", "name": "Buy Nothing", "icon": "🎁", "status": "manual",
     "why": "Gift economy groups — often on Facebook"},
    {"id": "facebook_marketplace", "name": "FB Marketplace", "icon": "📘", "status": "setup",
     "why": "Broad local coverage — needs login"},
    {"id": "facebook_groups", "name": "FB Groups", "icon": "👥", "status": "setup",
     "why": "Free & farm groups — run --discover-groups"},
    {"id": "freecycle", "name": "Freecycle", "icon": "♻️", "status": "setup",
     "why": "Town giveaway boards — join groups first"},
]

# GitHub Pages subfolder per profile (see scripts/pages-path.sh).
PAGES_PATH_BY_PROFILE = {
    "gardner-farm": "skout",
    "kate-vehicles": "auto-skout",
    "estate-skout": "estate-skout",
    "kate-art": "art",
}

SKOUT_APP_TABS = [
    ("gardner-farm", "Skout"),
    ("kate-vehicles", "Auto Skout"),
    ("estate-skout", "Estate Skout"),
]


def _pages_path(profile_id: str) -> str:
    return PAGES_PATH_BY_PROFILE.get(profile_id, profile_id)


def _app_tabs(profile_id: str) -> list[dict]:
    current = _pages_path(profile_id)
    tabs = []
    for pid, label in SKOUT_APP_TABS:
        path = _pages_path(pid)
        tabs.append({
            "id": pid,
            "label": label,
            "href": "./" if path == current else f"../{path}/",
            "active": pid == profile_id,
        })
    return tabs

TYPE_FAMILIES = [
    {"label": "Farm & garden", "cats": [
        "plants", "trees_shrubs", "hoses_irrigation", "dirt_soil", "livestock", "greenhouse",
    ]},
    {"label": "Building & yard", "cats": [
        "lumber", "rocks_bricks", "fencing", "pallets",
    ]},
    {"label": "Tools & machines", "cats": ["tools", "machinery"]},
    {"label": "Home & resale", "cats": ["furniture", "antiques", "curb_lot"]},
    {"label": "Vehicles", "cats": ["cars", "trucks", "travel_rv", "tow_equipment", "trailers"]},
    {"label": "Other", "cats": ["other", "paid_wanted"]},
]

ROOT = Path(__file__).resolve().parent.parent


def _quick_searches_payload(search_cfg: dict, profile: dict) -> dict:
    raw = search_cfg.get("quick_searches") or {}
    home = profile.get("home") or {}
    # Empty = nationwide; do not fall back to Pueblo
    if "route_dest" in raw:
        route_dest = raw.get("route_dest") or ""
    else:
        route_dest = ""
    presets = []
    for p in raw.get("presets") or []:
        if not p.get("id") or not p.get("keywords"):
            continue
        presets.append({
            "id": p["id"],
            "label": p.get("label") or p["id"],
            "icon": p.get("icon") or "",
            "keywords": list(p.get("keywords") or []),
            "route_dest": p["route_dest"] if "route_dest" in p else route_dest,
        })
    return {
        "route_dest": route_dest,
        "route_label": raw.get("route_label") or (
            f"Route to {route_dest}" if route_dest else "Nationwide"
        ),
        "sort_default": raw.get("sort_default") or "score",
        "presets": presets,
    }


def _locations_payload(routes_cfg: dict, profile: dict) -> list[dict]:
    """Route-based focus areas — no dates (dashboard location filter)."""
    routes = routes_cfg.get("routes", {})
    order = routes_cfg.get("location_order") or [
        "gardner_local",
        "gardner_to_pueblo",
        "gardner_to_cos",
        "gardner_to_denver",
        "denver_metro",
    ]
    home_city = (profile.get("home") or {}).get("city", "")
    out: list[dict] = []
    for route_id in order:
        route = routes.get(route_id)
        if not route:
            continue
        cities = route.get("cities") or []
        tags = route.get("tags") or []
        if "near_home" in tags:
            dest = cities[0] if cities else home_city
        else:
            dest = cities[-1] if cities else home_city
        out.append({
            "id": f"route-{route_id}",
            "name": route.get("name") or route_id.replace("_", " ").title(),
            "city": dest,
            "route_dest": dest,
            "notes": route.get("description", ""),
        })
    for route_id, route in routes.items():
        lid = f"route-{route_id}"
        if any(x["id"] == lid for x in out):
            continue
        cities = route.get("cities") or []
        dest = cities[-1] if cities else home_city
        out.append({
            "id": lid,
            "name": route.get("name") or route_id.replace("_", " ").title(),
            "city": dest,
            "route_dest": dest,
            "notes": route.get("description", ""),
        })
    return out


def _image_referer(url: str) -> str:
    u = (url or "").lower()
    m = re.match(r"https?://([a-z0-9-]+\.craigslist\.org)", u)
    if m:
        return f"https://{m.group(1)}/"
    if "craigslist.org" in u:
        return "https://www.craigslist.org/"
    if "fbcdn.net" in u or "facebook.com" in u:
        return "https://www.facebook.com/"
    if "offerup.com" in u or "offerupcdn.com" in u:
        return "https://offerup.com/"
    return ""


def _curl_image_bytes(url: str, referer: str = "") -> Optional[bytes]:
    cmd = [
        "curl", "-fsSL",
        "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "--max-time", "18",
    ]
    if referer:
        cmd.extend(["-e", referer])
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=22)
        if proc.returncode == 0 and len(proc.stdout) > 400:
            return proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _download_image(url: str, session) -> Optional[bytes]:
    referer = _image_referer(url)
    headers = {"Accept": "image/*,*/*"}
    if referer:
        headers["Referer"] = referer
    try:
        resp = session.get(url, timeout=15, headers=headers)
        if resp.ok and len(resp.content) > 400:
            return resp.content
    except Exception:
        pass
    alt_urls = []
    if "_300x300" in url:
        alt_urls.append(url.replace("_300x300", "_600x450"))
    if "50x50c" in url:
        alt_urls.append(url.replace("50x50c", "600x450"))
    for alt in alt_urls:
        if alt == url:
            continue
        try:
            resp = session.get(alt, timeout=15, headers=headers)
            if resp.ok and len(resp.content) > 400:
                return resp.content
        except Exception:
            pass
    return _curl_image_bytes(url, referer)


def _mirror_listing_images(listings: list[dict], assets_dir: Path) -> tuple[int, int]:
    """Save listing photos under site/assets for GitHub Pages (hotlink blocks)."""
    try:
        import requests
    except ImportError:
        return 0, 0
    photo_dir = assets_dir / "listing-photos"
    photo_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; AutoSkout/1.0)",
        "Accept": "image/*,*/*",
    })
    ok = fail = 0
    for item in listings:
        urls = [u for u in (item.get("image_urls") or []) if u]
        if not urls and item.get("image_url"):
            urls = [item["image_url"]]
        local: list[str] = []
        for i, url in enumerate(urls[:4]):
            if not url or str(url).startswith("assets/"):
                local.append(url)
                continue
            digest = hashlib.sha1(f"{url}|{i}".encode()).hexdigest()[:14]
            path = photo_dir / f"{digest}.jpg"
            rel = f"assets/listing-photos/{digest}.jpg"
            if path.exists() and path.stat().st_size > 400:
                local.append(rel)
                continue
            try:
                data = _download_image(url, session)
                if data:
                    path.write_bytes(data)
                    local.append(rel)
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
        if local:
            item["image_url"] = local[0]
            item["image_urls"] = local
        elif urls:
            item["image_url"] = urls[0]
            item["image_urls"] = urls[:4]
    return ok, fail


def _load_web_marketplaces(profile: dict) -> dict:
    """Load Autotrader-class bookmark sources for vehicle profiles."""
    raw = profile.get("web_marketplaces") or ""
    candidates = []
    if raw:
        p = Path(str(raw)).expanduser()
        candidates.append(p if p.is_absolute() else ROOT / raw)
    candidates.append(
        ROOT.parent / "Documents" / "1 Cursor Helper" / "personal" / "vehicle-market" / "web-sources.yaml"
    )
    try:
        import yaml
    except ImportError:
        return {}
    for path in candidates:
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return {
                "shop": data.get("shop", {}),
                "sites": data.get("sites", []),
                "searches": data.get("searches", []),
            }
    return {}


VEHICLE_SOURCE_ORDER = [
    ("craigslist", "Craigslist", "🟠", "craigslist"),
    ("offerup", "OfferUp", "📱", "offerup"),
    ("facebook", "Facebook", "📘", "facebook_marketplace"),
    ("web:cars_com", "Cars.com", "🚗", "web_marketplaces_scrape"),
    ("web:truecar", "TrueCar", "💲", "web_marketplaces_scrape"),
    ("web:ebay_motors", "eBay Motors", "🛒", "web_marketplaces_scrape"),
    ("web:hemmings", "Hemmings", "🏁", "web_marketplaces_scrape"),
    ("web:autotrader", "Autotrader", "🚙", "web_marketplaces_scrape"),
    ("web:cargurus", "CarGurus", "📊", "web_marketplaces_scrape"),
    ("web:privateauto", "PrivateAuto", "🤝", "web_marketplaces_scrape"),
    ("web:autolist", "Autolist", "📋", "web_marketplaces_scrape"),
    ("web:iseecars", "iSeeCars", "🔍", "web_marketplaces_scrape"),
    ("auction:govdeals", "GovDeals", "🏛", "auction_scrape"),
    ("auction:publicsurplus", "Public Surplus", "📋", "auction_scrape"),
    ("auction:propertyroom", "PropertyRoom", "👮", "auction_scrape"),
]


def _vehicle_source_counts(listings: list[dict], platforms_cfg: dict) -> list[dict]:
    from collections import Counter

    counts: Counter = Counter()
    for item in listings:
        src = item.get("source") or ""
        if src.startswith("craigslist"):
            counts["craigslist"] += 1
        elif src.startswith("facebook"):
            counts["facebook"] += 1
        elif src.startswith("web:"):
            counts[src] += 1
        elif src.startswith("auction:"):
            counts[src] += 1
        else:
            counts[src.split(":")[0]] += 1

    out = []
    for sid, label, icon, plat_key in VEHICLE_SOURCE_ORDER:
        plat = platforms_cfg.get(plat_key, {})
        if plat_key == "facebook_marketplace":
            enabled = (
                platforms_cfg.get("facebook_marketplace", {}).get("enabled")
                or platforms_cfg.get("facebook_groups", {}).get("enabled")
            )
        elif plat_key == "web_marketplaces_scrape":
            enabled = platforms_cfg.get("web_marketplaces_scrape", {}).get("enabled")
        elif plat_key == "auction_scrape":
            enabled = platforms_cfg.get("auction_scrape", {}).get("enabled")
        else:
            enabled = plat.get("enabled")
        count = counts[sid] if sid.startswith(("web:", "auction:")) else counts.get(sid, 0)
        if not enabled and count == 0:
            continue
        out.append({"id": sid, "label": label, "icon": icon, "count": count})
    return out


def _parse_length_ft(title: str, description: str = "") -> Optional[int]:
    blob = f"{title} {description}"
    m = re.search(r"\b(3[0-9]|4[0-2])\s*[\'′]?\s*(?:ft|foot|feet)\b", blob, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{2})\s*ft\b", blob, re.I)
    if m:
        val = int(m.group(1))
        if 28 <= val <= 42:
            return val
    return None


def _avion_comp_stats(sell_listings: list[dict], target: dict) -> dict:
    prices = sorted(p for p in (item.get("price_usd") for item in sell_listings) if p)
    floor = int(target.get("min_ask_usd") or 26000)
    median = prices[len(prices) // 2] if prices else None
    if prices and len(prices) % 2 == 0 and len(prices) > 1:
        median = int((prices[len(prices) // 2 - 1] + prices[len(prices) // 2]) / 2)
    above = sum(1 for p in prices if p >= floor)
    return {
        "count": len(sell_listings),
        "priced_count": len(prices),
        "median_usd": median,
        "min_usd": prices[0] if prices else None,
        "max_usd": prices[-1] if prices else None,
        "floor_usd": floor,
        "above_floor_count": above,
        "target_window": target.get("target_window", ""),
        "timing_note": (
            "Strongest buyer pools: NorCal + Pacific NW · snowbird season Nov–Mar (AZ/FL). "
            "Plan to list toward Summer 2027 — watch comps through winter 2026–27."
        ),
    }


def _apply_open_urls(listings: list[dict]) -> None:
    """Prefer verified non-Craigslist URLs when duplicates were merged."""
    session = None
    try:
        import requests
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; AutoSkout/1.0)"})
    except ImportError:
        pass
    for item in listings:
        target = pick_verified_open_url(item, session)
        item["open_url"] = target.get("url") or item.get("url") or ""
        item["open_source"] = target.get("source") or item.get("source") or ""
        item["open_platform"] = target.get("platform") or item.get("platform") or ""
        item["open_platform_icon"] = target.get("platform_icon") or item.get("platform_icon") or "🔗"


def _route_tag_labels(routes_cfg: dict, flags: dict) -> dict:
    """Map tag → human label for display."""
    labels = {}
    for route_id in flags.get("route_ids", []):
        route = routes_cfg.get("routes", {}).get(route_id, {})
        for tag in route.get("tags", []):
            labels[tag] = route.get("filter_label", tag.replace("_", " ").title())
    return labels


def _contact_info(listing) -> dict:
    src = listing.source.split(":")[0] if ":" in listing.source else listing.source
    reply_email = getattr(listing, "reply_email", "") or ""
    listing_url = listing.url or ""
    reply_url = normalize_reply_url(
        getattr(listing, "reply_url", "") or "",
        listing_url,
    )
    site_labels = {
        "craigslist": "Reply on Craigslist",
        "freecycle": "Reply on Freecycle",
        "trash_nothing": "Reply on Trash Nothing",
        "nextdoor": "Message on Nextdoor",
        "offerup": "Message on OfferUp",
        "facebook": "Message on Facebook",
        "facebook_group": "Message in group",
        "buy_nothing": "Reply in group",
    }
    if reply_email:
        return {
            "contact_method": "email",
            "contact_label": "Copy seller email",
            "contact_url": "",
        }
    if reply_url and reply_url != listing_url:
        return {
            "contact_method": "site",
            "contact_label": site_labels.get(src, "Contact on site"),
            "contact_url": reply_url,
        }
    if listing_url:
        return {
            "contact_method": "site",
            "contact_label": site_labels.get(src, "Open listing"),
            "contact_url": listing_url,
        }
    return {
        "contact_method": "copy",
        "contact_label": "Copy message",
        "contact_url": "",
    }


def _listing_dict(
    listing, score: int, tier: str, route_flags: dict, route_labels: dict, template: str, *, search: dict,
    is_new: bool = True,
    profile_id: str = "",
    vertical: str = "",
    shop_rules: Optional[dict] = None,
    home: Optional[dict] = None,
) -> dict:
    title = unescape(listing.title)
    contact = _contact_info(listing)
    posting_id = f"{profile_id}:{listing.source}:{listing.url}"
    desc = unescape(listing.description or "")
    pref = (search.get("make_preference") or {}).get("make", "")
    vehicle: dict = {}
    if vertical == "vehicles":
        rules = shop_rules or {}
        home_cfg = home or {}
        vehicle = compute_vehicle_fit(
            title,
            desc,
            listing.price,
            listing.location,
            listing.category_id,
            make_preference=pref,
            max_price_usd=int(rules.get("max_price_usd") or 20000),
            home_city=home_cfg.get("city", "Gardner"),
            search=search,
        )
    return {
        "id": listing.url,
        "posting_id": posting_id,
        "title": title,
        "url": listing.url,
        "price": listing.price,
        "location": listing.location,
        "score": score,
        "tier": tier,
        "source": listing.source,
        "platform": listing.platform_label,
        "platform_icon": listing.platform_icon,
        "category_id": listing.category_id,
        "category_label": listing.category_label,
        "category_icon": listing.category_icon,
        "route_tags": route_labels,
        "route_ids": route_flags.get("route_ids", []),
        "template": template,
        "pickup_message": build_pickup_message(template, title),
        "image_url": listing.image_url or "",
        "image_urls": list(listing.image_urls or []) or ([listing.image_url] if listing.image_url else []),
        "description": desc,
        "reply_email": getattr(listing, "reply_email", "") or "",
        "reply_url": normalize_reply_url(
            getattr(listing, "reply_url", "") or "",
            listing.url or "",
        ),
        "contact_method": contact["contact_method"],
        "contact_label": contact["contact_label"],
        "contact_url": contact["contact_url"],
        "is_priority": is_priority_match(listing.title, search),
        "is_free": is_free_by_price(
            listing.price, is_paid_wanted=listing.is_paid_wanted
        ),
        "is_free_price": is_free_by_price(
            listing.price, is_paid_wanted=listing.is_paid_wanted
        ),
        "is_new": is_new,
        "is_estate_sale": listing.source.split(":")[0] == "estate_sales",
        "sale_dates": (
            (listing.price or "")
            if listing.source.split(":")[0] == "estate_sales"
            and (listing.price or "").strip().lower() not in ("estate sale", "sale", "")
            else ""
        ),
        "also_on": normalize_also_on(
            getattr(listing, "also_on", None) or [],
            primary_url=listing.url or "",
        ),
        "year": vehicle.get("year", ""),
        "make": vehicle.get("make", ""),
        "model": vehicle.get("model", ""),
        "miles": vehicle.get("miles", ""),
        "make_preferred": vehicle.get("make_preferred", False),
        "preferred_match": vehicle.get("preferred_match", ""),
        "is_rebuilt": vehicle.get("is_rebuilt", False),
        "is_fleet": vehicle.get("is_fleet", False),
        "price_usd": vehicle.get("price_usd"),
        "location_band": vehicle.get("location_band", "other"),
        "fit_score": vehicle.get("fit_score", 0),
        "fit_label": vehicle.get("fit_label", ""),
        "fit_tow_class": vehicle.get("fit_tow_class", ""),
        "is_diesel": vehicle.get("is_diesel", False),
        "is_hd_tow": vehicle.get("is_hd_tow", False),
        "is_commercial": vehicle.get("is_commercial", False),
        "is_auction": vehicle.get("is_auction", False),
        "grant_credit_angle": vehicle.get("grant_credit_angle", False),
        "avoid_ram": vehicle.get("avoid_ram", False),
        "avoid_ford_60": vehicle.get("avoid_ford_60", False),
        "is_vintage_square": vehicle.get("is_vintage_square", False),
        "is_vintage_quality": vehicle.get("is_vintage_quality", False),
    }


def write_site(
    items: list[tuple],
    loc: dict,
    cfg: dict,
    *,
    total_checked: int,
    new_count: int,
    show_all: bool = False,
    new_urls: set = None,
    channel_stats: list = None,
    duplicates_removed: int = 0,
    focus_trip_id: str = "",
    trailer_hunt: bool = False,
    sell_items: Optional[list] = None,
) -> Path:
    if channel_stats is None:
        channel_stats = []
    if new_urls is None:
        new_urls = set()
    routes_cfg = cfg["routes"]
    routes = routes_cfg.get("routes", {})
    profile = cfg["profile"]
    meta = cfg.get("profile_meta", profile)
    deploy = cfg.get("deploy", {})
    app = profile.get("app_name", "Skout")
    branding = profile.get("branding") or {}
    logo_128 = branding.get("logo_icon", "ggl-goose-mark.svg")
    logo_64 = branding.get("logo_favicon", "ggl-goose-mark.svg")
    wordmark = branding.get("wordmark") or app
    wordmark_parts = branding.get("wordmark_parts") or []
    footer_org = branding.get("footer_org", "Gilded Goose Limited")
    if not wordmark_parts and wordmark:
        parts = wordmark.split(None, 1)
        wordmark_parts = [
            {"text": parts[0], "class": "logo-gilded"},
            {"text": parts[1] if len(parts) > 1 else "", "class": "logo-accent"},
        ]
    template = cfg["scoring"].get("response_template", "").strip()
    loc_label = f"{loc.get('city', '')}, {loc.get('state', '')} {loc.get('zip', '')}"
    now = datetime.now().strftime("%a %b %-d, %-I:%M %p")
    pages_cfg = profile.get("pages") or {}
    public_url = pages_cfg.get("public_url") or deploy.get("public_url", "")

    search_cfg = cfg.get("search", {})
    today = date.today()

    def _location_payload(loc: dict, index: int) -> dict:
        lid = re.sub(r"[^a-z0-9]+", "-", loc.get("name", "area").lower()).strip("-") or "area"
        lid = f"{lid}-{index}"
        return {
            "id": lid,
            "name": loc.get("name", ""),
            "city": loc.get("city", ""),
            "route_dest": loc.get("route_dest") or loc.get("city", ""),
            "notes": loc.get("notes", ""),
        }

    travel = cfg.get("travel", {})
    route_locations = _locations_payload(routes_cfg, profile)
    yaml_locs = travel.get("locations") or []
    if yaml_locs:
        all_locations = [_location_payload(loc, i) for i, loc in enumerate(yaml_locs)]
    elif route_locations:
        all_locations = route_locations
    else:
        all_trips_raw = travel.get("trips", [])
        all_locations = []
        for i, trip in enumerate(all_trips_raw):
            all_locations.append({
                "id": re.sub(r"[^a-z0-9]+", "-", trip.get("name", "area").lower()).strip("-") + f"-{i}",
                "name": trip.get("name", ""),
                "city": trip.get("city", ""),
                "route_dest": trip.get("city", ""),
                "notes": trip.get("notes", ""),
            })

    all_locations.sort(key=lambda x: x.get("name", "").lower())

    new_url_set = new_urls

    active_trip_label = loc.get("name", "") or loc.get("city", "")

    pickup = cfg.get("scoring", {}).get("pickup_contact", {})

    seen_tags = set()
    all_cats = {c["id"]: c for c in cfg.get("categories", {}).get("categories", [])}
    exclude_ids = search_cfg.get("exclude_categories", [])
    exclude_categories = [
        {
            "id": cat_id,
            "label": all_cats[cat_id]["label"],
            "icon": all_cats[cat_id]["icon"],
        }
        for cat_id in exclude_ids
        if cat_id in all_cats
    ]
    exclude_type_options = [
        {"id": cat_id, "label": cat["label"]}
        for cat_id, cat in sorted(all_cats.items(), key=lambda x: x[1]["label"])
        if cat_id not in ("paid_wanted",)
    ]
    exclude_title_keywords = search_cfg.get("exclude", {}).get("title_keywords", [])

    route_city_hints = sorted({
        city
        for route in routes.values()
        for city in route.get("cities", [])
        if city
    })

    profile_id = meta.get("id", "")
    vertical = meta.get("vertical", "")
    display_cfg = profile.get("display") or {}
    ui_defaults: dict = {}
    if "default_free_only" in display_cfg:
        ui_defaults["free_only"] = bool(display_cfg["default_free_only"])
    if display_cfg.get("source_only"):
        ui_defaults["source_only"] = list(display_cfg["source_only"])
    app_tabs = _app_tabs(profile_id)
    listings = []
    for listing, score, tier in items:
        flags = match_routes(listing.location, routes_cfg)
        labels = _route_tag_labels(routes_cfg, flags)
        listings.append(_listing_dict(
            listing, score, tier, flags, labels, template, search=search_cfg,
            # --all / sewing show: keep tiles at full opacity (not “seen” ghost)
            is_new=True if show_all else (listing.url in new_url_set),
            profile_id=profile_id,
            vertical=vertical,
            shop_rules=profile.get("shop_rules") or {},
            home=profile.get("home") or {},
        ))
    listings.sort(key=lambda item: (
        0 if item.get("is_new") else 1,
        0 if vertical != "vehicles" or item.get("make_preferred") else 1,
        -int(item.get("fit_score") or 0) if vertical == "vehicles" else 0,
        -item["is_priority"],
        -item["score"],
        item["title"],
    ))
    _apply_open_urls(listings)

    sale_targets = profile.get("sale_targets") or {}
    avion_target = sale_targets.get("avion") or {}
    shop_rules = profile.get("shop_rules") or {}
    sell_listings: list[dict] = []
    for listing, score, tier in sell_items or []:
        flags = match_routes(listing.location, routes_cfg)
        labels = _route_tag_labels(routes_cfg, flags)
        row = _listing_dict(
            listing, score, tier, flags, labels, template, search=search_cfg,
            is_new=listing.url in new_url_set,
            profile_id=profile_id,
            vertical=vertical,
            shop_rules=shop_rules,
            home=profile.get("home") or {},
        )
        price_usd = parse_price_usd(listing.price, listing.title)
        length_ft = _parse_length_ft(listing.title, listing.description or "")
        row["price_usd"] = price_usd
        row["length_ft"] = length_ft
        if price_usd and avion_target.get("min_ask_usd"):
            floor = int(avion_target["min_ask_usd"])
            row["comp_vs_floor"] = price_usd - floor
            row["comp_label"] = "At/above floor" if price_usd >= floor else "Below floor"
        sell_listings.append(row)
    sell_listings.sort(key=lambda x: (
        0 if (x.get("title") or "").lower().find("avion") >= 0 else 1,
        -abs((x.get("length_ft") or 0) - int(avion_target.get("length_ft") or 36)),
        -(x.get("price_usd") or 0),
    ))
    comp_stats = _avion_comp_stats(sell_listings, avion_target) if avion_target else {}
    market_tabs_enabled = bool(avion_target and (sell_listings or vertical == "vehicles"))
    buy_hold_until = shop_rules.get("truck_buy_hold_until") or ""

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    assets_dest = SITE_DIR / "assets"
    assets_dest.mkdir(parents=True, exist_ok=True)
    if ASSETS_DIR.is_dir():
        for asset in ASSETS_DIR.iterdir():
            if asset.is_file() and not asset.name.startswith("."):
                shutil.copy2(asset, assets_dest / asset.name)
    if deploy.get("mirror_images", True) and listings:
        mirrored, mirror_fail = _mirror_listing_images(listings, assets_dest)
        if mirrored or mirror_fail:
            print(f"  Mirrored {mirrored} photos ({mirror_fail} failed)", flush=True)

    cats_in_feed = {}
    sources_in_feed = {}
    for item in listings:
        cats_in_feed[item["category_id"]] = (item["category_icon"], item["category_label"])
        src_key = item["source"].split(":")[0]
        sources_in_feed[src_key] = (item["platform_icon"], item["platform"])

    platforms_cfg = cfg.get("platforms", {})
    active_sources = []

    def _platform_on(key: str) -> bool:
        if key == "facebook":
            return bool(
                platforms_cfg.get("facebook_marketplace", {}).get("enabled")
                or platforms_cfg.get("facebook_groups", {}).get("enabled")
            )
        return bool(platforms_cfg.get(key, {}).get("enabled"))

    source_filter_defs = [
        ("src:craigslist", "Craigslist", "craigslist"),
        ("src:freecycle", "Freecycle", "freecycle"),
        ("src:facebook", "Facebook", "facebook"),
        ("src:trash_nothing", "Trash Nothing", "trash_nothing"),
        ("src:nextdoor", "Nextdoor", "nextdoor"),
        ("src:offerup", "OfferUp", "offerup"),
        ("src:buy_nothing", "Buy Nothing", "buy_nothing"),
        ("src:estate_sales", "Estate sales", "estate_sales"),
        ("src:marketplace", "Machinio · eBay", "textile_marketplaces"),
        ("src:textile_auction", "Textile auctions", "textile_auctions"),
        ("src:dealer", "Dealer inventory", "dealer_inventory"),
    ]
    source_filter_options = [
        {"id": sid, "label": lbl}
        for sid, lbl, key in source_filter_defs
        if _platform_on(key)
    ]
    source_labels = {
        "craigslist": "Craigslist",
        "freecycle": "Freecycle",
        "facebook_marketplace": "Facebook",
        "facebook_groups": "Facebook",
        "trash_nothing": "Trash Nothing",
        "nextdoor": "Nextdoor",
        "offerup": "OfferUp",
        "buy_nothing": "Buy Nothing",
        "estate_sales": "Estate sales",
        "textile_marketplaces": "Machinio · eBay",
        "textile_auctions": "Textile auctions",
        "dealer_inventory": "Dealer inventory",
    }
    for key, label in source_labels.items():
        if platforms_cfg.get(key, {}).get("enabled") and label not in active_sources:
            active_sources.append(label)
    if trailer_hunt and platforms_cfg.get("web_marketplaces_scrape", {}).get("enabled"):
        if "Web marketplaces" not in active_sources:
            active_sources.append("Web marketplaces")
        source_filter_options.extend([
            {"id": "src:web:cars_com", "label": "Cars.com"},
            {"id": "src:web:truecar", "label": "TrueCar"},
            {"id": "src:web:ebay_motors", "label": "eBay Motors"},
            {"id": "src:web:autotrader", "label": "Autotrader"},
            {"id": "src:web:cargurus", "label": "CarGurus"},
        ])

    filter_groups = []
    if vertical == "vehicles":
        pref_meta = search_cfg.get("make_preference") or {}
        pref_filter_label = pref_meta.get("label") or "Preferred models"
        vehicle_source_opts = [
            {"id": "src:craigslist", "label": "Craigslist"},
            {"id": "src:facebook", "label": "Facebook"},
            {"id": "src:offerup", "label": "OfferUp"},
            {"id": "src:web:cars_com", "label": "Cars.com"},
            {"id": "src:web:truecar", "label": "TrueCar"},
            {"id": "src:web:ebay_motors", "label": "eBay Motors"},
            {"id": "src:web:autotrader", "label": "Autotrader"},
            {"id": "src:web:cargurus", "label": "CarGurus"},
            {"id": "src:auction:govdeals", "label": "GovDeals"},
            {"id": "src:auction:publicsurplus", "label": "Public Surplus"},
            {"id": "src:auction:propertyroom", "label": "PropertyRoom"},
        ]
        filter_groups = [
            {
                "id": "price",
                "label": "Price",
                "hint": "Check one or more bands (none checked = all prices)",
                "options": [
                    {"id": "price:under_5k", "label": "Under $5k"},
                    {"id": "price:5_10k", "label": "$5k–$10k"},
                    {"id": "price:10_15k", "label": "$10k–$15k"},
                    {"id": "price:15_20k", "label": "$15k–$20k"},
                ],
                "priority": 0,
            },
            {
                "id": "location",
                "label": "Location",
                "hint": "Colorado + Florida markets",
                "options": [
                    {"id": "loc:near_home", "label": "📍 Near Gardner / Huerfano"},
                    {"id": "loc:front_range", "label": "🏔 Front Range · Pueblo · COS"},
                    {"id": "loc:colorado", "label": "🌄 Colorado"},
                    {"id": "loc:florida", "label": "🌴 Florida"},
                ],
                "priority": 1,
            },
            {
                "id": "vehicle_specs",
                "label": "Vehicle",
                "options": [
                    {"id": "spec:hd_tow", "label": "🛻 3/4-ton+ tow rated"},
                    {"id": "spec:commercial", "label": "📦 Box / commercial / reefer"},
                    {"id": "chevy_preferred", "label": f"⭐ {pref_filter_label}"},
                    {"id": "spec:vintage_square", "label": "🛻 Vintage / square body"},
                    {"id": "spec:vintage_quality", "label": "✨ Vintage + quality"},
                    {"id": "spec:diesel", "label": "⛽ Diesel"},
                    {"id": "spec:auction", "label": "🔨 Auction / surplus / repo"},
                    {"id": "spec:grant_credit", "label": "💰 Grant / tax-credit angle"},
                    {"id": "rebuilt", "label": "🔧 Rebuilt / reman"},
                    {"id": "fleet", "label": "🏛 Fleet / municipal"},
                    {"id": "fit:top", "label": "⭐ Top fit (75+)"},
                ],
                "priority": 2,
            },
            {
                "id": "shortlist",
                "label": "Shortlist",
                "options": [
                    {"id": "saved", "label": "📌 Saved shortlist"},
                    {"id": "compare_pick", "label": "☑ Compare export"},
                ],
                "priority": 3,
            },
            {
                "id": "source",
                "label": "Listing source",
                "exclude": True,
                "hint": "Uncheck a source to hide it",
                "options": vehicle_source_opts,
                "priority": 4,
            },
        ]
        exclude_categories = []
    else:
        filter_groups = [
            {
                "id": "exclude_types",
                "label": "Exclude types",
                "exclude": True,
                "hint": "Uncheck a type to show it",
                "options": exclude_type_options,
                "priority": 0,
            },
            {
                "id": "quick",
                "label": "Quick",
                "options": [
                    {"id": "priority", "label": "⭐ Priority"},
                    {"id": "saved", "label": "📌 Saved (any trip)"},
                ],
                "priority": 1,
            },
        ]
        filter_groups.append({
            "id": "source",
            "label": "Source",
            "exclude": True,
            "hint": "Uncheck a source to hide it",
            "options": source_filter_options,
            "priority": 2,
        })

    filter_groups.sort(key=lambda g: g.get("priority", 99))

    with_photo = sum(1 for item in listings if item.get("image_url"))
    enabled_platform_ids = {
        key for key, section in platforms_cfg.items() if section.get("enabled")
    }
    recommended_platforms = [
        p for p in RECOMMENDED_PLATFORMS
        if p["id"] not in enabled_platform_ids
    ]
    show_web_ui = vertical == "vehicles" or trailer_hunt
    web_marketplaces = _load_web_marketplaces(profile) if show_web_ui else {}
    make_pref = search_cfg.get("make_preference") or {}
    source_counts = (
        _vehicle_source_counts(listings, platforms_cfg) if show_web_ui else []
    )

    payload = {
        "app": app,
        "profile_id": meta.get("id", ""),
        "profile_name": meta.get("name", ""),
        "vertical": meta.get("vertical", ""),
        "trailer_hunt": trailer_hunt,
        "compare_export_enabled": vertical == "vehicles",
        "make_preference": make_pref,
        "shop_home": profile.get("home") or {},
        "shop_rules": profile.get("shop_rules") or {},
        "web_marketplaces": web_marketplaces,
        "branding": {
            "logo_icon": logo_128,
            "logo_favicon": logo_64,
            "wordmark": wordmark,
            "wordmark_parts": wordmark_parts,
            "footer_org": footer_org,
        },
        "updated": now,
        "location": loc_label,
        "active_trip": active_trip_label,
        "pickup_contact": pickup,
        "pickup_template": template,
        "stats": {
            "new": new_count,
            "checked": total_checked,
            "showing": len(listings),
            "duplicates_removed": duplicates_removed,
        },
        "score_tiers": {
            "worth_the_drive_min": cfg["scoring"]["tiers"]["worth_the_drive"]["min_score"],
            "must_email_min": cfg["scoring"]["tiers"]["must_email_min_score"],
        },
        "image_stats": {
            "with_photo": with_photo,
            "without_photo": len(listings) - with_photo,
            "total": len(listings),
        },
        "show_all": show_all,
        "rerun_command": (
            "SKOUT_PROFILE=gardner-farm .venv/bin/python src/main.py --trailer --all --open"
            if trailer_hunt
            else ".venv/bin/python src/main.py --all --open"
        ),
        "mark_seen_command": ".venv/bin/python src/main.py --mark-seen",
        "recommended_platforms": recommended_platforms,
        "asset_paths": {
            "icons": str(ASSETS_DIR),
            "site_assets": str(SITE_DIR / "assets"),
            "project": str(SITE_DIR.parent),
        },
        "filter_groups": filter_groups,
        "routes": {
            k: {
                "name": v["name"],
                "description": v.get("description", ""),
                "tags": v.get("tags", []),
                "cities": v.get("cities", []),
            }
            for k, v in routes.items()
        },
        "listings": listings,
        "sell_listings": sell_listings,
        "sale_targets": sale_targets,
        "comp_stats": comp_stats,
        "market_tabs_enabled": market_tabs_enabled,
        "buy_hold_until": buy_hold_until,
        "public_url": public_url,
        "exclude_categories": exclude_categories,
        "exclude_title_keywords": exclude_title_keywords,
        "route_city_hints": route_city_hints,
        "default_route_dest": "",
        "quick_searches": _quick_searches_payload(search_cfg, profile),
        "priority_keywords": search_cfg.get("priority_keywords", []),
        "locations": all_locations,
        "upcoming_trips": all_locations,
        "trips": all_locations,
        "active_sources": active_sources,
        "channel_stats": channel_stats,
        "source_counts": source_counts,
        "focus_trip_id": focus_trip_id,
        "app_tabs": app_tabs,
        "ui_defaults": ui_defaults,
    }

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    assets_dest = SITE_DIR / "assets"
    assets_dest.mkdir(parents=True, exist_ok=True)
    if ASSETS_DIR.is_dir():
        for asset in ASSETS_DIR.iterdir():
            if asset.is_file() and not asset.name.startswith("."):
                shutil.copy2(asset, assets_dest / asset.name)
    (SITE_DIR / "data.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (SITE_DIR / ".nojekyll").touch()

    shop_rules = profile.get("shop_rules") or {}
    home_cfg = profile.get("home") or {}
    vehicle_sub = ""
    if vertical == "vehicles":
        max_p = int(shop_rules.get("max_price_usd") or 20000)
        vehicle_sub = (
            f'<p class="sub">Tow rig · max ${max_p:,} · '
            f'{escape(home_cfg.get("city", "Gardner"))} CO · ranked by fit</p>'
        )
    elif vertical == "estate_sales":
        vehicle_sub = '<p class="sub">Estate &amp; yard sales · matched in photos &amp; text</p>'

    app_tabs_html = ""
    for t in app_tabs:
        active_cls = " active" if t["active"] else ""
        aria = ' aria-current="page"' if t["active"] else ""
        app_tabs_html += (
            f'<a class="app-tab{active_cls}" href="{escape(t["href"])}"{aria}>'
            f'{escape(t["label"])}</a>'
        )

    sub_accent = VERTICAL_SUB_ACCENT.get(vertical, "#c9a962")
    vertical_class = re.sub(r"[^a-z0-9_-]+", "-", (vertical or "general").lower())
    vertical_theme_css = f"""
    body.vertical-{vertical_class} {{ --gg-app-sub: {sub_accent}; }}
    body.vertical-{vertical_class} .sidebar-head .sub {{ color: var(--gg-app-sub); }}
"""

    data_json = json.dumps(payload)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <title>{escape(wordmark)}</title>
  <link rel="icon" href="assets/{logo_64}" type="image/png">
  <style>
    {GGL_THEME_CSS}
    {vertical_theme_css}
    * {{ box-sizing: border-box; }}
    body {{ font-family: var(--gg-sans); margin: 0; font-size: 16px;
      background: var(--gg-ivory-warm); color: var(--gg-ink); }}
    .layout {{ display: flex; min-height: 100vh; }}
    .sidebar {{ width: 240px; min-width: 240px; background: var(--gg-paper); border-right: 1px solid var(--gg-border-soft);
      position: sticky; top: 0; align-self: flex-start; max-height: 100vh; overflow-y: auto;
      z-index: 11; }}
    .sidebar-head {{ padding: .85rem 1rem; border-bottom: 1px solid var(--gg-gold-dark);
      background: linear-gradient(165deg, var(--gg-charcoal) 0%, var(--gg-charcoal-soft) 100%); color: #fff; }}
    .brand-row {{ display: flex; gap: .65rem; align-items: center; }}
    .brand-icon {{ width: 56px; height: 56px; border-radius: 50%;
      border: 2px solid var(--gg-gold); flex-shrink: 0; object-fit: cover;
      object-position: center 20%; box-shadow: 0 2px 14px rgba(201, 169, 98, 0.28); }}
    .brand-text {{ min-width: 0; }}
    .logo-wordmark {{ margin: 0; font-family: var(--gg-serif); font-size: 1.45rem; font-weight: 400;
      letter-spacing: .03em; line-height: 1.1; display: flex; align-items: baseline; gap: .28rem; }}
    .logo-gilded, .logo-auto {{ color: #fff; font-weight: 400; }}
    .logo-accent, .logo-skout {{ color: var(--gg-gold-light); font-weight: 400; font-style: italic; }}
    .sidebar-head .sub {{ opacity: .92; font-size: .76rem; margin-top: .3rem; line-height: 1.35;
      letter-spacing: .02em; }}
    .app-tabs {{ display: flex; margin: -.85rem -1rem .7rem; border-bottom: 1px solid rgba(201,169,98,.25); }}
    .app-tab {{ flex: 1; text-align: center; padding: .55rem .2rem; font-size: .64rem; font-weight: 600;
      color: rgba(255,255,255,.72); text-decoration: none; border-bottom: 2px solid transparent;
      line-height: 1.2; letter-spacing: .04em; text-transform: uppercase; }}
    .app-tab:hover {{ color: #fff; background: rgba(201,169,98,.12); }}
    .app-tab.active {{ color: var(--gg-gold-light); border-bottom-color: var(--gg-gold); }}
    .market-tabs {{ display: flex; gap: .35rem; padding: .65rem 1rem 0; flex-wrap: wrap; }}
    .market-tabs.hidden {{ display: none; }}
    .market-tab {{ padding: .55rem .9rem; border-radius: 999px; border: 1px solid #d6d3d1;
      background: #fff; color: #44403c; font-size: .84rem; font-weight: 600; cursor: pointer; }}
    .market-tab:hover {{ border-color: var(--gg-gold); background: var(--gg-gold-bg); }}
    .market-tab.active {{ background: var(--gg-charcoal); border-color: var(--gg-gold); color: #fff; }}
    .buy-hold-banner, .sell-summary {{ margin: .55rem 1rem 0; padding: .6rem .85rem; border-radius: 10px;
      font-size: .82rem; line-height: 1.45; }}
    .buy-hold-banner {{ background: #fef3c7; border: 1px solid #fcd34d; color: #78350f; }}
    .sell-summary {{ background: #eff6ff; border: 1px solid #bfdbfe; color: #1e3a8a; }}
    .sell-summary .sell-timing {{ margin-top: .35rem; font-size: .76rem; color: #1e40af; opacity: .92; }}
    .buy-hold-banner.hidden, .sell-summary.hidden {{ display: none; }}
    .comp-badge {{ display: inline-block; font-size: .72rem; font-weight: 600; padding: .15rem .45rem;
      border-radius: 6px; margin-top: .25rem; }}
    .comp-badge.comp-above {{ background: var(--gg-gold-bg-strong); color: var(--gg-accent); }}
    .comp-badge.comp-below {{ background: #fef3c7; color: #92400e; }}
    .content-toolbar {{ display: flex; flex-wrap: wrap; gap: .5rem; align-items: center;
      padding: .65rem 1rem 0; }}
    .toolbar-btn {{ padding: .55rem .85rem; border-radius: 8px; border: 1px solid var(--gg-gold-dark);
      background: linear-gradient(180deg, var(--gg-gold) 0%, var(--gg-gold-dark) 100%);
      color: var(--gg-charcoal); font-size: .88rem; font-weight: 600; cursor: pointer;
      letter-spacing: .02em; }}
    .toolbar-btn:hover {{ background: linear-gradient(180deg, var(--gg-gold-light) 0%, var(--gg-gold) 100%); }}
    .toolbar-btn.secondary {{ background: #fff; color: #44403c; border-color: #d6d3d1; font-weight: 500; }}
    .toolbar-free-opt {{ display: inline-flex; align-items: center; gap: .35rem; font-size: .82rem;
      font-weight: 600; color: var(--gg-accent); padding: .45rem .65rem; border-radius: 8px;
      border: 1px solid var(--gg-gold-light); background: var(--gg-gold-bg); cursor: pointer; user-select: none; }}
    .toolbar-free-opt input {{ margin: 0; cursor: pointer; accent-color: var(--gg-accent); }}
    .quick-search-bar {{ padding: .65rem 1rem 0; border-bottom: 1px solid #e7e5e4; background: #fafaf9; }}
    .quick-search-bar.hidden {{ display: none; }}
    .quick-search-head {{ display: flex; flex-wrap: wrap; align-items: center; gap: .45rem .65rem;
      margin-bottom: .45rem; }}
    .quick-search-title {{ font-size: .72rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: .04em; color: #78716c; }}
    .quick-search-route {{ font-size: .72rem; color: #57534e; }}
    .quick-search-chips {{ display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; }}
    .quick-chip {{ font-size: .78rem; font-weight: 600; padding: .35rem .65rem; border-radius: 999px;
      border: 1px solid #d6d3d1; background: #fff; color: #44403c; cursor: pointer; }}
    .quick-chip:hover {{ border-color: var(--gg-accent); background: var(--gg-gold-bg); }}
    .quick-chip.active {{ background: var(--gg-charcoal); border-color: var(--gg-gold); color: var(--gg-gold-light); }}
    .quick-chip.recent {{ font-weight: 500; font-style: italic; }}
    .quick-search-sort {{ margin-left: auto; font-size: .78rem; display: inline-flex; align-items: center;
      gap: .35rem; color: #57534e; }}
    .quick-search-sort select {{ padding: .3rem .45rem; border: 1px solid #d6d3d1; border-radius: 6px;
      font-size: .78rem; background: #fff; }}
    .quick-search-add {{ display: flex; gap: .35rem; margin-top: .4rem; }}
    .quick-search-add input {{ flex: 1; min-width: 0; max-width: 220px; padding: .35rem .5rem;
      border: 1px solid #d6d3d1; border-radius: 6px; font-size: .78rem; }}
    .quick-search-add button {{ padding: .35rem .55rem; border: 1px solid #d6d3d1; border-radius: 6px;
      background: #fff; font-size: .78rem; cursor: pointer; }}
    .image-health {{ font-size: .78rem; color: #57534e; }}
    .image-health.warn {{ color: #b45309; }}
    .platform-recs {{ padding: .5rem 1rem 1rem; border-top: 1px solid #f5f5f4; }}
    .platform-recs summary {{ font-size: .82rem; font-weight: 600; cursor: pointer; color: #44403c; }}
    .platform-rec {{ padding: .45rem 0; border-bottom: 1px solid #f5f5f4; font-size: .8rem; }}
    .platform-rec:last-child {{ border-bottom: none; }}
    .platform-rec b {{ font-size: .85rem; }}
    .platform-rec span {{ display: block; color: #78716c; font-size: .75rem; margin-top: .1rem; }}
    .platform-rec .tag {{ display: inline-block; font-size: .62rem; text-transform: uppercase;
      letter-spacing: .03em; color: var(--gg-accent); background: var(--gg-gold-bg); padding: .1rem .35rem;
      border-radius: 4px; margin-left: .25rem; }}
    .upcoming-nav {{ padding: 0; border-bottom: 1px solid var(--gg-border-soft); background: var(--gg-ivory); }}
    .sidebar-acc {{ border-bottom: 1px solid var(--gg-border-soft); }}
    .sidebar-acc > summary {{ padding: .6rem 1rem; font-size: .72rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: .05em; color: var(--gg-muted); cursor: pointer;
      list-style: none; background: var(--gg-ivory); }}
    .sidebar-acc > summary::-webkit-details-marker {{ display: none; }}
    .sidebar-acc[open] > summary {{ color: var(--gg-gold-dark); border-bottom: 1px solid var(--gg-border-soft); }}
    .sidebar-acc-body {{ padding: .5rem 1rem .75rem; }}
    .upcoming-nav-title {{ display: none; }}
    .upcoming-trip-link {{ display: block; padding: .45rem .55rem; border-radius: 8px;
      text-decoration: none; color: #292524; border: 1px solid #e7e5e4; margin-bottom: .35rem;
      background: #fff; cursor: pointer; }}
    .upcoming-trip-link:hover {{ border-color: var(--gg-accent); background: var(--gg-gold-bg); }}
    .upcoming-trip-link.active {{ border-color: var(--gg-accent); background: var(--gg-gold-bg-strong); box-shadow: inset 0 0 0 1px var(--gg-accent); }}
    .ut-name {{ display: block; font-size: .78rem; font-weight: 600; line-height: 1.25; }}
    .ut-date {{ display: block; font-size: .68rem; color: #78716c; margin-top: .1rem; }}
    .ut-meta {{ display: block; font-size: .62rem; color: var(--gg-accent); margin-top: .15rem; }}
    .ut-empty {{ font-size: .72rem; color: #a8a29e; font-style: italic; }}
    .trip-save-search {{ margin-top: .35rem; width: 100%; font-size: .68rem; padding: .35rem;
      border: 1px solid #d6d3d1; border-radius: 6px; background: #fff; cursor: pointer; color: #44403c; }}
    .trip-save-search:hover {{ border-color: var(--gg-accent); background: var(--gg-gold-bg); }}
    .trip-search-tags {{ font-size: .62rem; color: #57534e; margin-top: .25rem; line-height: 1.35; }}
    .filter-panel {{ padding: .5rem 0 1rem; }}
    .filter-panel-top {{ display: flex; justify-content: space-between; align-items: center;
      padding: .5rem 1rem; }}
    .filter-panel-top h2 {{ margin: 0; font-size: .95rem; color: #44403c; }}
    .filter-panel-top button {{ font-size: .8rem; padding: .25rem .5rem; border-radius: 6px;
      border: 1px solid #d6d3d1; background: #fff; cursor: pointer; color: #57534e; }}
    details.filter-group {{ border-bottom: 1px solid #f5f5f4; }}
    details.filter-group summary {{ padding: .55rem 1rem; font-size: .88rem; font-weight: 600;
      cursor: pointer; list-style: none; color: #292524; }}
    details.filter-group summary::-webkit-details-marker {{ display: none; }}
    .filter-options {{ padding: 0 1rem .6rem; max-height: 280px; overflow-y: auto; }}
    .filter-family {{ margin-bottom: .45rem; }}
    .filter-family-name {{ font-size: .68rem; font-weight: 700; color: #78716c; text-transform: uppercase;
      letter-spacing: .03em; padding: .25rem 0 .15rem; }}
    .filter-options label {{ display: flex; align-items: flex-start; gap: .45rem; font-size: .86rem;
      padding: .3rem 0; cursor: pointer; line-height: 1.3; }}
    .filter-options input {{ margin-top: .15rem; accent-color: var(--gg-accent); flex-shrink: 0; }}
    .filter-hint {{ font-size: .68rem; color: #a8a29e; padding: 0 1rem .25rem; }}
    .filter-count {{ font-size: .7rem; color: #78716c; padding: 0 1rem .5rem; }}
    .hide-seen-opt {{ display: block; font-size: .78rem; color: #44403c; padding: 0 1rem .65rem; cursor: pointer; }}
    .hide-seen-opt input {{ margin-right: .35rem; }}
    .route-filter {{ padding: .5rem 1rem .75rem; border-bottom: 1px solid #f5f5f4; }}
    .route-filter label {{ display: block; font-size: .72rem; font-weight: 600; color: #44403c;
      margin-bottom: .35rem; }}
    .route-filter input {{ width: 100%; padding: .45rem .55rem; border: 1px solid #d6d3d1;
      border-radius: 8px; font-size: .8rem; }}
    .route-filter .hint {{ font-size: .65rem; color: #a8a29e; margin-top: .25rem; }}
    .trips-panel {{ padding: .5rem 1rem .75rem; border-bottom: 1px solid #f5f5f4; }}
    .trips-panel summary {{ font-size: .8rem; font-weight: 600; cursor: pointer; color: #292524;
      list-style: none; margin-bottom: .45rem; }}
    .trips-panel summary::-webkit-details-marker {{ display: none; }}
    .trips-panel select {{ width: 100%; padding: .45rem .55rem; border: 1px solid #d6d3d1;
      border-radius: 8px; font-size: .8rem; margin-bottom: .45rem; }}
    .trip-filter-opts {{ display: flex; flex-direction: column; gap: .25rem; margin-bottom: .5rem; }}
    .trip-filter-opts label {{ font-size: .72rem; display: flex; gap: .35rem; align-items: center; }}
    .trip-rows {{ max-height: 220px; overflow-y: auto; margin-bottom: .45rem; }}
    .trip-row {{ border: 1px solid #e7e5e4; border-radius: 8px; padding: .45rem; margin-bottom: .4rem;
      background: #fafaf9; }}
    .trip-row.active {{ border-color: var(--gg-accent); background: var(--gg-gold-bg); }}
    .trip-row input {{ width: 100%; padding: .3rem .4rem; border: 1px solid #d6d3d1; border-radius: 6px;
      font-size: .72rem; margin-top: .2rem; }}
    .trip-row-dates {{ display: grid; grid-template-columns: 1fr 1fr; gap: .35rem; }}
    .trip-row-meta {{ display: flex; justify-content: space-between; align-items: center;
      font-size: .65rem; color: #78716c; margin-top: .3rem; }}
    .trip-row-meta button {{ border: none; background: transparent; color: #b91c1c; cursor: pointer;
      font-size: .85rem; padding: 0 .2rem; }}
    .trip-add-btn {{ width: 100%; padding: .4rem; border: 1px dashed #a8a29e; border-radius: 8px;
      background: #fff; font-size: .75rem; cursor: pointer; color: #44403c; }}
    .tile.trip-saved {{ outline: 2px solid #eab308; outline-offset: -2px; }}
    .tile.trip-route-only {{ box-shadow: inset 0 0 0 2px #93c5fd; }}
    .trip-tag {{ font-size: .58rem; color: var(--gg-accent); font-weight: 600; }}
    .exclude-keywords {{ padding: 0 1rem .6rem; border-bottom: 1px solid #f5f5f4; }}
    .exclude-keywords summary {{ font-size: .72rem; font-weight: 600; cursor: pointer; color: #44403c; }}
    .exclude-keywords label {{ display: flex; gap: .4rem; font-size: .72rem; padding: .2rem 0; }}
    .exclude-kw-add {{ display: flex; gap: .35rem; margin: .45rem 0 .35rem; }}
    .exclude-kw-add input {{ flex: 1; min-width: 0; padding: .35rem .5rem; border: 1px solid #d6d3d1;
      border-radius: 6px; font-size: .75rem; }}
    .exclude-kw-add button {{ padding: .35rem .55rem; border: 1px solid #d6d3d1; border-radius: 6px;
      background: #fff; font-size: .72rem; cursor: pointer; white-space: nowrap; }}
    .exclude-kw-custom {{ display: flex; flex-wrap: wrap; gap: .3rem; margin-bottom: .35rem; }}
    .exclude-kw-chip {{ display: inline-flex; align-items: center; gap: .2rem; font-size: .68rem;
      padding: .15rem .4rem; border-radius: 999px; background: #fef2f2; color: #991b1b;
      border: 1px solid #fecaca; }}
    .exclude-kw-chip button {{ border: none; background: transparent; cursor: pointer; color: #991b1b;
      font-size: .85rem; line-height: 1; padding: 0; }}
    .source-toggle-opt {{ display: block; font-size: .78rem; color: #44403c; padding: 0 1rem .65rem; cursor: pointer; }}
    .source-toggle-opt input {{ margin-right: .35rem; }}
    .trip-filter-banner {{ margin: 0 1rem .5rem; padding: .55rem .75rem; border-radius: 8px;
      background: #eff6ff; border: 1px solid #bfdbfe; font-size: .8rem; color: #1e40af; }}
    .trip-filter-banner button {{ margin-left: .5rem; padding: .2rem .5rem; border-radius: 6px;
      border: 1px solid #93c5fd; background: #fff; cursor: pointer; font-size: .75rem; }}
    .source-toggle-opt input {{ margin-right: .35rem; }}
    .cl-warning {{ margin: .65rem 1rem 0; padding: .65rem .85rem; background: #fef3c7;
      border: 1px solid #f59e0b; border-radius: 8px; font-size: .82rem; line-height: 1.45;
      color: #78350f; }}
    .cl-warning.hidden {{ display: none; }}
    .cl-warning button {{ float: right; border: none; background: transparent; cursor: pointer;
      font-size: 1rem; color: #92400e; margin: -.2rem -.2rem 0 .5rem; }}
    .content {{ flex: 1; min-width: 0; padding-bottom: 5rem; }}
    .mobile-filter-btn {{ display: none; }}
    .tile.priority {{ box-shadow: none; }}
    .tile.paid {{ box-shadow: none; }}
    .tile.saved-pin {{ outline: 2px solid #eab308; outline-offset: -2px; }}
    .modal {{ position: fixed; inset: 0; z-index: 100; display: flex; align-items: flex-start;
      justify-content: center; padding: 1rem 1rem 2rem; overflow-y: auto; }}
    .modal.hidden {{ display: none; }}
    .modal-backdrop {{ position: fixed; inset: 0; background: rgba(0,0,0,.55); }}
    .modal-panel {{ position: relative; background: var(--gg-paper); width: 100%; max-width: 760px;
      max-height: none; overflow: hidden; border-radius: 16px; padding: 0 0 1.5rem;
      z-index: 1; margin-top: 0; box-shadow: 0 16px 48px rgba(0,0,0,.28);
      border: 1px solid var(--gg-border-soft); }}
    .modal-header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: .75rem;
      padding: 1rem 1rem .75rem; background: linear-gradient(165deg, var(--gg-charcoal) 0%, var(--gg-charcoal-soft) 100%);
      border-bottom: 1px solid var(--gg-gold-dark); color: #fff; }}
    .modal-header h2 {{ margin: 0; font-family: var(--gg-serif); font-size: 1.2rem; font-weight: 400;
      line-height: 1.35; color: #fff; flex: 1; }}
    .modal-body {{ padding: 0 1rem; }}
    .modal-panel img {{ width: 100%; max-height: 50vh; object-fit: contain; background: var(--gg-ivory);
      border-radius: 0; margin-bottom: 0; display: block; }}
    .modal-gallery {{ position: relative; margin-bottom: .75rem; touch-action: pan-y; }}
    .modal-gallery.hidden {{ display: none; }}
    .gallery-nav {{ position: absolute; top: 50%; transform: translateY(-50%); z-index: 2;
      width: 2.25rem; height: 2.25rem; border-radius: 999px; border: none; background: rgba(255,255,255,.92);
      box-shadow: 0 1px 6px rgba(0,0,0,.18); font-size: 1.35rem; line-height: 1; cursor: pointer; color: #292524; }}
    .gallery-nav:disabled {{ opacity: .35; cursor: default; }}
    .gallery-prev {{ left: .5rem; }}
    .gallery-next {{ right: .5rem; }}
    .gallery-count {{ position: absolute; bottom: .5rem; right: .6rem; font-size: .72rem;
      background: rgba(0,0,0,.55); color: #fff; padding: .15rem .45rem; border-radius: 6px; }}
    .gallery-dots {{ display: flex; justify-content: center; gap: .35rem; margin-top: .45rem; }}
    .gallery-dot {{ width: 7px; height: 7px; border-radius: 999px; border: none; padding: 0;
      background: #d6d3d1; cursor: pointer; }}
    .gallery-dot.active {{ background: var(--gg-accent); }}
    .channel-bar-wrap {{ padding: 0; }}
    .channel-bar-details > summary {{ padding: .55rem 1rem; font-size: .72rem; font-weight: 700;
      text-transform: uppercase; letter-spacing: .04em; color: var(--gg-muted); cursor: pointer;
      list-style: none; border-bottom: 1px solid var(--gg-border-soft); }}
    .channel-bar-details > summary::-webkit-details-marker {{ display: none; }}
    .channel-bar-details[open] > summary {{ color: var(--gg-gold-dark); }}
    .channel-bar-details .channel-bar {{ padding: .5rem 1rem .65rem; }}
    .channel-bar-title {{ display: none; }}
    .active-filter-chips {{ display: flex; flex-wrap: wrap; gap: .35rem; padding: .5rem 1rem 0;
      align-items: center; }}
    .active-filter-chips.hidden {{ display: none; }}
    .active-filter-chip {{ font-size: .72rem; font-weight: 600; padding: .28rem .55rem; border-radius: 999px;
      border: 1px solid var(--gg-gold); background: var(--gg-paper); color: var(--gg-charcoal);
      cursor: pointer; letter-spacing: .02em; }}
    .active-filter-chip:hover {{ background: var(--gg-gold-bg); }}
    .active-filter-chip span {{ color: var(--gg-gold-dark); margin-left: .15rem; }}
    .empty-state {{ text-align: center; padding: 3rem 1.5rem 3.5rem; max-width: 22rem; margin: 0 auto; }}
    .empty-mark {{ width: 3.5rem; height: 3.5rem; margin: 0 auto 1rem; color: var(--gg-gold);
      opacity: .85; }}
    .empty-title {{ font-family: var(--gg-serif); font-size: 1.15rem; color: var(--gg-ink); margin: 0 0 .35rem; }}
    .empty-hint {{ font-size: .85rem; color: var(--gg-gold-dark); line-height: 1.5; margin: 0; }}
    .empty-hint button {{ border: none; background: none; color: var(--gg-gold-dark); text-decoration: underline;
      cursor: pointer; font: inherit; padding: 0; }}
    .empty {{ text-align: center; padding: 3rem 1rem; color: var(--gg-muted); }}
    .channel-bar {{ display: flex; flex-wrap: wrap; gap: .35rem; }}
    .channel-pill {{ font-size: .72rem; padding: .25rem .55rem; border-radius: 999px;
      border: 1px solid #e7e5e4; background: #fafaf9; color: #44403c; }}
    .channel-pill.ok {{ border-color: var(--gg-gold-light); background: var(--gg-gold-bg); }}
    .channel-pill.setup {{ border-color: #fde68a; background: #fffbeb; color: #92400e; }}
    .channel-pill.filtered {{ border-color: #fed7aa; background: #fff7ed; color: #9a3412; }}
    .channel-pill .ch-status {{ font-weight: 700; margin-right: .15rem; }}
    .worth-badge {{ display: inline-block; font-size: .68rem; font-weight: 600;
      padding: .15rem .45rem; border-radius: 6px; margin-top: .25rem; }}
    .worth-high {{ background: var(--gg-gold-bg-strong); color: var(--gg-accent); }}
    .worth-medium {{ background: #fef9c3; color: #854d0e; }}
    .worth-low {{ background: #f5f5f4; color: #78716c; }}
    .fit-badge {{ display: inline-block; font-size: .68rem; font-weight: 700;
      padding: .15rem .45rem; border-radius: 6px; margin-top: .2rem; }}
    .fit-top {{ background: var(--gg-gold-bg-strong); color: var(--gg-accent); }}
    .fit-good {{ background: #dbeafe; color: #1d4ed8; }}
    .fit-maybe {{ background: #fef9c3; color: #854d0e; }}
    .fit-weak {{ background: #f5f5f4; color: #78716c; }}
    .vehicle-loc-filter {{ padding: .5rem 1rem .75rem; border-bottom: 1px solid #f5f5f4; }}
    .vehicle-loc-filter label {{ display: block; font-size: .72rem; font-weight: 600; color: #44403c;
      margin-bottom: .25rem; }}
    .vehicle-loc-filter input {{ width: 100%; padding: .45rem .55rem; border: 1px solid #d6d3d1;
      border-radius: 8px; font-size: .82rem; }}
    .vehicle-loc-filter .hint {{ font-size: .65rem; color: #a8a29e; margin-top: .25rem; }}
    .also-on {{ font-size: .68rem; color: #78716c; margin-top: .15rem; }}
    .modal-close {{ position: static; flex-shrink: 0; font-size: 1.35rem; line-height: 1;
      border: 1px solid var(--gg-gold); background: transparent; cursor: pointer;
      color: var(--gg-gold-light); width: 2rem; height: 2rem; border-radius: 8px; }}
    .modal-close:hover {{ background: rgba(201,169,98,.15); }}
    .modal-panel .modal-meta {{ font-size: .88rem; color: var(--gg-muted); margin: .75rem 0; padding: 0 1rem; }}
    .modal-desc {{ font-family: var(--gg-serif); font-size: 1.05rem; line-height: 1.6;
      white-space: pre-wrap; margin: 0 0 1rem; padding: 0 1rem; color: var(--gg-ink); }}
    .modal-desc.estate-desc {{ font-size: 1.08rem; }}
    .modal-actions {{ display: flex; gap: .5rem; flex-wrap: wrap; padding: 0 1rem; }}
    .modal-actions button, .modal-actions a {{ padding: .5rem .85rem; border-radius: 8px;
      border: 1px solid #d6d3d1; background: #fff; font-size: .85rem; cursor: pointer;
      text-decoration: none; color: #1c1917; }}
    .modal-actions button.primary {{ background: var(--gg-charcoal); color: var(--gg-gold-light);
      border-color: var(--gg-gold); }}
    .modal-actions button.email-btn {{ background: #1d4ed8; color: #fff; border-color: #1d4ed8; }}
    .modal-actions button.copy-btn {{ background: #fafaf9; }}
    .tile-email {{ position: absolute; bottom: .35rem; right: .35rem; z-index: 2;
      background: rgba(255,255,255,.92); border: 1px solid #d6d3d1; border-radius: 6px;
      padding: .2rem .45rem; font-size: .75rem; cursor: pointer; }}
    .toast {{ position: fixed; bottom: 1rem; left: 50%; transform: translateX(-50%);
      background: #1c1917; color: #fff; padding: .5rem 1rem; border-radius: 8px;
      font-size: .85rem; z-index: 100; opacity: 0; transition: opacity .2s; pointer-events: none; }}
    .toast.show {{ opacity: 1; }}
    .stats {{ display: flex; gap: .5rem; padding: .75rem 1rem; flex-wrap: wrap; }}
    .stat {{ background: #fff; border-radius: 8px; padding: .5rem .75rem; flex: 1;
      min-width: 80px; text-align: center; box-shadow: 0 1px 2px rgba(0,0,0,.06); }}
    .stat b {{ display: block; font-size: 1.5rem; color: var(--gg-accent); }}
    .stat span {{ font-size: .78rem; color: #78716c; }}
    .source-counts {{ display: flex; gap: .4rem; padding: .65rem 1rem 0; flex-wrap: wrap;
      border-bottom: 1px solid #e7e5e4; background: #fff; }}
    .source-counts-title {{ width: 100%; font-size: .72rem; font-weight: 700; color: #44403c;
      text-transform: uppercase; letter-spacing: .04em; margin-bottom: .15rem; }}
    .source-count {{ background: #fff; border-radius: 8px; padding: .4rem .65rem; min-width: 72px;
      text-align: center; box-shadow: 0 1px 2px rgba(0,0,0,.06); border: 1px solid #e7e5e4; }}
    .source-count b {{ display: block; font-size: 1.35rem; color: var(--gg-accent); line-height: 1.1; }}
    .source-count.zero b {{ color: #a8a29e; }}
    .source-count span {{ font-size: .68rem; color: #57534e; line-height: 1.2; }}
    #list {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: .75rem; padding: 0 1rem 1.25rem; }}
    @media (min-width: 900px) {{
      #list {{ grid-template-columns: repeat(auto-fill, minmax(155px, 1fr)); gap: .85rem; }}
    }}
    @media (min-width: 1200px) {{
      #list {{ grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); }}
    }}
    .tile {{ background: var(--gg-paper); border-radius: 12px; overflow: hidden;
      border: 1px solid var(--gg-border-soft); display: flex; flex-direction: column;
      position: relative; transition: opacity .15s ease, border-color .15s ease; box-shadow: none; }}
    .tile:hover {{ border-color: var(--gg-gold-light); }}
    .tile.priority {{ border-top: 2px solid var(--gg-gold); box-shadow: none; }}
    .tile.paid {{ border-color: var(--gg-gold-dark); box-shadow: none; }}
    .tile.post-seen {{ opacity: 0.4; }}
    .tile.post-seen:hover {{ opacity: 1; }}
    .tile-media-wrap {{ position: relative; }}
    .tile-media {{ position: relative; aspect-ratio: 1; background: var(--gg-ivory);
      display: flex; align-items: center; justify-content: center; overflow: hidden;
      cursor: pointer; border: none; padding: 0; width: 100%; font: inherit; }}
    .tile-media.loading::before {{ content: ''; position: absolute; inset: 0;
      background: linear-gradient(110deg, var(--gg-ivory) 8%, var(--gg-gold-bg-strong) 18%, var(--gg-ivory) 33%);
      background-size: 200% 100%; animation: gg-shimmer 1.2s ease-in-out infinite; z-index: 0; }}
    @keyframes gg-shimmer {{ 0% {{ background-position: 100% 0; }} 100% {{ background-position: -100% 0; }} }}
    .tile-media img {{ width: 100%; height: 100%; object-fit: cover; display: block;
      transition: opacity .2s ease; position: relative; z-index: 1; opacity: 0; }}
    .tile-media img.loaded {{ opacity: 1; }}
    .tile-media.cycling img.loaded {{ opacity: .92; }}
    .tile-media.loaded::before {{ display: none; }}
    .tile-media .placeholder {{ font-size: 1.5rem; opacity: .35; color: var(--gg-gold-dark);
      font-family: var(--gg-serif); z-index: 1; }}
    .tile-new {{ position: absolute; top: .35rem; left: .35rem; z-index: 3; font-size: .58rem;
      font-weight: 700; letter-spacing: .06em; text-transform: uppercase; padding: .15rem .4rem;
      border-radius: 4px; background: var(--gg-charcoal); color: var(--gg-gold-light);
      border: 1px solid var(--gg-gold); }}
    .tile-dates {{ margin: 0; font-size: .68rem; color: var(--gg-gold-dark); font-weight: 600;
      line-height: 1.25; letter-spacing: .01em; }}
    .tile-badges {{ position: absolute; top: .3rem; left: .3rem; display: flex; gap: .2rem; }}
    .tile-badge {{ font-size: .85rem; background: rgba(255,255,255,.92); border-radius: 6px;
      padding: .1rem .25rem; line-height: 1; box-shadow: 0 1px 2px rgba(0,0,0,.12); }}
    .tile-save {{ position: absolute; top: .3rem; right: .3rem; width: 1.6rem; height: 1.6rem;
      border-radius: 50%; border: none; background: rgba(255,255,255,.92); cursor: pointer;
      font-size: .75rem; box-shadow: 0 1px 2px rgba(0,0,0,.15); }}
    .tile-save.saved {{ background: var(--gg-charcoal); color: var(--gg-gold-light); border: 1px solid var(--gg-gold); }}
    .tile-seen {{ position: absolute; bottom: .35rem; left: .35rem; width: 1.6rem; height: 1.6rem;
      border-radius: 50%; border: none; background: rgba(255,255,255,.92); cursor: pointer;
      font-size: .72rem; box-shadow: 0 1px 2px rgba(0,0,0,.15); color: #78716c; z-index: 2; }}
    .tile-seen.seen {{ background: #44403c; color: #fff; }}
    .tile-compare {{ position: absolute; bottom: .35rem; left: 2.1rem; z-index: 2;
      display: flex; align-items: center; gap: .2rem; font-size: .62rem; font-weight: 600;
      background: rgba(255,255,255,.94); border-radius: 6px; padding: .15rem .35rem;
      border: 1px solid #d6d3d1; cursor: pointer; color: #44403c; }}
    .tile-compare input {{ margin: 0; cursor: pointer; }}
    .tile-exclude {{ position: absolute; bottom: .35rem; left: .35rem; z-index: 2;
      display: flex; align-items: center; gap: .15rem; font-size: .58rem; font-weight: 600;
      background: rgba(255,255,255,.94); border-radius: 6px; padding: .12rem .3rem;
      border: 1px solid #d6d3d1; cursor: pointer; color: #991b1b; }}
    .tile-exclude input {{ margin: 0; cursor: pointer; accent-color: #991b1b; }}
    .tile.perm-excluded {{ display: none; }}
    .modal-exclude {{ display: flex; align-items: center; gap: .4rem; margin: .5rem 0;
      font-size: .85rem; font-weight: 600; color: #991b1b; }}
    .modal-exclude input {{ margin: 0; cursor: pointer; accent-color: #991b1b; }}
    .tile-pref-star {{ background: #fef9c3 !important; color: #a16207; font-weight: 700; }}
    .tile.compare-on {{ box-shadow: inset 0 0 0 2px #7c3aed; }}
    .compare-bar {{ display: flex; flex-wrap: wrap; gap: .4rem; align-items: center;
      padding: .45rem 1rem 0; font-size: .78rem; }}
    .compare-bar.hidden {{ display: none; }}
    .web-market-bar {{ padding: .65rem 1rem 0; border-bottom: 1px solid #e7e5e4; background: #fffef8; }}
    .web-market-bar-title {{ font-size: .72rem; font-weight: 700; color: #44403c;
      text-transform: uppercase; letter-spacing: .04em; margin-bottom: .4rem; }}
    .web-market-chips {{ display: flex; flex-wrap: wrap; gap: .35rem; }}
    .web-market-chip {{ font-size: .72rem; padding: .3rem .55rem; border-radius: 999px;
      border: 1px solid #d6d3d1; background: #fff; color: #1d4ed8; text-decoration: none; }}
    .web-market-chip:hover {{ border-color: #1d4ed8; background: #eff6ff; }}
    .web-market-link small {{ color: #78716c; font-weight: 400; }}
    .web-market-searches {{ margin-top: .5rem; }}
    .modal-compare {{ display: flex; align-items: center; gap: .4rem; margin: .5rem 0;
      font-size: .85rem; font-weight: 600; }}
    .tile-body {{ padding: .55rem .55rem .65rem; flex: 1; display: flex; flex-direction: column; gap: .28rem; }}
    .tile-title {{ margin: 0; font-size: .88rem; font-weight: 600; line-height: 1.3;
      display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
    .tile-title a {{ color: #1c1917; text-decoration: none; }}
    .tile-meta {{ font-size: .72rem; color: #78716c; line-height: 1.3;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
    .tile-price {{ font-size: .76rem; font-weight: 600; color: var(--gg-gold-dark); }}
    .tile-price.paid-price {{ color: var(--gg-charcoal); }}
    @media (max-width: 768px) {{
      .layout {{ flex-direction: column; }}
      .sidebar {{ width: 100%; min-width: 0; position: relative; max-height: none;
        border-right: none; border-bottom: 1px solid #e7e5e4; }}
      .mobile-filter-btn {{ display: inline-block; margin: .5rem 1rem 0; padding: .45rem .75rem;
        border: 1px solid #d6d3d1; border-radius: 8px; background: #fff; font-size: .8rem; }}
      .sidebar.collapsed .filter-panel,
      .sidebar.collapsed .sidebar-acc {{ display: none; }}
    }}
    footer {{ text-align: center; font-size: .72rem; color: var(--gg-muted); padding: 1.5rem 1rem 2rem;
      letter-spacing: .02em; }}
    footer .gg-org {{ display: block; margin-top: .35rem; font-size: .68rem; color: var(--gg-gold-dark);
      font-family: var(--gg-serif); letter-spacing: .06em; text-transform: uppercase; }}
  </style>
</head>
<body class="vertical-{vertical_class}">
  <div class="layout">
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-head">
        <nav class="app-tabs" aria-label="Skout apps">{app_tabs_html}</nav>
        <div class="brand-row">
          <img src="assets/{logo_128}" alt="" class="brand-icon" width="56" height="56">
            <div class="brand-text">
            <div class="logo-wordmark" aria-label="{escape(wordmark)}">
              {''.join(f'<span class="{p["class"]}">{escape(p["text"])}</span>' for p in wordmark_parts if p.get("text"))}
            </div>
            {vehicle_sub}
          </div>
        </div>
      </div>
      <details class="sidebar-acc upcoming-nav" id="upcoming-nav">
        <summary>Upcoming trips</summary>
        <div class="sidebar-acc-body">
          <div id="upcoming-trip-links"></div>
        </div>
      </details>
      <button type="button" class="mobile-filter-btn" id="toggle-filters">Filters</button>
      <div class="filter-panel">
        <div class="filter-panel-top">
          <h2>Filters</h2>
          <button type="button" id="clear-filters">Clear</button>
        </div>
        <div class="filter-count" id="filter-count"></div>
        <label class="hide-seen-opt" id="hide-seen-wrap"><input type="checkbox" id="hide-seen" checked> Hide as seen</label>
        <label class="hide-seen-opt" id="free-only-wrap"><input type="checkbox" id="free-only-sidebar"> Free only</label>
        <label class="hide-seen-opt" id="show-icons-wrap"><input type="checkbox" id="show-icons" checked> Show emoji icons</label>
        <details class="trips-panel sidebar-acc">
          <summary>My trips</summary>
          <div class="sidebar-acc-body">
          <label for="trip-filter-select" style="font-size:.72rem;font-weight:600;color:#44403c">Filter feed by trip</label>
          <select id="trip-filter-select">
            <option value="">All listings</option>
          </select>
          <div class="trip-filter-opts">
            <label><input type="checkbox" id="trip-show-saved"> Only saved for this trip</label>
            <label><input type="checkbox" id="trip-show-route"> Only along route</label>
          </div>
          <button type="button" class="trip-save-search" id="save-search-trip" style="display:none">
            Save current filters for selected trip
          </button>
          <div class="trip-rows" id="trips-list"></div>
          <button type="button" class="trip-add-btn" id="trip-add">+ Add trip</button>
          </div>
        </details>
        <details class="sidebar-acc route-acc" id="route-filter-box-wrap">
          <summary>Route filter</summary>
          <div class="sidebar-acc-body route-filter" id="route-filter-box">
          <label for="route-dest">Route — show listings on the way to</label>
          <input type="text" id="route-dest" list="route-city-list" placeholder="e.g. Pueblo, Colorado Springs">
          <datalist id="route-city-list"></datalist>
          <div class="hint">Optional — e.g. Pueblo or Colorado Springs. Leave blank to show everywhere.</div>
          </div>
        </details>
        <details class="sidebar-acc vehicle-loc-acc" id="vehicle-loc-filter-wrap" hidden>
          <summary>Location filter</summary>
          <div class="sidebar-acc-body vehicle-loc-filter" id="vehicle-loc-filter">
          <label for="vehicle-loc-query">Location contains</label>
          <input type="text" id="vehicle-loc-query" placeholder="e.g. Pueblo, Walsenburg, 81040">
          <div class="hint">Filter by city or ZIP in the listing location.</div>
          </div>
        </details>
        <details class="exclude-keywords sidebar-acc">
          <summary>Hide posts with keywords</summary>
          <div class="sidebar-acc-body">
          <div class="exclude-kw-add">
            <input type="text" id="exclude-kw-input" placeholder="Type word to hide…" autocomplete="off">
            <button type="button" id="exclude-kw-add">Add</button>
          </div>
          <div class="exclude-kw-custom" id="exclude-kw-custom"></div>
          <div id="exclude-keywords"></div>
          </div>
        </details>
        <div id="filter-groups"></div>
        <details class="platform-recs sidebar-acc">
          <summary>Add more sources</summary>
          <div class="sidebar-acc-body">
          <div id="platform-recs"></div>
          </div>
        </details>
        <details class="web-market-panel sidebar-acc" id="web-market-panel">
          <summary>More sites — saved searches &amp; forums</summary>
          <div class="sidebar-acc-body">
          <div class="web-market-searches" id="web-market-searches"></div>
          <div id="web-market-sites"></div>
          </div>
        </details>
      </div>
    </aside>
    <main class="content">
      <div class="market-tabs hidden" id="market-tabs" role="tablist" aria-label="Buy or sell tracking">
        <button type="button" class="market-tab active" data-mode="buy" role="tab" aria-selected="true">🛻 Buy — tow truck</button>
        <button type="button" class="market-tab" data-mode="sell" role="tab" aria-selected="false">🏷 Sell — Avion comps</button>
      </div>
      <div id="buy-hold-banner" class="buy-hold-banner hidden" role="status"></div>
      <div id="sell-summary" class="sell-summary hidden" role="status"></div>
      <div class="quick-search-bar hidden" id="quick-search-bar" role="search">
        <div class="quick-search-head">
          <span class="quick-search-title">Quick search</span>
          <span class="quick-search-route" id="quick-search-route"></span>
          <label class="quick-search-sort">
            Sort
            <select id="list-sort">
              <option value="score">Best match</option>
              <option value="new">New first</option>
              <option value="title">Title A–Z</option>
            </select>
          </label>
        </div>
        <div class="quick-search-chips" id="quick-search-chips"></div>
        <div class="quick-search-add">
          <input type="text" id="quick-search-input" placeholder="Custom keyword…" autocomplete="off">
          <button type="button" id="quick-search-go">Search</button>
        </div>
      </div>
      <div class="content-toolbar">
        <label class="toolbar-free-opt" id="hide-seen-wrap-toolbar">
          <input type="checkbox" id="hide-seen-toolbar" checked> Hide as seen
        </label>
        <label class="toolbar-free-opt" id="free-only-wrap-toolbar">
          <input type="checkbox" id="free-only"> Free only
        </label>
        <button type="button" class="toolbar-btn secondary" id="mark-all-seen">Mark all as seen</button>
        <button type="button" class="toolbar-btn secondary" id="reset-seen">Reset seen marks</button>
        <button type="button" class="toolbar-btn" id="rerun-all">Rerun search (include seen)</button>
        <button type="button" class="toolbar-btn" id="export-compare" hidden>Export compare (Excel)</button>
        <button type="button" class="toolbar-btn secondary" id="clear-compare" hidden>Clear compare</button>
        <span class="image-health" id="image-health"></span>
      </div>
      <div class="compare-bar hidden" id="compare-bar">
        <span id="compare-count">0 selected for compare</span>
      </div>
      <div id="cl-warning" class="cl-warning hidden" role="status">
        <button type="button" id="cl-warning-dismiss" aria-label="Dismiss">×</button>
        Craigslist may block your browser after a big scan. Use <b>Copy seller email</b> to grab the relay address and your pickup message
        instead of opening listings. If you see a block page, wait a few hours or switch networks, then rerun Skout.
      </div>
      <div id="trip-filter-banner" class="trip-filter-banner hidden" role="status"></div>
      <div id="web-market-bar" class="web-market-bar hidden"></div>
      <div id="source-counts" class="source-counts hidden"></div>
      <details class="channel-bar-details" id="channel-bar-details">
        <summary>Source health</summary>
        <div class="channel-bar" id="channel-bar"></div>
      </details>
      <div class="stats" id="stats"></div>
      <div id="active-filter-chips" class="active-filter-chips hidden" aria-label="Active filters"></div>
      <div id="list"></div>
  <div id="modal" class="modal hidden" aria-hidden="true">
    <div class="modal-backdrop" id="modal-backdrop"></div>
    <div class="modal-panel">
      <div class="modal-header">
        <h2 id="modal-title"></h2>
        <button type="button" class="modal-close" id="modal-close" aria-label="Close">×</button>
      </div>
      <div class="modal-gallery" id="modal-gallery">
        <button type="button" class="gallery-nav gallery-prev" id="gallery-prev" aria-label="Previous photo">‹</button>
        <img id="modal-img" alt="">
        <button type="button" class="gallery-nav gallery-next" id="gallery-next" aria-label="Next photo">›</button>
        <span class="gallery-count" id="gallery-count"></span>
        <div class="gallery-dots" id="gallery-dots"></div>
      </div>
      <div class="modal-body">
      <p class="modal-meta" id="modal-meta"></p>
      <p class="modal-desc" id="modal-desc"></p>
      <label class="modal-compare" id="modal-compare-wrap" hidden>
        <input type="checkbox" id="modal-compare"> Include in compare export
      </label>
      <label class="modal-exclude" id="modal-exclude-wrap">
        <input type="checkbox" id="modal-exclude"> Always hide this listing
      </label>
      <div class="modal-actions">
        <button type="button" class="email-btn" id="modal-email">Copy seller email</button>
        <button type="button" class="copy-btn" id="modal-copy">Copy message</button>
        <button type="button" class="primary" id="modal-save">Save for trip</button>
        <a id="modal-link" target="_blank" rel="noopener">Open listing</a>
      </div>
      </div>
    </div>
  </div>
  <div id="toast" class="toast" role="status"></div>
      <footer id="footer"></footer>
    </main>
  </div>
  <script id="skout-data" type="application/json">{data_json}</script>
  <script>
    const DATA = JSON.parse(document.getElementById('skout-data').textContent);
    const includeFilters = new Set();
    const defaultHiddenCats = () => new Set(
      DATA.vertical === 'vehicles'
        ? []
        : (DATA.exclude_categories || []).map(c => c.id).filter(id => id !== 'other')
    );
    const hiddenCats = defaultHiddenCats();
    const hiddenSources = new Set();
    const saveKey = 'skout_saved_' + (DATA.profile_id || 'default');
    const tripsKey = 'skout_trips_' + (DATA.profile_id || 'default');
    const tripSearchesKey = 'skout_trip_searches_' + (DATA.profile_id || 'default');
    const filterStateKey = 'skout_filters_v2_' + (DATA.profile_id || 'default');
    const manualSeenKey = 'skout_manual_seen_' + (DATA.profile_id || 'default');
    const permExcludeKey = 'skout_perm_exclude_' + (DATA.profile_id || 'default');
    const compareKey = 'skout_compare_' + (DATA.profile_id || 'default');
    const marketModeKey = 'skout_market_mode_' + (DATA.profile_id || 'default');
    const quickSearchRecentKey = 'skout_quick_recent_' + (DATA.profile_id || 'default');
    const uiPrefsKey = 'skout_ui_' + (DATA.profile_id || 'default');
    let marketMode = 'buy';
    let showIcons = true;
    const hiddenKeywords = new Set((DATA.exclude_title_keywords || []).map(k => k.toLowerCase()));
    let routeDest = '';
    let selectedTripId = '';
    let tripShowSaved = false;
    let tripShowRoute = false;
    let hideSeen = true;
    let freeOnly = false;
    let activeQuickSearchId = '';
    let searchKeywords = [];
    let listSort = (DATA.quick_searches || {{}}).sort_default || 'score';
    let vehicleLocQuery = '';
    let modalItem = null;
    let modalBrowseList = [];
    let modalBrowseIndex = -1;
    let galleryUrls = [];
    let galleryIdx = 0;

    const EMPTY_MARK_SVG = '<svg class="empty-mark" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="28" fill="none" stroke="currentColor" stroke-width="1.5"/><path fill="currentColor" d="M20 38c4-14 18-22 32-18 6 2 10 7 10 12 0 9-8 17-19 19-7 2-14 0-18-3-2 1-4 3-7 3-3 0-5-2-5-4 0-2 3-4 7-6z"/></svg>';

    function loadUiPrefs() {{
      try {{
        const saved = JSON.parse(localStorage.getItem(uiPrefsKey) || '{{}}');
        if (typeof saved.showIcons === 'boolean') showIcons = saved.showIcons;
      }} catch (e) {{}}
    }}

    function saveUiPrefs() {{
      localStorage.setItem(uiPrefsKey, JSON.stringify({{ showIcons }}));
    }}

    function syncShowIconsUi() {{
      const box = document.getElementById('show-icons');
      if (box) box.checked = showIcons;
    }}

    function chipLabel(icon, text) {{
      return showIcons && icon ? (icon + ' ' + text) : text;
    }}

    function formatSaleDates(raw) {{
      if (!raw) return '';
      const s = String(raw).replace(/\\s+/g, ' ').trim();
      return s.length > 72 ? s.slice(0, 69) + '…' : s;
    }}

    function applyUiDefaults() {{
      const d = DATA.ui_defaults || {{}};
      if (typeof d.free_only === 'boolean') freeOnly = d.free_only;
      if (Array.isArray(d.source_only) && d.source_only.length) {{
        const srcOpts = [];
        (DATA.filter_groups || []).forEach(g => {{
          (g.options || []).forEach(o => {{
            if ((o.id || '').startsWith('src:')) srcOpts.push(o.id);
          }});
        }});
        srcOpts.forEach(id => {{
          if (!d.source_only.includes(id)) hiddenSources.add(id);
        }});
      }}
    }}

    function loadFilterState() {{
      try {{
        const raw = localStorage.getItem(filterStateKey);
        if (!raw) {{
          applyUiDefaults();
          return;
        }}
        const saved = JSON.parse(raw);
        (saved.include || []).forEach(id => includeFilters.add(id));
        (saved.hidden || []).forEach(id => hiddenCats.add(id));
        (saved.hiddenSources || []).forEach(id => hiddenSources.add(id));
        if (saved.showOfferUp === false) hiddenSources.add('src:offerup');
        (saved.hiddenKeywords || []).forEach(k => hiddenKeywords.add(k));
        routeDest = (typeof saved.routeDest === 'string') ? saved.routeDest : '';
        selectedTripId = saved.selectedTripId || '';
        tripShowSaved = saved.tripShowSaved === true;
        tripShowRoute = saved.tripShowRoute === true;
        hideSeen = saved.hideSeen !== false;
        freeOnly = !!saved.freeOnly;
        activeQuickSearchId = saved.activeQuickSearchId || '';
        searchKeywords = (saved.searchKeywords || []).map(k => String(k).toLowerCase());
        listSort = saved.listSort || (DATA.quick_searches || {{}}).sort_default || 'score';
        vehicleLocQuery = saved.vehicleLocQuery || '';
      }} catch (e) {{
        applyUiDefaults();
      }}
    }}

    function saveFilterState() {{
      localStorage.setItem(filterStateKey, JSON.stringify({{
        include: [...includeFilters],
        hidden: [...hiddenCats],
        hiddenSources: [...hiddenSources],
        hiddenKeywords: [...hiddenKeywords],
        routeDest,
        selectedTripId,
        tripShowSaved,
        tripShowRoute,
        hideSeen,
        freeOnly,
        activeQuickSearchId,
        searchKeywords: [...searchKeywords],
        listSort,
        vehicleLocQuery,
      }}));
    }}
    loadFilterState();
    loadUiPrefs();
    syncShowIconsUi();

    const showIconsBox = document.getElementById('show-icons');
    if (showIconsBox) {{
      showIconsBox.onchange = () => {{
        showIcons = !!showIconsBox.checked;
        saveUiPrefs();
        render();
      }};
    }}

    if (window.matchMedia('(max-width: 768px)').matches) {{
      const sb = document.getElementById('sidebar');
      if (sb) sb.classList.add('collapsed');
    }}

    function syncHideSeenUi() {{
      const toolbar = document.getElementById('hide-seen-toolbar');
      const sidebar = document.getElementById('hide-seen');
      if (toolbar) toolbar.checked = hideSeen;
      if (sidebar) sidebar.checked = hideSeen;
    }}

    function setHideSeen(on) {{
      hideSeen = !!on;
      syncHideSeenUi();
      saveFilterState();
      render();
    }}

    function syncFreeOnlyUi() {{
      const toolbar = document.getElementById('free-only');
      const sidebar = document.getElementById('free-only-sidebar');
      if (toolbar) toolbar.checked = freeOnly;
      if (sidebar) sidebar.checked = freeOnly;
    }}

    function setFreeOnly(on) {{
      freeOnly = !!on;
      syncFreeOnlyUi();
      saveFilterState();
      render();
    }}

    function activeFeedList() {{
      if (marketMode === 'sell' && DATA.market_tabs_enabled) {{
        return DATA.sell_listings || [];
      }}
      return DATA.listings || [];
    }}

    function allTrackedItems() {{
      return [...(DATA.listings || []), ...(DATA.sell_listings || [])];
    }}

    function loadMarketMode() {{
      if (!DATA.market_tabs_enabled) return;
      try {{
        const saved = localStorage.getItem(marketModeKey);
        if (saved === 'buy' || saved === 'sell') marketMode = saved;
      }} catch (e) {{}}
    }}

    function saveMarketMode() {{
      if (!DATA.market_tabs_enabled) return;
      localStorage.setItem(marketModeKey, marketMode);
    }}

    function setMarketMode(mode) {{
      if (!DATA.market_tabs_enabled || mode === marketMode) return;
      marketMode = mode;
      saveMarketMode();
      render({{ preserveScroll: true }});
    }}

    function formatUsd(n) {{
      if (n == null || n === '') return '—';
      return '$' + Number(n).toLocaleString('en-US');
    }}

    function renderMarketChrome() {{
      const tabsEl = document.getElementById('market-tabs');
      const buyBanner = document.getElementById('buy-hold-banner');
      const sellSummary = document.getElementById('sell-summary');
      const enabled = !!DATA.market_tabs_enabled;
      if (tabsEl) {{
        tabsEl.classList.toggle('hidden', !enabled);
        if (enabled) {{
          tabsEl.querySelectorAll('.market-tab').forEach(btn => {{
            const on = btn.dataset.mode === marketMode;
            btn.classList.toggle('active', on);
            btn.setAttribute('aria-selected', on ? 'true' : 'false');
          }});
        }}
      }}
      if (buyBanner) {{
        const hold = DATA.buy_hold_until;
        const showBuy = enabled && marketMode === 'buy' && hold;
        buyBanner.classList.toggle('hidden', !showBuy);
        if (showBuy) {{
          const d = new Date(hold + 'T12:00:00');
          const label = d.toLocaleDateString('en-US', {{ month: 'long', year: 'numeric' }});
          buyBanner.innerHTML =
            '⏸ <b>Monitor only</b> — holding truck purchase until ' + label +
            '. Feed stays live for price tracking; no action needed yet.';
        }}
      }}
      if (sellSummary) {{
        const cs = DATA.comp_stats || {{}};
        const target = (DATA.sale_targets || {{}}).avion || {{}};
        const showSell = enabled && marketMode === 'sell';
        sellSummary.classList.toggle('hidden', !showSell);
        if (showSell) {{
          const comps = DATA.sell_listings || [];
          const floor = cs.floor_usd || target.min_ask_usd || 26000;
          const priced = cs.priced_count || 0;
          const above = cs.above_floor_count || 0;
          const yr = target.year || 1969;
          const len = target.length_ft || 36;
          let line = `<strong>${{yr}} Avion ${{len}}′ comps</strong> · floor ${{formatUsd(floor)}} · ${{comps.length}} tracked`;
          if (priced) {{
            line += ` · median ${{formatUsd(cs.median_usd)}} · ${{above}}/${{priced}} at/above floor`;
          }}
          const timing = cs.timing_note || '';
          sellSummary.innerHTML = line +
            (timing ? `<div class="sell-timing">${{escapeHtml(timing)}}</div>` : '');
        }}
      }}
    }}

    function bindMarketTabsOnce() {{
      const tabsEl = document.getElementById('market-tabs');
      if (!tabsEl || tabsEl._bound) return;
      tabsEl._bound = true;
      tabsEl.addEventListener('click', (e) => {{
        const btn = e.target.closest('.market-tab');
        if (!btn) return;
        setMarketMode(btn.dataset.mode);
      }});
    }}

    function configureVehicleUi() {{
      if (DATA.vertical !== 'vehicles') return;
      const hideIds = ['upcoming-nav', 'route-filter-box-wrap'];
      hideIds.forEach(id => {{
        const el = document.getElementById(id);
        if (el) el.hidden = true;
      }});
      const tripsPanel = document.querySelector('.trips-panel');
      if (tripsPanel) tripsPanel.hidden = true;
      const locBox = document.getElementById('vehicle-loc-filter-wrap');
      if (locBox) locBox.hidden = false;
    }}
    configureVehicleUi();
    loadMarketMode();
    bindMarketTabsOnce();

    DATA.listings.forEach(item => {{ item._was_new = item.is_new; }});
    (DATA.sell_listings || []).forEach(item => {{ item._was_new = item.is_new; }});

    function getManualSeen() {{
      try {{
        return new Set(JSON.parse(localStorage.getItem(manualSeenKey) || '[]'));
      }} catch (e) {{
        return new Set();
      }}
    }}

    function saveManualSeen(seen) {{
      localStorage.setItem(manualSeenKey, JSON.stringify([...seen]));
    }}

    function itemIsNew(item) {{
      if (!item || item.is_new === false) return false;
      return !getManualSeen().has(item.id || item.url);
    }}

    function applyManualSeenState() {{
      const seen = getManualSeen();
      let changed = false;
      allTrackedItems().forEach(item => {{
        const id = item.id || item.url;
        if (item.is_new === true && seen.has(id)) {{
          seen.delete(id);
          changed = true;
        }} else if (seen.has(id)) {{
          item.is_new = false;
        }}
      }});
      if (changed) saveManualSeen(seen);
    }}
    applyManualSeenState();

    function getPermExcluded() {{
      try {{
        return JSON.parse(localStorage.getItem(permExcludeKey) || '{{}}');
      }} catch (e) {{
        return {{}};
      }}
    }}

    function savePermExcluded(map) {{
      localStorage.setItem(permExcludeKey, JSON.stringify(map));
    }}

    function permExcludeKeys(item) {{
      const keys = [];
      if (item.id) keys.push(item.id);
      if (item.url && item.url !== item.id) keys.push(item.url);
      if (item.posting_id) keys.push(item.posting_id);
      return [...new Set(keys)];
    }}

    function isPermExcluded(item) {{
      const ex = getPermExcluded();
      return permExcludeKeys(item).some(k => ex[k]);
    }}

    function setPermExcluded(item, excluded) {{
      const ex = getPermExcluded();
      const meta = {{
        title: item.title || '',
        url: item.url || item.id || '',
        ts: Date.now(),
      }};
      for (const k of permExcludeKeys(item)) {{
        if (excluded) ex[k] = meta;
        else delete ex[k];
      }}
      savePermExcluded(ex);
      showToast(excluded ? 'Hidden from all future scans' : 'Listing restored');
      if (excluded && modalItem && modalItem.id === item.id) closeModal();
      render({{ listOnly: true, preserveScroll: true }});
    }}

    function toggleItemSeen(item) {{
      const id = item.id || item.url;
      const seen = getManualSeen();
      if (itemIsNew(item)) {{
        seen.add(id);
        item.is_new = false;
        saveManualSeen(seen);
        showToast('Marked as seen');
      }} else {{
        seen.delete(id);
        item.is_new = true;
        saveManualSeen(seen);
        showToast('Marked as new again');
      }}
      render({{ listOnly: true, preserveScroll: true }});
    }}

    function worthTripBadge(item, trip) {{
      if (DATA.vertical === 'vehicles') {{
        const score = item.fit_score || 0;
        let level = 'weak';
        let cls = 'fit-weak';
        if (score >= 75) {{ level = 'top'; cls = 'fit-top'; }}
        else if (score >= 55) {{ level = 'good'; cls = 'fit-good'; }}
        else if (score >= 35) {{ level = 'maybe'; cls = 'fit-maybe'; }}
        const tow = item.fit_tow_class ? ` · class ${{item.fit_tow_class}}` : '';
        return `<span class="fit-badge ${{cls}}">${{item.fit_label || 'Fit'}} · ${{score}}${{tow}}</span>`;
      }}
      const tiers = DATA.score_tiers || {{}};
      const driveMin = tiers.worth_the_drive_min || 70;
      const showMin = tiers.must_email_min || 50;
      let level = 'low';
      let text = 'Low priority';
      if (item.tier === 'worth_the_drive' || item.tier === 'paid_wanted'
          || item.score >= driveMin || item.is_priority) {{
        level = 'high';
        text = 'Worth the trip';
      }} else if (item.score >= showMin) {{
        level = 'medium';
        text = 'Maybe';
      }}
      let route = '';
      if (trip && trip.city && locationOnRouteTo(item.location, trip.city)) {{
        route = ' · on route';
      }} else if (item.route_tags) {{
        const tags = Object.values(item.route_tags).filter(Boolean);
        if (tags.length) route = ' · ' + tags[0];
      }}
      return `<span class="worth-badge worth-${{level}}">${{text}}${{route}}</span>`;
    }}

    function channelStatusIcon(ch) {{
      if (ch.status === 'ok') return '✓';
      if (ch.status === 'filtered') return '⚠';
      return '○';
    }}

    function channelStatusDetail(ch) {{
      if (ch.status === 'ok') return `${{ch.showing}} in feed (${{ch.fetched}} fetched)`;
      if (ch.status === 'filtered') return `${{ch.fetched}} fetched, ${{ch.showing}} pass score filter`;
      return ch.setup_hint || 'Not returning listings — check setup';
    }}

    function applyTripFromHash() {{
      const m = (location.hash || '').match(/trip=([^&]+)/);
      if (m) selectedTripId = decodeURIComponent(m[1]);
    }}

    function updateTripHash() {{
      const base = location.pathname + location.search;
      if (selectedTripId) history.replaceState(null, '', base + '#trip=' + encodeURIComponent(selectedTripId));
      else history.replaceState(null, '', base);
    }}

    function getTripSearches() {{
      try {{ return JSON.parse(localStorage.getItem(tripSearchesKey) || '{{}}'); }}
      catch (e) {{ return {{}}; }}
    }}

    function saveTripSearches(searches) {{
      localStorage.setItem(tripSearchesKey, JSON.stringify(searches));
    }}

    function currentSearchSnapshot() {{
      return {{
        include: [...includeFilters],
        hidden: [...hiddenCats],
        hiddenSources: [...hiddenSources],
        hiddenKeywords: [...hiddenKeywords],
        tripShowSaved,
        tripShowRoute,
        hideSeen,
        freeOnly,
      }};
    }}

    function applyTripSearch(tripId) {{
      const saved = getTripSearches()[tripId];
      if (!saved) {{
        tripShowSaved = false;
        tripShowRoute = false;
        return;
      }}
      includeFilters.clear();
      (saved.include || []).forEach(id => includeFilters.add(id));
      hiddenCats.clear();
      defaultHiddenCats().forEach(id => hiddenCats.add(id));
      (saved.hidden || []).forEach(id => hiddenCats.add(id));
      hiddenSources.clear();
      (saved.hiddenSources || []).forEach(id => hiddenSources.add(id));
      if (saved.showOfferUp === false) hiddenSources.add('src:offerup');
      hiddenKeywords.clear();
      (DATA.exclude_title_keywords || []).forEach(k => hiddenKeywords.add(k.toLowerCase()));
      (saved.hiddenKeywords || []).forEach(k => hiddenKeywords.add(k));
      tripShowSaved = saved.tripShowSaved === true;
      tripShowRoute = saved.tripShowRoute === true;
      hideSeen = saved.hideSeen !== false;
      freeOnly = !!saved.freeOnly;
    }}

    function saveSearchForTrip(tripId) {{
      if (!tripId) return;
      const all = getTripSearches();
      all[tripId] = currentSearchSnapshot();
      saveTripSearches(all);
      showToast('Filters saved for trip');
      renderUpcomingNav();
      renderTripsPanel();
    }}

    function searchTagsForTrip(tripId) {{
      const saved = getTripSearches()[tripId];
      if (!saved) return '';
      const tags = [];
      (saved.include || []).forEach(id => {{
        const label = (DATA.filter_groups || []).flatMap(g => g.options || [])
          .concat((DATA.filter_groups || []).flatMap(g => (g.families || []).flatMap(f => f.options || [])))
          .find(o => o.id === id);
        tags.push(label ? label.label : id);
      }});
      if (saved.tripShowSaved === false) tags.push('no saved');
      if (saved.tripShowRoute === false) tags.push('no route');
      return tags.length ? tags.join(' · ') : 'All filters saved';
    }}

    function activateTrip(tripId, applySavedSearch) {{
      selectedTripId = tripId || '';
      if (selectedTripId && applySavedSearch !== false) applyTripSearch(selectedTripId);
      saveFilterState();
      updateTripHash();
      render();
    }}

    applyTripFromHash();
    if (!selectedTripId && DATA.focus_trip_id) {{
      selectedTripId = DATA.focus_trip_id;
      tripShowSaved = true;
      tripShowRoute = true;
    }}
    if (selectedTripId) applyTripSearch(selectedTripId);

    function getTrips() {{
      try {{
        const stored = JSON.parse(localStorage.getItem(tripsKey) || 'null');
        if (Array.isArray(stored) && stored.length) return stored;
      }} catch (e) {{}}
      return (DATA.trips || []).map(t => ({{ ...t }}));
    }}

    function saveTrips(trips) {{
      localStorage.setItem(tripsKey, JSON.stringify(trips));
    }}

    function refreshTripDateLabel(trip) {{
      if (!trip.start) {{
        trip.date_label = '';
        return;
      }}
      const s = new Date(trip.start + 'T12:00:00');
      const fmt = {{ month: 'long', day: 'numeric' }};
      const day = s.toLocaleDateString('en-US', {{ weekday: 'short' }});
      if (!trip.end || trip.end === trip.start) {{
        trip.date_label = s.toLocaleDateString('en-US', fmt) + ' — ' + day;
      }} else {{
        const e = new Date(trip.end + 'T12:00:00');
        trip.date_label = s.toLocaleDateString('en-US', fmt) + ' – ' +
          e.toLocaleDateString('en-US', fmt);
      }}
    }}

    function sortTripsByDate(trips) {{
      return trips.slice().sort((a, b) => {{
        const sa = a.start || '9999-12-31';
        const sb = b.start || '9999-12-31';
        if (sa !== sb) return sa.localeCompare(sb);
        return (a.end || '').localeCompare(b.end || '');
      }});
    }}

    function getNextUpcomingTrip() {{
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      return sortTripsByDate(getTrips()).find(t => {{
        if (!t.end) return !!t.start;
        return new Date(t.end + 'T23:59:59') >= today;
      }}) || null;
    }}

    function getSelectedTrip() {{
      if (!selectedTripId) return null;
      return getTrips().find(t => t.id === selectedTripId) || null;
    }}

    function countSavedForTrip(tripId) {{
      return Object.values(getSaved()).filter(s => s.tripId === tripId).length;
    }}

    function getSaved() {{
      try {{ return JSON.parse(localStorage.getItem(saveKey) || '{{}}'); }}
      catch (e) {{ return {{}}; }}
    }}

    function isSaved(id) {{ return !!getSaved()[id]; }}

    function isSavedForTrip(itemId, tripId) {{
      const s = getSaved()[itemId];
      return !!(s && s.tripId === tripId);
    }}

    function getCompareMap() {{
      try {{ return JSON.parse(localStorage.getItem(compareKey) || '{{}}'); }}
      catch (e) {{ return {{}}; }}
    }}

    function isCompareSelected(id) {{
      return !!getCompareMap()[id];
    }}

    function countCompareSelected() {{
      return Object.keys(getCompareMap()).length;
    }}

    function saveCompareEntry(item) {{
      const map = getCompareMap();
      map[item.id] = {{
        title: item.title,
        price: item.price,
        year: item.year || '',
        make: item.make || '',
        model: item.model || '',
        miles: item.miles || '',
        location: item.location || '',
        platform: item.platform || '',
        source: item.source || '',
        score: item.score || 0,
        category: item.category_label || '',
        url: item.url || '',
        description: (item.description || '').slice(0, 500),
        make_preferred: item.make_preferred ? 'yes' : '',
      }};
      localStorage.setItem(compareKey, JSON.stringify(map));
      updateCompareUi();
    }}

    function removeCompareEntry(id) {{
      const map = getCompareMap();
      delete map[id];
      localStorage.setItem(compareKey, JSON.stringify(map));
      updateCompareUi();
    }}

    function toggleCompareItem(item, on) {{
      if (on) saveCompareEntry(item);
      else removeCompareEntry(item.id);
    }}

    function updateCompareUi() {{
      const n = countCompareSelected();
      const enabled = !!DATA.compare_export_enabled && marketMode !== 'sell';
      ['export-compare', 'clear-compare'].forEach(id => {{
        const el = document.getElementById(id);
        if (el) el.hidden = !enabled;
      }});
      const bar = document.getElementById('compare-bar');
      const countEl = document.getElementById('compare-count');
      if (bar && countEl) {{
        bar.classList.toggle('hidden', !enabled || n === 0);
        countEl.textContent = n + ' selected for compare export';
      }}
    }}

    function csvEscape(val) {{
      const s = String(val == null ? '' : val).replace(/"/g, '""');
      return /[",\\n\\r]/.test(s) ? `"${{s}}"` : s;
    }}

    function exportCompareCsv() {{
      const rows = Object.values(getCompareMap());
      if (!rows.length) {{
        showToast('Check listings to add them to compare first');
        return;
      }}
      const headers = [
        'Title', 'Price', 'Price USD', 'Year', 'Make', 'Model', 'Miles', 'Location',
        'Fit score', 'Fit label', 'Tow class', 'Platform', 'Source', 'Score',
        'Chevy/GMC preferred', 'Listing URL', 'Notes',
      ];
      const lines = [headers.join(',')];
      rows.forEach(r => {{
        lines.push([
          r.title, r.price, r.price_usd, r.year, r.make, r.model, r.miles, r.location,
          r.fit_score, r.fit_label, r.fit_tow_class, r.platform, r.source, r.score,
          r.make_preferred, r.url, r.description,
        ].map(csvEscape).join(','));
      }});
      const blob = new Blob(['\\ufeff' + lines.join('\\n')], {{ type: 'text/csv;charset=utf-8;' }});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'vehicle-compare-' + (DATA.profile_id || 'skout') + '.csv';
      a.click();
      URL.revokeObjectURL(a.href);
      showToast('Exported ' + rows.length + ' rows — open in Excel');
    }}

    function renderWebMarketplaces() {{
      const wm = DATA.web_marketplaces || {{}};
      const panel = document.getElementById('web-market-panel');
      if (!panel || !wm.sites) return;
      panel.hidden = !(wm.sites && wm.sites.length);
      const searchesEl = document.getElementById('web-market-searches');
      if (searchesEl) {{
        const searches = wm.searches || [];
        searchesEl.innerHTML = searches.length
          ? '<div class="filter-hint" style="margin-bottom:.35rem">Open a saved search (login may be required on some sites):</div>' +
            searches.map(s =>
              `<a class="web-market-link" href="${{escapeHtml(s.url)}}" target="_blank" rel="noopener">${{escapeHtml(s.label)}}</a>`
            ).join('')
          : '';
      }}
      const sitesEl = document.getElementById('web-market-sites');
      if (sitesEl) {{
        const tiers = ['A', 'B', 'C'];
        sitesEl.innerHTML = tiers.map(tier => {{
          const group = (wm.sites || []).filter(s => s.tier === tier);
          if (!group.length) return '';
          return `<div class="filter-hint" style="margin-top:.5rem">Tier ${{tier}}</div>` +
            group.map(s =>
              `<a class="web-market-link" href="${{escapeHtml(s.home)}}" target="_blank" rel="noopener">${{escapeHtml(s.name)}} <small>(${{escapeHtml(s.browse_login || '')}})</small></a>`
            ).join('');
        }}).join('');
      }}
      const bar = document.getElementById('web-market-bar');
      if (bar && (DATA.vertical === 'vehicles' || DATA.trailer_hunt)) {{
        bar.classList.remove('hidden');
        const chips = (wm.searches || []).slice(0, 10).map(s =>
          `<a class="web-market-chip" href="${{escapeHtml(s.url)}}" target="_blank" rel="noopener">${{escapeHtml(s.label.replace(/ — .*/, ''))}}</a>`
        ).join('');
        bar.innerHTML =
          `<div class="web-market-bar-title">Autotrader-class searches (also in feed via AutoTempest)</div>` +
          `<div class="web-market-chips">${{chips}}</div>`;
      }}
    }}

    function showToast(msg) {{
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.classList.add('show');
      setTimeout(() => t.classList.remove('show'), 2200);
    }}

    function citiesTowardDestination(dest) {{
      const needle = (dest || '').trim().toLowerCase();
      if (!needle) return [];
      for (const route of Object.values(DATA.routes || {{}})) {{
        const cities = route.cities || [];
        for (let i = 0; i < cities.length; i++) {{
          const c = cities[i].toLowerCase();
          if (c.includes(needle) || needle.includes(c)) return cities.slice(0, i + 1);
        }}
      }}
      return [dest.trim()];
    }}

    function locationOnRouteTo(location, dest) {{
      if (!(dest || '').trim()) return true;
      const cities = citiesTowardDestination(dest);
      const loc = (location || '').toLowerCase();
      return cities.some(c => loc.includes(c.toLowerCase()));
    }}

    function pickupMessage(item) {{
      return item.pickup_message || DATA.pickup_template || '';
    }}

    function sanitizeReplyUrl(url) {{
      if (!url) return '';
      return url.split('__SERVICE_ID__')[0].replace(/\\/$/, '');
    }}

    async function copyText(text) {{
      if (!text) return false;
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:fixed;left:-9999px;top:0';
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try {{ ok = document.execCommand('copy'); }} catch (e) {{}}
      document.body.removeChild(ta);
      if (ok) return true;
      try {{
        if (navigator.clipboard) {{
          await navigator.clipboard.writeText(text);
          return true;
        }}
      }} catch (e) {{}}
      return false;
    }}

    function normalizeAlsoOn(alsoOn) {{
      return (alsoOn || []).map(a => {{
        if (typeof a === 'string') return {{ platform: a, url: '', source: '', platform_icon: '🔗' }};
        return a;
      }}).filter(a => a && a.url);
    }}

    function listingOpenTarget(item) {{
      if (!item) return {{ url: '', source: '', platform: '', platform_icon: '🔗' }};
      if (item.open_url) {{
        return {{
          url: item.open_url,
          source: item.open_source || item.source,
          platform: item.open_platform || item.platform,
          platform_icon: item.open_platform_icon || item.platform_icon || '🔗',
        }};
      }}
      const srcBase = s => (s || '').split(':')[0];
      const rank = s => ({{
        web: 6, auction: 6, freecycle: 5, facebook: 4, facebook_group: 4,
        trash_nothing: 4, nextdoor: 3, offerup: 3, craigslist: 1,
      }}[srcBase(s)] || 1);
      const alts = normalizeAlsoOn(item.also_on).filter(a => a.url !== item.url);
      const candidates = [{{
        url: item.url, source: item.source, platform: item.platform,
        platform_icon: item.platform_icon || '🔗',
      }}, ...alts];
      candidates.sort((a, b) => {{
        const acl = srcBase(a.source) === 'craigslist' ? 1 : 0;
        const bcl = srcBase(b.source) === 'craigslist' ? 1 : 0;
        if (acl !== bcl) return acl - bcl;
        return rank(b.source) - rank(a.source);
      }});
      return candidates[0] || {{ url: item.url || '', source: item.source || '', platform: item.platform || '' }};
    }}

    function openListingLabel(item) {{
      const t = listingOpenTarget(item);
      if (t.platform && t.platform !== item.platform) return `Open on ${{t.platform}}`;
      return 'Open listing';
    }}

    function alsoOnHtml(item) {{
      const alts = normalizeAlsoOn(item.also_on);
      if (!alts.length) return '';
      const srcBase = s => (s || '').split(':')[0];
      const links = [...alts].sort((a, b) => {{
        const acl = srcBase(a.source) === 'craigslist' ? 1 : 0;
        const bcl = srcBase(b.source) === 'craigslist' ? 1 : 0;
        return acl - bcl;
      }});
      return `<p class="also-on">Also on: ${{links.map(a =>
        `<a href="${{escapeHtml(a.url)}}" target="_blank" rel="noopener noreferrer" ` +
        `onclick="return skoutOpenListing(event, '${{escapeHtml(a.source)}}')">${{escapeHtml(a.platform)}}</a>`
      ).join(' · ')}}</p>`;
    }}

    async function contactSeller(item) {{
      if (!item) return;
      const msg = pickupMessage(item);
      const subject = ('Re: ' + (item.title || 'listing')).slice(0, 120);
      const body = msg.slice(0, 1800);
      const copied = await copyText(msg);

      const method = item.contact_method || (item.reply_email ? 'email' : 'site');
      let contactUrl = sanitizeReplyUrl(item.contact_url || item.reply_url || '');
      const listingUrl = item.url || '';
      const openT = listingOpenTarget(item);
      const openSrc = (openT.source || '').split(':')[0];

      if (method === 'email' && (item.reply_email || '').trim()) {{
        const to = item.reply_email.trim();
        await copyText(`Seller: ${{to}}\\n\\n${{msg}}`);
        showToast('Copied Craigslist relay email + pickup message');
        return;
      }}

      if (openSrc !== 'craigslist' && openT.url) {{
        window.open(openT.url, '_blank', 'noopener,noreferrer');
        showToast(copied
          ? `Message copied · opened ${{openT.platform || 'listing'}}`
          : `Opened ${{openT.platform || 'listing'}} — use Copy message`);
        return;
      }}

      const src = (item.source || '').split(':')[0];
      if (src === 'craigslist') {{
        if ((item.reply_email || '').trim()) {{
          await copyText(`Seller: ${{item.reply_email.trim()}}\\n\\n${{msg}}`);
          showToast('Copied Craigslist relay email + pickup message');
          return;
        }}
        const openUrl = contactUrl && contactUrl.includes('/reply/') ? contactUrl : listingUrl;
        if (openUrl) window.open(openUrl, '_blank', 'noopener,noreferrer');
        showToast(copied
          ? 'Message copied — paste in Craigslist Reply box'
          : 'Open listing · use Copy message button');
        return;
      }}

      if (contactUrl) {{
        window.open(contactUrl, '_blank', 'noopener,noreferrer');
        const label = item.contact_label || 'Contact on site';
        showToast(copied
          ? `Message copied · opened ${{label}} — paste there`
          : `Opened ${{label}} — use Copy message`);
        return;
      }}

      showToast(copied
        ? 'Message copied — open the listing to contact seller'
        : 'Use Copy message button below');
    }}

    function skoutOpenListing(e, source) {{
      if (!(source || '').startsWith('craigslist')) return true;
      if (sessionStorage.getItem('skout_cl_open_ok') === '1') return true;
      const ok = confirm(
        'Craigslist may block your browser after a big scan. Prefer Copy seller email for the relay address. Open listing anyway?'
      );
      if (!ok) {{
        e.preventDefault();
        return false;
      }}
      sessionStorage.setItem('skout_cl_open_ok', '1');
      return true;
    }}

    async function copyPickupMessage(item) {{
      const text = pickupMessage(item);
      const ok = await copyText(text);
      if (ok) showToast('Pickup message copied');
      else prompt('Copy this message:', text);
    }}

    function saveListing(item, tripId, tripName) {{
      const saved = getSaved();
      saved[item.id] = {{
        ...item, tripId, tripName, savedAt: Date.now()
      }};
      localStorage.setItem(saveKey, JSON.stringify(saved));
      render({{ listOnly: true, preserveScroll: true }});
    }}

    function unsaveListing(id) {{
      const saved = getSaved();
      delete saved[id];
      localStorage.setItem(saveKey, JSON.stringify(saved));
      render({{ listOnly: true, preserveScroll: true }});
    }}

    function pickTripAndSave(item) {{
      if (DATA.vertical === 'vehicles') {{
        saveListing(item, 'shortlist', 'Shortlist');
        showToast('Saved to shortlist');
        return;
      }}
      if (selectedTripId) {{
        const t = getTrips().find(x => x.id === selectedTripId);
        if (t) {{
          saveListing(item, t.id, t.name);
          showToast('Saved for ' + t.name);
          return;
        }}
      }}
      const next = getNextUpcomingTrip();
      if (next) {{
        saveListing(item, next.id, next.name);
        showToast('Saved for ' + next.name);
        return;
      }}
      const trips = sortTripsByDate(getTrips());
      if (!trips.length) {{
        saveListing(item, 'general', 'Any trip');
        return;
      }}
      const labels = trips.map((t, i) =>
        `${{i + 1}}. ${{t.name}} (${{t.date_label || t.start || 'no date'}})`).join('\\n');
      const choice = prompt('Save for which trip?\\n\\n' + labels + '\\n\\nEnter number:');
      const idx = parseInt(choice, 10) - 1;
      if (idx >= 0 && idx < trips.length) {{
        saveListing(item, trips[idx].id, trips[idx].name);
        showToast('Saved for ' + trips[idx].name);
      }}
    }}

    function listingImages(item) {{
      const urls = (item.image_urls || []).filter(Boolean);
      if (urls.length) return urls;
      return item.image_url ? [item.image_url] : [];
    }}

    function photoReferrerAttr(url) {{
      if (!url || !/^https?:\\/\\//i.test(url)) return '';
      return ' referrerpolicy="strict-origin-when-cross-origin"';
    }}

    function setPhotoReferrer(img, url) {{
      if (!img) return;
      if (!url || !/^https?:\\/\\//i.test(url)) img.removeAttribute('referrerpolicy');
      else img.referrerPolicy = 'strict-origin-when-cross-origin';
    }}

    const tileHoverTimers = new WeakMap();

    function setTileImage(img, url, icon) {{
      if (!img || !url) return;
      img.src = url;
      img.dataset.src = url;
      if (icon) img.dataset.icon = icon;
      img.dataset.retried = '';
    }}

    function startTileHoverCycle(btn, imgs, icon) {{
      if (!imgs || imgs.length < 2) return;
      const img = btn.querySelector('img');
      if (!img) return;
      stopTileHoverCycle(btn);
      imgs.slice(1).forEach(url => {{
        const pre = new Image();
        setPhotoReferrer(pre, url);
        pre.src = url;
      }});
      let idx = 0;
      btn.classList.add('cycling');
      const timer = setInterval(() => {{
        idx = (idx + 1) % imgs.length;
        setTileImage(img, imgs[idx], icon);
      }}, 850);
      tileHoverTimers.set(btn, {{ timer, imgs, img, icon }});
    }}

    function stopTileHoverCycle(btn) {{
      const state = tileHoverTimers.get(btn);
      if (!state) return;
      clearInterval(state.timer);
      tileHoverTimers.delete(btn);
      btn.classList.remove('cycling');
      setTileImage(state.img, state.imgs[0], state.icon);
    }}

    function renderGallery() {{
      const wrap = document.getElementById('modal-gallery');
      const img = document.getElementById('modal-img');
      const prev = document.getElementById('gallery-prev');
      const next = document.getElementById('gallery-next');
      const count = document.getElementById('gallery-count');
      const dots = document.getElementById('gallery-dots');
      if (!galleryUrls.length) {{
        wrap.classList.add('hidden');
        img.removeAttribute('src');
        return;
      }}
      wrap.classList.remove('hidden');
      galleryIdx = Math.max(0, Math.min(galleryIdx, galleryUrls.length - 1));
      img.src = galleryUrls[galleryIdx];
      img.dataset.src = galleryUrls[galleryIdx];
      setPhotoReferrer(img, galleryUrls[galleryIdx]);
      img.onerror = () => window.skoutImgError(img);
      const multi = galleryUrls.length > 1;
      prev.style.display = multi ? '' : 'none';
      next.style.display = multi ? '' : 'none';
      prev.disabled = galleryIdx <= 0;
      next.disabled = galleryIdx >= galleryUrls.length - 1;
      count.textContent = multi ? `${{galleryIdx + 1}} / ${{galleryUrls.length}}` : '';
      dots.innerHTML = multi
        ? galleryUrls.map((_, i) =>
            `<button type="button" class="gallery-dot${{i === galleryIdx ? ' active' : ''}}" data-idx="${{i}}" aria-label="Photo ${{i + 1}}"></button>`
          ).join('')
        : '';
      dots.querySelectorAll('.gallery-dot').forEach(dot => {{
        dot.onclick = () => {{
          galleryIdx = parseInt(dot.dataset.idx, 10);
          renderGallery();
        }};
      }});
    }}

    function galleryStep(delta) {{
      if (!galleryUrls.length) return;
      galleryIdx = Math.max(0, Math.min(galleryIdx + delta, galleryUrls.length - 1));
      renderGallery();
    }}

    document.getElementById('gallery-prev').onclick = () => galleryStep(-1);
    document.getElementById('gallery-next').onclick = () => galleryStep(1);

    (function bindGallerySwipe() {{
      const wrap = document.getElementById('modal-gallery');
      let startX = 0;
      wrap.addEventListener('touchstart', (e) => {{
        startX = e.changedTouches[0].clientX;
      }}, {{ passive: true }});
      wrap.addEventListener('touchend', (e) => {{
        const dx = e.changedTouches[0].clientX - startX;
        if (Math.abs(dx) < 40) return;
        galleryStep(dx < 0 ? 1 : -1);
      }}, {{ passive: true }});
    }})();

    function populateModal(item) {{
      modalItem = item;
      const modal = document.getElementById('modal');
      galleryUrls = listingImages(item);
      galleryIdx = 0;
      renderGallery();
      document.getElementById('modal-title').textContent = item.title;
      const browseHint = modalBrowseList.length > 1
        ? ` · ${{modalBrowseIndex + 1}}/${{modalBrowseList.length}} (← →)`
        : '';
      document.getElementById('modal-meta').textContent =
        `${{item.platform}} · ${{item.price}} · ${{item.location}}` +
        (DATA.vertical === 'vehicles'
          ? ` · fit ${{item.fit_score || 0}} (${{item.fit_label || 'n/a'}})`
          : ` · score ${{item.score}}`) +
        (item.make ? ` · ${{item.make}} ${{item.model}}` : '') +
        (item.miles ? ` · ${{item.miles}}` : '') +
        browseHint;
      document.getElementById('modal-desc').textContent =
        item.description || 'No description available — open the listing for full details.';
      const descEl = document.getElementById('modal-desc');
      descEl.classList.toggle('estate-desc', !!item.is_estate_sale);
      const openT = listingOpenTarget(item);
      const modalLink = document.getElementById('modal-link');
      modalLink.href = openT.url || item.url;
      modalLink.textContent = openListingLabel(item);
      modalLink.onclick = (e) => skoutOpenListing(e, openT.source || item.source);
      const compareWrap = document.getElementById('modal-compare-wrap');
      const compareBox = document.getElementById('modal-compare');
      if (compareWrap && compareBox) {{
        compareWrap.hidden = !DATA.compare_export_enabled;
        compareBox.checked = isCompareSelected(item.id);
        compareBox.onchange = () => toggleCompareItem(item, compareBox.checked);
      }}
      const excludeWrap = document.getElementById('modal-exclude-wrap');
      const excludeBox = document.getElementById('modal-exclude');
      if (excludeWrap && excludeBox) {{
        excludeBox.checked = isPermExcluded(item);
        excludeBox.onchange = () => setPermExcluded(item, excludeBox.checked);
      }}
      const saveBtn = document.getElementById('modal-save');
      saveBtn.textContent = isSaved(item.id)
        ? (showIcons ? '📌 Saved — tap to remove' : 'Saved — tap to remove')
        : (DATA.vertical === 'vehicles'
          ? (showIcons ? '📌 Save to shortlist' : 'Save to shortlist')
          : (showIcons ? '📌 Save for trip' : 'Save for trip'));
      const contactBtn = document.getElementById('modal-email');
      const cLabel = item.contact_label || (item.reply_email ? 'Copy seller email' : 'Contact seller');
      contactBtn.textContent = (showIcons && item.contact_method === 'email' ? '✉ ' : '') + cLabel;
      modal.scrollTop = 0;
    }}

    function openModal(item, list, index) {{
      modalBrowseList = list || [];
      modalBrowseIndex = typeof index === 'number' && index >= 0
        ? index
        : modalBrowseList.findIndex(x => x.id === item.id);
      populateModal(item);
      const modal = document.getElementById('modal');
      modal.classList.remove('hidden');
      modal.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
      window.scrollTo(0, 0);
    }}

    function modalStepListing(delta) {{
      if (!modalBrowseList.length || modalBrowseIndex < 0) return;
      const next = modalBrowseIndex + delta;
      if (next < 0 || next >= modalBrowseList.length) return;
      modalBrowseIndex = next;
      populateModal(modalBrowseList[modalBrowseIndex]);
    }}

    function closeModal() {{
      document.getElementById('modal').classList.add('hidden');
      document.getElementById('modal').setAttribute('aria-hidden', 'true');
      document.body.style.overflow = '';
      modalItem = null;
      modalBrowseList = [];
      modalBrowseIndex = -1;
    }}

    document.getElementById('modal-close').onclick = closeModal;
    document.getElementById('modal-backdrop').onclick = closeModal;
    document.getElementById('modal-save').onclick = () => {{
      if (!modalItem) return;
      if (isSaved(modalItem.id)) unsaveListing(modalItem.id);
      else pickTripAndSave(modalItem);
      document.getElementById('modal-save').textContent =
        isSaved(modalItem.id)
          ? (showIcons ? '📌 Saved — tap to remove' : 'Saved — tap to remove')
          : (DATA.vertical === 'vehicles'
            ? (showIcons ? '📌 Save to shortlist' : 'Save to shortlist')
            : (showIcons ? '📌 Save for trip' : 'Save for trip'));
    }};
    document.getElementById('modal-email').onclick = async () => {{
      if (modalItem) await contactSeller(modalItem);
    }};
    document.getElementById('modal-copy').onclick = () => {{
      if (modalItem) copyPickupMessage(modalItem);
    }};

    document.addEventListener('keydown', (e) => {{
      const modal = document.getElementById('modal');
      if (!modalItem || modal.classList.contains('hidden')) return;
      if (e.key === 'ArrowLeft') {{ e.preventDefault(); modalStepListing(-1); }}
      if (e.key === 'ArrowRight') {{ e.preventDefault(); modalStepListing(1); }}
      if (e.key === 'Escape') closeModal();
    }});

    function getQuickRecent() {{
      try {{
        const raw = JSON.parse(localStorage.getItem(quickSearchRecentKey) || '[]');
        return Array.isArray(raw) ? raw : [];
      }} catch (e) {{ return []; }}
    }}

    function saveQuickRecent(list) {{
      localStorage.setItem(quickSearchRecentKey, JSON.stringify(list.slice(0, 8)));
    }}

    function recordQuickRecent(preset) {{
      if (!preset || !preset.id) return;
      const entry = {{
        id: preset.id,
        label: preset.label || preset.id,
        icon: preset.icon || '',
        keywords: preset.keywords || [],
        route_dest: Object.prototype.hasOwnProperty.call(preset, 'route_dest')
          ? (preset.route_dest || '')
          : '',
        ts: Date.now(),
      }};
      const rest = getQuickRecent().filter(r => r.id !== entry.id);
      saveQuickRecent([entry, ...rest]);
    }}

    function presetById(id) {{
      const presets = (DATA.quick_searches || {{}}).presets || [];
      return presets.find(p => p.id === id) || null;
    }}

    function itemMatchesSearchKeywords(item) {{
      if (!searchKeywords.length) return true;
      const blob = ((item.title || '') + ' ' + (item.description || '')).toLowerCase();
      return searchKeywords.some(kw => blob.includes(String(kw).toLowerCase()));
    }}

    function keywordMatchStrength(item) {{
      if (!searchKeywords.length) return 0;
      const title = (item.title || '').toLowerCase();
      const desc = (item.description || '').toLowerCase();
      let strength = 0;
      for (const kw of searchKeywords) {{
        const k = String(kw).toLowerCase();
        if (title.includes(k)) strength += 3;
        else if (desc.includes(k)) strength += 1;
      }}
      return strength;
    }}

    function syncRouteInput() {{
      const routeInput = document.getElementById('route-dest');
      if (routeInput) routeInput.value = routeDest;
    }}

    function applyQuickSearch(preset) {{
      if (!preset) {{
        activeQuickSearchId = '';
        searchKeywords = [];
        saveFilterState();
        renderQuickSearchBar();
        render({{ listOnly: true, preserveScroll: true }});
        return;
      }}
      activeQuickSearchId = preset.id;
      searchKeywords = (preset.keywords || []).map(k => String(k).toLowerCase());
      if (Object.prototype.hasOwnProperty.call(preset, 'route_dest')) {{
        routeDest = preset.route_dest || '';
      }} else {{
        // Don't auto-select a route — nationwide unless the preset opts in
        routeDest = '';
      }}
      recordQuickRecent(preset);
      saveFilterState();
      syncRouteInput();
      renderQuickSearchBar();
      render({{ listOnly: true, preserveScroll: true }});
    }}

    function applyCustomQuickSearch(term) {{
      const kw = (term || '').trim().toLowerCase();
      if (!kw) return;
      const preset = {{
        id: 'custom:' + kw,
        label: kw,
        icon: '🔎',
        keywords: [kw],
        route_dest: '',
      }};
      applyQuickSearch(preset);
    }}

    function renderQuickSearchBar() {{
      const bar = document.getElementById('quick-search-bar');
      const chips = document.getElementById('quick-search-chips');
      const routeEl = document.getElementById('quick-search-route');
      const sortEl = document.getElementById('list-sort');
      const qs = DATA.quick_searches || {{}};
      const isSellMode = DATA.market_tabs_enabled && marketMode === 'sell';
      if (!bar || DATA.vertical === 'vehicles' || isSellMode || !(qs.presets || []).length) {{
        if (bar) bar.classList.add('hidden');
        return;
      }}
      bar.classList.remove('hidden');
      if (routeEl) {{
        const dest = (routeDest || '').trim();
        routeEl.textContent = dest
          ? ('Route: ' + dest) + (searchKeywords.length ? ' · filtered' : '')
          : (searchKeywords.length ? 'Nationwide · filtered' : '');
      }}
      if (sortEl && sortEl.value !== listSort) sortEl.value = listSort;

      const recent = getQuickRecent().filter(r => !presetById(r.id));
      let html = `<button type="button" class="quick-chip${{activeQuickSearchId ? '' : ' active'}}" data-id="">All</button>`;
      (qs.presets || []).forEach(p => {{
        const active = activeQuickSearchId === p.id ? ' active' : '';
        html += `<button type="button" class="quick-chip${{active}}" data-id="${{escapeHtml(p.id)}}">` +
          `${{escapeHtml(chipLabel(p.icon, p.label))}}</button>`;
      }});
      recent.forEach(r => {{
        const active = activeQuickSearchId === r.id ? ' active' : '';
        html += `<button type="button" class="quick-chip recent${{active}}" data-id="${{escapeHtml(r.id)}}">` +
          `${{escapeHtml(chipLabel(r.icon || (showIcons ? '🔎' : ''), r.label))}}</button>`;
      }});
      if (chips) chips.innerHTML = html;
      if (chips && !chips._bound) {{
        chips._bound = true;
        chips.onclick = (e) => {{
          const btn = e.target.closest('.quick-chip');
          if (!btn) return;
          const id = btn.dataset.id || '';
          if (!id) {{
            applyQuickSearch(null);
            return;
          }}
          const preset = presetById(id) || getQuickRecent().find(r => r.id === id);
          if (preset) applyQuickSearch(preset);
        }};
      }}
    }}

    function bindQuickSearchOnce() {{
      const sortEl = document.getElementById('list-sort');
      if (sortEl && !sortEl._bound) {{
        sortEl._bound = true;
        sortEl.onchange = () => {{
          listSort = sortEl.value || 'score';
          saveFilterState();
          render({{ listOnly: true, preserveScroll: true }});
        }};
      }}
      const goBtn = document.getElementById('quick-search-go');
      const input = document.getElementById('quick-search-input');
      if (goBtn && !goBtn._bound) {{
        goBtn._bound = true;
        goBtn.onclick = () => applyCustomQuickSearch(input ? input.value : '');
      }}
      if (input && !input._bound) {{
        input._bound = true;
        input.onkeydown = (e) => {{
          if (e.key === 'Enter') {{
            e.preventDefault();
            applyCustomQuickSearch(input.value);
          }}
        }};
      }}
    }}

    function itemMatchesInclude(item, filterId) {{
      if (filterId === 'priority') return item.is_priority;
      if (filterId === 'saved') return isSaved(item.id);
      if (filterId === 'free_only') return item.is_free;
      if (filterId === 'chevy_preferred') return !!item.make_preferred;
      if (filterId === 'spec:vintage_square') return !!item.is_vintage_square;
      if (filterId === 'spec:vintage_quality') return !!item.is_vintage_square && !!item.is_vintage_quality;
      if (filterId === 'compare_pick') return isCompareSelected(item.id);
      if (filterId === 'rebuilt') return !!item.is_rebuilt;
      if (filterId === 'fleet') return !!item.is_fleet;
      if (filterId === 'spec:hd_tow') return !!item.is_hd_tow;
      if (filterId === 'spec:commercial') return !!item.is_commercial;
      if (filterId === 'spec:diesel') return !!item.is_diesel;
      if (filterId === 'spec:auction') return !!item.is_auction;
      if (filterId === 'spec:grant_credit') return !!item.grant_credit_angle;
      if (filterId === 'fit:top') return (item.fit_score || 0) >= 75;
      if (filterId.startsWith('price:')) {{
        const p = item.price_usd;
        if (p == null || p === '') return false;
        if (filterId === 'price:under_5k') return p < 5000;
        if (filterId === 'price:5_10k') return p >= 5000 && p < 10000;
        if (filterId === 'price:10_15k') return p >= 10000 && p < 15000;
        if (filterId === 'price:15_20k') return p >= 15000 && p <= 20000;
        return false;
      }}
      if (filterId.startsWith('loc:')) return item.location_band === filterId.slice(4);
      if (filterId.startsWith('cat:')) return item.category_id === filterId.slice(4);
      if (filterId.startsWith('tag:')) {{
        const tag = filterId.slice(4);
        return item.route_tags && item.route_tags[tag];
      }}
      if (filterId.startsWith('src:')) {{
        const srcNeedle = filterId.slice(4);
        return (item.source || '') === srcNeedle || (item.source || '').startsWith(srcNeedle + ':');
      }}
      return true;
    }}

    function isSourceHidden(item) {{
      if (!hiddenSources.size) return false;
      const srcNeedle = (item.source || '').split(':')[0];
      for (const hid of hiddenSources) {{
        const key = hid.startsWith('src:') ? hid.slice(4) : hid;
        if (srcNeedle === key || (item.source || '').startsWith(key + ':')) return true;
      }}
      return false;
    }}

    function matchesFilter(item) {{
      if (isPermExcluded(item)) return false;
      if (hideSeen && !itemIsNew(item)) return false;
      if (freeOnly && !item.is_free) return false;
      if (isSourceHidden(item)) return false;
      if (hiddenCats.has(item.category_id)) return false;
      for (const kw of hiddenKeywords) {{
        const blob = ((item.title || '') + ' ' + (item.description || '')).toLowerCase();
        if (blob.includes(kw)) return false;
      }}
      if (searchKeywords.length && !itemMatchesSearchKeywords(item)) return false;
      if (DATA.vertical === 'vehicles') {{
        if (vehicleLocQuery) {{
          const q = vehicleLocQuery.trim().toLowerCase();
          if (q && !(item.location || '').toLowerCase().includes(q)) return false;
        }}
      }} else {{
        const trip = getSelectedTrip();
        if (trip && (tripShowSaved || tripShowRoute)) {{
          const savedFor = isSavedForTrip(item.id, trip.id);
          const onRoute = trip.city
            ? locationOnRouteTo(item.location, trip.city)
            : false;
          const ok = (tripShowSaved && savedFor) || (tripShowRoute && onRoute);
          if (!ok) return false;
        }}
        if (routeDest && !locationOnRouteTo(item.location, routeDest)) return false;
      }}
      if (!includeFilters.size) return true;
      for (const f of includeFilters) {{
        if (itemMatchesInclude(item, f)) return true;
      }}
      return false;
    }}

    function sortShownList(list, trip) {{
      if (DATA.market_tabs_enabled && marketMode === 'sell') {{
        const targetLen = ((DATA.sale_targets || {{}}).avion || {{}}).length_ft || 36;
        return list.slice().sort((a, b) => {{
          const seenA = itemIsNew(a) ? 0 : 1;
          const seenB = itemIsNew(b) ? 0 : 1;
          if (seenA !== seenB) return seenA - seenB;
          const avA = (a.title || '').toLowerCase().includes('avion') ? 0 : 1;
          const avB = (b.title || '').toLowerCase().includes('avion') ? 0 : 1;
          if (avA !== avB) return avA - avB;
          const lenA = Math.abs((a.length_ft || 99) - targetLen);
          const lenB = Math.abs((b.length_ft || 99) - targetLen);
          if (lenA !== lenB) return lenA - lenB;
          return (b.price_usd || 0) - (a.price_usd || 0);
        }});
      }}
      return list.slice().sort((a, b) => {{
        if (searchKeywords.length) {{
          const ka = keywordMatchStrength(a);
          const kb = keywordMatchStrength(b);
          if (ka !== kb) return kb - ka;
        }}
        if (listSort === 'title') {{
          return (a.title || '').localeCompare(b.title || '', undefined, {{ sensitivity: 'base' }});
        }}
        if (trip) {{
          const sa = isSavedForTrip(a.id, trip.id) ? 0 : 1;
          const sb = isSavedForTrip(b.id, trip.id) ? 0 : 1;
          if (sa !== sb) return sa - sb;
        }}
        const seenA = itemIsNew(a) ? 0 : 1;
        const seenB = itemIsNew(b) ? 0 : 1;
        if (seenA !== seenB) return seenA - seenB;
        if (listSort === 'new') {{
          return (b.score || 0) - (a.score || 0);
        }}
        if (DATA.vertical === 'vehicles') {{
          const prefA = a.make_preferred ? 0 : 1;
          const prefB = b.make_preferred ? 0 : 1;
          if (prefA !== prefB) return prefA - prefB;
          const fitA = a.fit_score || 0;
          const fitB = b.fit_score || 0;
          if (fitA !== fitB) return fitB - fitA;
        }}
        if (a.is_priority !== b.is_priority) {{
          return (b.is_priority ? 1 : 0) - (a.is_priority ? 1 : 0);
        }}
        return (b.score || 0) - (a.score || 0);
      }});
    }}

    function escapeHtml(s) {{
      return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }}

    function renderUpcomingNav() {{
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      const trips = sortTripsByDate(getTrips().filter(t => {{
        if (!t.end) return false;
        return new Date(t.end + 'T23:59:59') >= today;
      }}));
      const el = document.getElementById('upcoming-trip-links');
      if (!trips.length) {{
        el.innerHTML = '<div class="ut-empty">No upcoming trips — add one below</div>';
        return;
      }}
      el.innerHTML = trips.map(t => {{
        const active = selectedTripId === t.id ? ' active' : '';
        const nSaved = countSavedForTrip(t.id);
        const tags = searchTagsForTrip(t.id);
        const meta = [
          nSaved ? `${{nSaved}} saved` : '',
          tags ? '🔍 ' + tags : '',
        ].filter(Boolean).join(' · ');
        return `<a href="#trip=${{encodeURIComponent(t.id)}}" class="upcoming-trip-link${{active}}"
          data-trip="${{t.id}}">
          <span class="ut-name">${{escapeHtml(t.name)}}</span>
          <span class="ut-date">${{escapeHtml(t.date_label || t.start || '')}}</span>
          ${{meta ? `<span class="ut-meta">${{escapeHtml(meta)}}</span>` : ''}}
        </a>`;
      }}).join('');
    }}

    function renderTripsPanel() {{
      const trips = sortTripsByDate(getTrips());
      const sel = document.getElementById('trip-filter-select');
      const prev = sel.value;
      sel.innerHTML = '<option value="">All listings</option>' +
        trips.map(t =>
          `<option value="${{t.id}}">${{escapeHtml(t.name)}} (${{escapeHtml(t.date_label || t.start || '')}})</option>`
        ).join('');
      sel.value = selectedTripId || prev || '';

      document.getElementById('trip-show-saved').checked = tripShowSaved;
      document.getElementById('trip-show-route').checked = tripShowRoute;

      const saveSearchBtn = document.getElementById('save-search-trip');
      saveSearchBtn.style.display = selectedTripId ? 'block' : 'none';

      const list = document.getElementById('trips-list');
      const editing = document.activeElement && list.contains(document.activeElement);
      if (!editing) {{
        list.innerHTML = trips.map(t => {{
          const active = selectedTripId === t.id ? ' active' : '';
          const nSaved = countSavedForTrip(t.id);
          const tags = searchTagsForTrip(t.id);
          return `<div class="trip-row${{active}}" data-id="${{t.id}}">
            <input class="trip-name" value="${{escapeHtml(t.name)}}" placeholder="Trip name">
            <input class="trip-city" value="${{escapeHtml(t.city || '')}}" placeholder="Destination city (for route)">
            <div class="trip-row-dates">
              <input type="date" class="trip-start" value="${{t.start || ''}}">
              <input type="date" class="trip-end" value="${{t.end || ''}}">
            </div>
            ${{tags ? `<div class="trip-search-tags">🔍 ${{escapeHtml(tags)}}</div>` : ''}}
            <div class="trip-row-meta">
              <span>${{nSaved}} saved · ${{escapeHtml(t.date_label || '')}}</span>
              <button type="button" class="trip-save-search trip-save-row" data-trip="${{t.id}}">💾</button>
              <button type="button" class="trip-delete" title="Delete trip">×</button>
            </div>
          </div>`;
        }}).join('');
      }}

      const routeBox = document.getElementById('route-filter-box');
      routeBox.style.opacity = selectedTripId ? '0.55' : '1';
    }}

    function bindTripsPanelOnce() {{
      if (bindTripsPanelOnce.done) return;
      bindTripsPanelOnce.done = true;

      document.getElementById('trip-filter-select').onchange = (e) => {{
        activateTrip(e.target.value);
      }};
      document.getElementById('save-search-trip').onclick = () => {{
        if (selectedTripId) saveSearchForTrip(selectedTripId);
      }};
      document.getElementById('trip-show-saved').onchange = (e) => {{
        tripShowSaved = e.target.checked;
        saveFilterState();
        render();
      }};
      document.getElementById('trip-show-route').onchange = (e) => {{
        tripShowRoute = e.target.checked;
        saveFilterState();
        render();
      }};
      document.getElementById('trip-add').onclick = () => {{
        const trips = getTrips();
        const id = 'trip-' + Date.now();
        trips.push({{
          id, name: 'New trip', city: '', start: '', end: '', date_label: '', notes: ''
        }});
        saveTrips(trips);
        selectedTripId = id;
        saveFilterState();
        render();
      }};

      document.getElementById('trips-list').addEventListener('input', (e) => {{
        const row = e.target.closest('.trip-row');
        if (!row) return;
        const trips = getTrips();
        const t = trips.find(x => x.id === row.dataset.id);
        if (!t) return;
        if (e.target.classList.contains('trip-name')) t.name = e.target.value;
        if (e.target.classList.contains('trip-city')) t.city = e.target.value;
        if (e.target.classList.contains('trip-start')) t.start = e.target.value;
        if (e.target.classList.contains('trip-end')) t.end = e.target.value;
        refreshTripDateLabel(t);
        saveTrips(trips);
      }});
      document.getElementById('trips-list').addEventListener('change', () => {{
        renderTripsPanel();
        renderListings();
      }});
      document.getElementById('trips-list').addEventListener('click', (e) => {{
        const saveBtn = e.target.closest('.trip-save-row');
        if (saveBtn) {{
          e.stopPropagation();
          saveSearchForTrip(saveBtn.dataset.trip);
          return;
        }}
        const btn = e.target.closest('.trip-delete');
        if (btn) {{
          const row = btn.closest('.trip-row');
          if (!row || !confirm('Delete this trip? Saved items keep their tag.')) return;
          const trips = getTrips().filter(t => t.id !== row.dataset.id);
          saveTrips(trips);
          const searches = getTripSearches();
          delete searches[row.dataset.id];
          saveTripSearches(searches);
          if (selectedTripId === row.dataset.id) selectedTripId = '';
          saveFilterState();
          updateTripHash();
          render();
          return;
        }}
        const row = e.target.closest('.trip-row');
        if (row && !e.target.matches('input')) {{
          activateTrip(row.dataset.id);
        }}
      }});

      document.getElementById('upcoming-trip-links').addEventListener('click', (e) => {{
        const link = e.target.closest('.upcoming-trip-link');
        if (!link) return;
        e.preventDefault();
        activateTrip(link.dataset.trip);
      }});

      window.addEventListener('hashchange', () => {{
        applyTripFromHash();
        if (selectedTripId) applyTripSearch(selectedTripId);
        saveFilterState();
        render();
      }});
    }}

    function render(opts) {{
      opts = opts || {{}};
      const scrollY = opts.preserveScroll ? window.scrollY : null;
      bindTripsPanelOnce();
      bindMarketTabsOnce();
      renderMarketChrome();
      if (opts.listOnly) {{
        renderListings();
      }} else {{
        if (DATA.vertical !== 'vehicles') {{
          renderUpcomingNav();
          renderTripsPanel();
        }}
        renderWebMarketplaces();
        renderQuickSearchBar();
        bindQuickSearchOnce();
        renderListings();
      }}
      if (scrollY !== null) {{
        requestAnimationFrame(() => window.scrollTo(0, scrollY));
      }}
    }}

    function sourceLabelForId(srcId) {{
      for (const g of (DATA.filter_groups || [])) {{
        const opt = (g.options || []).find(o => o.id === srcId);
        if (opt) return opt.label;
      }}
      return String(srcId || '').replace('src:', '');
    }}

    function clearActiveFilterChip(key) {{
      if (key === 'free') setFreeOnly(false);
      else if (key === 'seen') setHideSeen(false);
      else if (key === 'route') {{
        routeDest = '';
        syncRouteInput();
        saveFilterState();
        render();
      }} else if (key === 'quick') {{
        applyQuickSearch(null);
      }} else if (key === 'trip') {{
        tripShowSaved = false;
        tripShowRoute = false;
        saveFilterState();
        render();
      }} else if (key === 'vloc') {{
        vehicleLocQuery = '';
        saveFilterState();
        render();
      }} else if (key && key.startsWith('kw:')) {{
        const i = parseInt(key.slice(3), 10);
        if (!Number.isNaN(i)) {{
          searchKeywords = searchKeywords.filter((_, j) => j !== i);
          saveFilterState();
          render();
        }}
      }} else if (key && key.startsWith('hideSrc:')) {{
        hiddenSources.delete(key.slice(8));
        saveFilterState();
        render();
      }} else if (key && key.startsWith('inc:')) {{
        includeFilters.delete(key.slice(4));
        saveFilterState();
        render();
      }}
    }}

    function renderActiveFilterChips() {{
      const el = document.getElementById('active-filter-chips');
      if (!el) return;
      const chips = [];
      if (freeOnly) chips.push({{ key: 'free', label: 'Free only' }});
      if (hideSeen) chips.push({{ key: 'seen', label: 'Hide seen' }});
      if (routeDest.trim()) chips.push({{ key: 'route', label: 'Route: ' + routeDest.trim() }});
      if (activeQuickSearchId) {{
        const p = presetById(activeQuickSearchId) || getQuickRecent().find(r => r.id === activeQuickSearchId);
        chips.push({{ key: 'quick', label: (p && p.label) || activeQuickSearchId }});
      }}
      searchKeywords.forEach((kw, i) => chips.push({{ key: 'kw:' + i, label: '"' + kw + '"' }}));
      hiddenSources.forEach(src => chips.push({{ key: 'hideSrc:' + src, label: '− ' + sourceLabelForId(src) }}));
      includeFilters.forEach(id => chips.push({{ key: 'inc:' + id, label: id }}));
      if (tripShowSaved || tripShowRoute) {{
        const parts = [];
        if (tripShowSaved) parts.push('saved');
        if (tripShowRoute) parts.push('route');
        chips.push({{ key: 'trip', label: 'Trip: ' + parts.join(' + ') }});
      }}
      if (DATA.vertical === 'vehicles' && vehicleLocQuery.trim()) {{
        chips.push({{ key: 'vloc', label: 'Loc: ' + vehicleLocQuery.trim() }});
      }}
      if (!chips.length) {{
        el.classList.add('hidden');
        el.innerHTML = '';
        return;
      }}
      el.classList.remove('hidden');
      el.innerHTML = chips.map(c =>
        `<button type="button" class="active-filter-chip" data-chip-key="${{escapeHtml(c.key)}}">` +
        `${{escapeHtml(c.label)}}<span aria-hidden="true">×</span></button>`
      ).join('');
      if (!el._bound) {{
        el._bound = true;
        el.onclick = (e) => {{
          const btn = e.target.closest('.active-filter-chip');
          if (!btn) return;
          clearActiveFilterChip(btn.dataset.chipKey);
        }};
      }}
    }}

    function renderListings() {{
      const footer = [DATA.branding?.wordmark || DATA.app || 'Skout', DATA.profile_id || ''].filter(Boolean).join(' · ');
      const org = DATA.branding?.footer_org || 'Gilded Goose Limited';
      const footEl = document.getElementById('footer');
      const meta = DATA.public_url ? footer + ' · ' + DATA.public_url : footer;
      footEl.innerHTML = escapeHtml(meta) + '<span class="gg-org">' + escapeHtml(org) + '</span>';

      const s = DATA.stats;
      const savedCount = Object.keys(getSaved()).length;
      const trip = getSelectedTrip();
      const feed = activeFeedList();
      const isSellMode = DATA.market_tabs_enabled && marketMode === 'sell';
      const shownPreview = sortShownList(feed.filter(matchesFilter), trip);

      const sourceCountsEl = document.getElementById('source-counts');
      if (sourceCountsEl && !isSellMode && (DATA.vertical === 'vehicles' || DATA.trailer_hunt) && (DATA.source_counts || []).length) {{
        sourceCountsEl.classList.remove('hidden');
        sourceCountsEl.innerHTML =
          '<div class="source-counts-title">Listings by source</div>' +
          (DATA.source_counts || []).map(sc => {{
            const zero = !sc.count ? ' zero' : '';
            return `<div class="source-count${{zero}}" title="${{escapeHtml(sc.label)}}">
              <b>${{sc.count}}</b><span>${{sc.icon}} ${{escapeHtml(sc.label)}}</span></div>`;
          }}).join('');
      }} else if (sourceCountsEl) {{
        sourceCountsEl.classList.add('hidden');
      }}

      let statsHtml;
      if (isSellMode) {{
        const cs = DATA.comp_stats || {{}};
        statsHtml =
          `<div class="stat"><b>${{shownPreview.length}}</b><span>showing</span></div>` +
          `<div class="stat"><b>${{feed.filter(itemIsNew).length}}</b><span>new</span></div>` +
          `<div class="stat"><b>${{feed.length}}</b><span>comps</span></div>`;
        if (cs.median_usd) {{
          statsHtml += `<div class="stat"><b>${{formatUsd(cs.median_usd)}}</b><span>median</span></div>`;
        }}
        if (cs.priced_count) {{
          statsHtml += `<div class="stat"><b>${{cs.above_floor_count || 0}}</b><span>≥ $26k floor</span></div>`;
        }}
      }} else {{
        statsHtml =
          `<div class="stat"><b>${{shownPreview.length}}</b><span>showing</span></div>` +
          `<div class="stat"><b>${{feed.filter(itemIsNew).length}}</b><span>new</span></div>` +
          `<div class="stat"><b>${{savedCount}}</b><span>saved</span></div>` +
          `<div class="stat"><b>${{s.checked}}</b><span>checked</span></div>` +
          `<div class="stat"><b>${{feed.length}}</b><span>in feed</span></div>`;
        if (s.duplicates_removed > 0) {{
          statsHtml += `<div class="stat"><b>${{s.duplicates_removed}}</b><span>dupes merged</span></div>`;
        }}
        if (trip && DATA.vertical !== 'vehicles') {{
          const nSaved = countSavedForTrip(trip.id);
          const nRoute = trip.city
            ? feed.filter(i => locationOnRouteTo(i.location, trip.city)).length
            : 0;
          statsHtml +=
            `<div class="stat"><b>${{nSaved}}</b><span>saved · ${{trip.name}}</span></div>` +
            `<div class="stat"><b>${{nRoute}}</b><span>on route</span></div>`;
        }}
      }}
      document.getElementById('stats').innerHTML = statsHtml;

      const channels = DATA.channel_stats || [];
      const channelBar = document.getElementById('channel-bar');
      if (channelBar) {{
        channelBar.innerHTML = channels.length
          ? channels.map(ch => {{
              const icon = channelStatusIcon(ch);
              const detail = channelStatusDetail(ch);
              return `<span class="channel-pill ${{ch.status}}" title="${{escapeHtml(detail)}}">
                <span class="ch-status">${{icon}}</span>${{ch.icon}} ${{escapeHtml(ch.label)}}
                ${{ch.showing > 0 || ch.fetched > 0 ? ch.showing + '/' + ch.fetched : 'setup'}}
              </span>`;
            }}).join('')
          : '<span class="channel-pill setup"><span class="ch-status">○</span>No sources configured</span>';
      }}

      const hasCl = !isSellMode && (feed || []).some(i => (i.source || '').startsWith('craigslist'));
      const clWarn = document.getElementById('cl-warning');
      if (clWarn) {{
        const dismissed = sessionStorage.getItem('skout_cl_warn_dismissed') === '1';
        clWarn.classList.toggle('hidden', !hasCl || dismissed);
      }}

      const imgStats = DATA.image_stats || {{}};
      const healthEl = document.getElementById('image-health');
      if (healthEl) {{
        const pct = imgStats.total
          ? Math.round((imgStats.with_photo / imgStats.total) * 100) : 0;
        healthEl.textContent = imgStats.total
          ? `${{imgStats.with_photo}}/${{imgStats.total}} photos (${{pct}}%)`
          : '';
        healthEl.classList.toggle('warn', pct < 50 && imgStats.total > 0);
      }}

      document.getElementById('platform-recs').innerHTML =
        DATA.vertical === 'vehicles'
          ? '<div class="platform-rec"><span>Feed: Craigslist · Facebook · OfferUp · <b>AutoTempest</b> (Cars.com, TrueCar, eBay, …). Sidebar has direct Autotrader/CarGurus links.</span></div>'
          : ((DATA.recommended_platforms || []).map(p =>
          `<div class="platform-rec">
            <b>${{p.icon}} ${{escapeHtml(p.name)}}<span class="tag">${{escapeHtml(p.status)}}</span></b>
            <span>${{escapeHtml(p.why)}}</span>
          </div>`
        ).join('') || '<div class="platform-rec"><span>All configured sources enabled.</span></div>');

      const routeInput = document.getElementById('route-dest');
      routeInput.value = routeDest;
      routeInput.oninput = () => {{
        routeDest = routeInput.value;
        saveFilterState();
        render();
      }};
      const dl = document.getElementById('route-city-list');
      dl.innerHTML = (DATA.route_city_hints || [])
        .map(c => `<option value="${{c}}">`).join('');

      document.getElementById('exclude-keywords').innerHTML =
        (DATA.exclude_title_keywords || []).map(kw => {{
          const id = 'ek-' + kw.replace(/[^a-z0-9]+/gi, '-');
          const on = hiddenKeywords.has(kw.toLowerCase());
          return `<label><input type="checkbox" data-kw="${{kw}}" id="${{id}}" ${{on ? 'checked' : ''}}>
            <span>${{escapeHtml(kw)}}</span></label>`;
        }}).join('');
      document.querySelectorAll('#exclude-keywords input').forEach(input => {{
        input.onchange = () => {{
          const kw = input.dataset.kw.toLowerCase();
          if (input.checked) hiddenKeywords.add(kw); else hiddenKeywords.delete(kw);
          saveFilterState();
          render();
        }};
      }});

      const yamlSet = new Set((DATA.exclude_title_keywords || []).map(k => k.toLowerCase()));
      const custom = [...hiddenKeywords].filter(k => !yamlSet.has(k)).sort();
      document.getElementById('exclude-kw-custom').innerHTML = custom.map(kw =>
        `<span class="exclude-kw-chip">${{escapeHtml(kw)}}
          <button type="button" data-rm-kw="${{escapeHtml(kw)}}" aria-label="Remove">×</button></span>`
      ).join('');
      document.querySelectorAll('#exclude-kw-custom button').forEach(btn => {{
        btn.onclick = () => {{
          hiddenKeywords.delete(btn.dataset.rmKw.toLowerCase());
          saveFilterState();
          render();
        }};
      }});

      const kwInput = document.getElementById('exclude-kw-input');
      const kwAddBtn = document.getElementById('exclude-kw-add');
      kwAddBtn.onclick = () => {{
        const raw = (kwInput.value || '').trim().toLowerCase();
        if (!raw) return;
        raw.split(/[,;]+/).map(s => s.trim()).filter(Boolean).forEach(k => hiddenKeywords.add(k));
        kwInput.value = '';
        saveFilterState();
        render();
      }};
      kwInput.onkeydown = (e) => {{
        if (e.key === 'Enter') {{ e.preventDefault(); kwAddBtn.click(); }}
      }};

      function renderFilterOption(group, opt) {{
        const id = opt.id;
        let checked;
        if (group.exclude) {{
          checked = group.id === 'source' ? !hiddenSources.has(id) : !hiddenCats.has(id);
        }} else {{
          checked = includeFilters.has(id);
        }}
        const inputId = 'f-' + group.id + '-' + id.replace(/[^a-z0-9]+/gi, '-');
        return `<label for="${{inputId}}">
          <input type="checkbox" id="${{inputId}}" data-group="${{group.id}}"
            data-filter="${{id}}" data-exclude="${{group.exclude ? '1' : '0'}}"
            ${{checked ? 'checked' : ''}}>
          <span>${{opt.label}}</span>
        </label>`;
      }}

      const groupsHtml = (DATA.filter_groups || []).map(group => {{
        let inner = '';
        if (group.families) {{
          inner = group.families.map(fam => {{
            const opts = fam.options.map(opt => renderFilterOption(group, opt)).join('');
            return `<div class="filter-family">
              <div class="filter-family-name">${{fam.label}}</div>
              ${{opts}}
            </div>`;
          }}).join('');
        }} else if (group.options) {{
          inner = group.options.map(opt => renderFilterOption(group, opt)).join('');
        }}
        const hint = group.hint ? `<div class="filter-hint">${{group.hint}}</div>` : '';
        return `<details class="filter-group" open>
          <summary>${{group.label}}</summary>
          ${{hint}}
          <div class="filter-options">${{inner}}</div>
        </details>`;
      }}).join('');
      document.getElementById('filter-groups').innerHTML = groupsHtml;
      document.querySelectorAll('.filter-options input').forEach(input => {{
        input.onchange = () => {{
          const id = input.dataset.filter;
          if (input.dataset.exclude === '1') {{
            if (input.dataset.group === 'source') {{
              if (input.checked) hiddenSources.delete(id); else hiddenSources.add(id);
            }} else {{
              if (input.checked) hiddenCats.delete(id); else hiddenCats.add(id);
            }}
          }} else {{
            if (input.checked) includeFilters.add(id); else includeFilters.delete(id);
          }}
          saveFilterState();
          render();
        }};
      }});

      document.getElementById('clear-filters').onclick = () => {{
        includeFilters.clear();
        hiddenCats.clear();
        hiddenSources.clear();
        defaultHiddenCats().forEach(id => hiddenCats.add(id));
        hiddenKeywords.clear();
        (DATA.exclude_title_keywords || []).forEach(k => hiddenKeywords.add(k.toLowerCase()));
        routeDest = '';
        selectedTripId = '';
        tripShowSaved = false;
        tripShowRoute = false;
        vehicleLocQuery = '';
        freeOnly = false;
        activeQuickSearchId = '';
        searchKeywords = [];
        saveFilterState();
        syncFreeOnlyUi();
        syncHideSeenUi();
        renderQuickSearchBar();
        updateTripHash();
        render();
      }};

      syncFreeOnlyUi();
      ['free-only', 'free-only-sidebar'].forEach(id => {{
        const el = document.getElementById(id);
        if (!el || el._bound) return;
        el._bound = true;
        el.onchange = () => setFreeOnly(el.checked);
      }});

      syncHideSeenUi();
      ['hide-seen', 'hide-seen-toolbar'].forEach(id => {{
        const el = document.getElementById(id);
        if (!el || el._bound) return;
        el._bound = true;
        el.onchange = () => setHideSeen(el.checked);
      }});

      const tripBanner = document.getElementById('trip-filter-banner');
      if (tripBanner && DATA.vertical !== 'vehicles' && !isSellMode) {{
        const narrowing = trip && (tripShowSaved || tripShowRoute);
        if (narrowing && shownPreview.length < feed.length) {{
          tripBanner.classList.remove('hidden');
          tripBanner.innerHTML =
            `Trip filter — showing ${{shownPreview.length}} of ${{feed.length}}.` +
            `<button type="button" id="trip-show-all">Show all</button>`;
          document.getElementById('trip-show-all').onclick = () => {{
            tripShowSaved = false;
            tripShowRoute = false;
            saveFilterState();
            render();
          }};
        }} else {{
          tripBanner.classList.add('hidden');
        }}
      }}

      let shown = shownPreview;
      let activeCount = includeFilters.size + hiddenCats.size;
      if (hideSeen) activeCount += 1;
      if (freeOnly) activeCount += 1;
      if (searchKeywords.length) activeCount += 1;
      if (hiddenSources.size) activeCount += 1;
      if (DATA.vertical !== 'vehicles' && (tripShowSaved || tripShowRoute)) activeCount += 1;
      if (DATA.vertical === 'vehicles' && vehicleLocQuery.trim()) activeCount += 1;
      let countLabel = `${{shown.length}} showing`;
      if (trip && DATA.vertical !== 'vehicles') {{
        const parts = [];
        if (tripShowSaved) parts.push(countSavedForTrip(trip.id) + ' saved');
        if (tripShowRoute && trip.city) {{
          parts.push(shown.filter(i => !isSavedForTrip(i.id, trip.id)).length + ' on route');
        }}
        if (parts.length) countLabel += ' · ' + parts.join(', ');
      }}
      document.getElementById('filter-count').textContent =
        activeCount ? `${{activeCount}} filter(s) active · ${{countLabel}}` : countLabel;
      const vehLoc = document.getElementById('vehicle-loc-query');
      if (vehLoc && DATA.vertical === 'vehicles') {{
        vehLoc.value = vehicleLocQuery;
        if (!vehLoc._bound) {{
          vehLoc._bound = true;
          vehLoc.oninput = () => {{
            vehicleLocQuery = vehLoc.value;
            saveFilterState();
            render({{ listOnly: true, preserveScroll: true }});
          }};
        }}
      }}
      if (!shown.length) {{
        let hint = 'Try clearing filters or broadening your search.';
        if (trip && (tripShowSaved || tripShowRoute)) {{
          hint = `No listings match trip filter for ${{trip.name}}. Clear trip filters or show all.`;
        }} else if (feed.length) {{
          hint = `${{feed.length}} in feed but filters hide them. <button type="button" id="empty-clear-filters">Clear all filters</button>`;
        }}
        document.getElementById('list').innerHTML =
          `<div class="empty-state">${{EMPTY_MARK_SVG}}` +
          `<p class="empty-title">No matches</p>` +
          `<p class="empty-hint">${{hint}}</p></div>`;
        const clearBtn = document.getElementById('empty-clear-filters');
        if (clearBtn) clearBtn.onclick = () => document.getElementById('clear-filters').click();
        renderActiveFilterChips();
        return;
      }}
      renderActiveFilterChips();
      document.getElementById('list').innerHTML = shown.map((item, idx) => {{
        const imgs = listingImages(item);
        const mediaClass = 'tile-media' + (imgs.length ? ' loading' : '');
        const thumb = imgs.length
          ? `<img src="${{escapeHtml(imgs[0])}}" alt="" loading="lazy"${{photoReferrerAttr(imgs[0])}}
              data-src="${{escapeHtml(imgs[0])}}" data-icon="${{escapeHtml(item.category_icon)}}"
              onload="this.classList.add('loaded'); this.closest('.tile-media').classList.add('loaded');"
              onerror="window.skoutImgError(this)">`
          : `<span class="placeholder">${{showIcons ? item.category_icon : '·'}}</span>`;
        const newBadge = itemIsNew(item) ? '<span class="tile-new">NEW</span>' : '';
        const prefStar = item.make_preferred
          ? `<span class="tile-badge tile-pref-star" title="Preferred: ${{escapeHtml(item.preferred_match || (DATA.make_preference || {{}}).label || 'your models')}}">⭐</span>`
          : '';
        const vintageBadge = item.is_vintage_square
          ? `<span class="tile-badge" title="${{item.is_vintage_quality ? 'Vintage square · quality signals' : 'Vintage / square body'}}">🛻</span>`
          : '';
        const multiBadge = imgs.length > 1
          ? `<span class="tile-badge" title="${{imgs.length}} photos">${{showIcons ? '🖼 ' : ''}}${{imgs.length}}</span>` : '';
        let tripTag = '';
        let tripClass = '';
        if (DATA.vertical === 'vehicles') {{
          if (isSaved(item.id)) tripTag = '<span class="trip-tag">Shortlist</span>';
        }} else if (trip) {{
          const savedFor = isSavedForTrip(item.id, trip.id);
          const onRoute = trip.city && locationOnRouteTo(item.location, trip.city);
          if (savedFor) {{
            tripClass = 'trip-saved';
            tripTag = '<span class="trip-tag">Saved for trip</span>';
          }} else if (onRoute) {{
            tripClass = 'trip-route-only';
            tripTag = '<span class="trip-tag">On route</span>';
          }}
        }} else if (isSaved(item.id)) {{
          const sv = getSaved()[item.id];
          tripTag = `<span class="trip-tag">${{escapeHtml(sv.tripName || 'Saved')}}</span>`;
        }}
        const tileClass = [
          'tile',
          item.is_priority ? 'priority' : '',
          !item.is_free ? 'paid' : '',
          isSaved(item.id) ? 'saved-pin' : '',
          !itemIsNew(item) ? 'post-seen' : '',
          item.make_preferred ? 'chev-pref' : '',
          isCompareSelected(item.id) ? 'compare-on' : '',
          tripClass,
        ].filter(Boolean).join(' ');
        const priceClass = item.is_free ? 'tile-price' : 'tile-price paid-price';
        const loc = (item.location || '').split('(')[0].trim();
        let badgeHtml = worthTripBadge(item, trip);
        if (isSellMode) {{
          const cls = (item.comp_vs_floor != null && item.comp_vs_floor >= 0) ? 'comp-above' : 'comp-below';
          const lenTag = item.length_ft ? ` · ${{item.length_ft}}′` : '';
          const label = item.comp_label || (item.price_usd ? formatUsd(item.price_usd) : 'Comp');
          badgeHtml = `<span class="comp-badge ${{cls}}">${{escapeHtml(label)}}${{lenTag}}</span>`;
        }}
        const openT = listingOpenTarget(item);
        const alsoOn = alsoOnHtml(item);
        const compareChk = (!isSellMode && DATA.compare_export_enabled)
          ? `<label class="tile-compare" title="Export to Excel compare sheet">
              <input type="checkbox" data-compare="${{idx}}" ${{isCompareSelected(item.id) ? 'checked' : ''}}> Compare
            </label>` : '';
        const excludeChk = `<label class="tile-exclude" title="Always hide this listing">
              <input type="checkbox" data-exclude="${{idx}}" ${{isPermExcluded(item) ? 'checked' : ''}}> Hide
            </label>`;
        const dateLine = item.is_estate_sale && item.sale_dates
          ? `<p class="tile-dates">${{escapeHtml(formatSaleDates(item.sale_dates))}}</p>` : '';
        const saveLabel = DATA.vertical === 'vehicles' ? 'Save' : 'Save';
        const saveBtnLabel = showIcons ? '📌' : '★';
        const emailBtnLabel = showIcons ? '✉' : '@';
        return `<article class="${{tileClass}}">
          <div class="tile-media-wrap">
            <button type="button" class="${{mediaClass}}" data-idx="${{idx}}" aria-label="View details">${{thumb}}</button>
            ${{newBadge}}
            <div class="tile-badges">
              ${{prefStar}}
              ${{vintageBadge}}
              ${{multiBadge}}
            </div>
            <button type="button" class="tile-save ${{isSaved(item.id) ? 'saved' : ''}}" data-save="${{idx}}"
              aria-label="${{DATA.vertical === 'vehicles' ? 'Save to shortlist' : 'Save for trip'}}">${{saveBtnLabel}}</button>
            <button type="button" class="tile-email" data-email="${{idx}}" aria-label="Copy seller email">${{emailBtnLabel}}</button>
            ${{excludeChk}}
            ${{compareChk}}
          </div>
          <div class="tile-body">
            <p class="${{priceClass}}">${{item.price}}</p>
            ${{dateLine}}
            <h3 class="tile-title"><a href="${{openT.url || item.url}}" target="_blank" rel="noopener noreferrer"
              onclick="return skoutOpenListing(event, '${{escapeHtml(openT.source || item.source)}}')">${{item.title}}</a></h3>
            <p class="tile-meta">${{item.platform}} · ${{loc}} ${{tripTag}}</p>
            ${{badgeHtml}}
            ${{alsoOn}}
          </div>
        </article>`;
      }}).join('');

      document.querySelectorAll('.tile-media').forEach(btn => {{
        const item = shown[parseInt(btn.dataset.idx, 10)];
        const imgs = listingImages(item);
        btn.onmouseenter = () => startTileHoverCycle(btn, imgs, item.category_icon || '');
        btn.onmouseleave = () => stopTileHoverCycle(btn);
        btn.onclick = (e) => {{
          if (e.target.closest('.tile-save') || e.target.closest('.tile-exclude')) return;
          stopTileHoverCycle(btn);
          e.preventDefault();
          openModal(item, shown, parseInt(btn.dataset.idx, 10));
        }};
      }});
      document.querySelectorAll('.tile-exclude input').forEach(box => {{
        box.onclick = (e) => e.stopPropagation();
        box.onchange = (e) => {{
          e.stopPropagation();
          const item = shown[parseInt(box.dataset.exclude, 10)];
          setPermExcluded(item, box.checked);
        }};
      }});
      document.querySelectorAll('.tile-save').forEach(btn => {{
        btn.onclick = (e) => {{
          e.stopPropagation();
          e.preventDefault();
          const item = shown[parseInt(btn.dataset.save, 10)];
          if (isSaved(item.id)) unsaveListing(item.id);
          else pickTripAndSave(item);
        }};
      }});
      document.querySelectorAll('.tile-email').forEach(btn => {{
        btn.onclick = async (e) => {{
          e.stopPropagation();
          e.preventDefault();
          await contactSeller(shown[parseInt(btn.dataset.email, 10)]);
        }};
      }});
      document.querySelectorAll('.tile-compare input').forEach(box => {{
        box.onclick = (e) => e.stopPropagation();
        box.onchange = (e) => {{
          e.stopPropagation();
          const item = shown[parseInt(box.dataset.compare, 10)];
          toggleCompareItem(item, box.checked);
          renderListings();
        }};
      }});
      updateCompareUi();
      auditTileImages();
    }}

    window.skoutImgError = function(img) {{
      if (!img.dataset.retried && img.dataset.src) {{
        img.dataset.retried = '1';
        const sep = img.dataset.src.includes('?') ? '&' : '?';
        img.src = img.dataset.src + sep + '_=' + Date.now();
        return;
      }}
      const icon = showIcons ? (img.dataset.icon || '·') : '·';
      const span = document.createElement('span');
      span.className = 'placeholder';
      span.textContent = icon;
      const wrap = img.closest('.tile-media');
      if (wrap) wrap.classList.remove('loading');
      img.replaceWith(span);
    }};

    function auditTileImages() {{
      const imgs = document.querySelectorAll('.tile-media img');
      let broken = 0;
      imgs.forEach(img => {{
        if (!img.complete || img.naturalWidth === 0) broken += 1;
      }});
      const healthEl = document.getElementById('image-health');
      if (!healthEl || !DATA.image_stats) return;
      const s = DATA.image_stats;
      const pct = s.total ? Math.round((s.with_photo / s.total) * 100) : 0;
      let label = `${{s.with_photo}}/${{s.total}} photos (${{pct}}%)`;
      if (broken) label += ` · ${{broken}} failed to load`;
      healthEl.textContent = label;
      healthEl.classList.toggle('warn', broken > 0 || (pct < 50 && s.total > 0));
    }}

    document.getElementById('toggle-filters').onclick = () => {{
      document.getElementById('sidebar').classList.toggle('collapsed');
    }};
    document.getElementById('reset-seen').onclick = () => {{
      localStorage.removeItem(manualSeenKey);
      allTrackedItems().forEach(item => {{
        if (item._was_new !== undefined) item.is_new = item._was_new;
      }});
      render();
      showToast('Cleared seen marks — new listings restored');
    }};
    document.getElementById('mark-all-seen').onclick = async () => {{
      const seen = getManualSeen();
      let n = 0;
      activeFeedList().forEach(item => {{
        const id = item.id || item.url;
        if (itemIsNew(item)) {{
          seen.add(id);
          item.is_new = false;
          n += 1;
        }}
      }});
      saveManualSeen(seen);
      render();
      const cmd = DATA.mark_seen_command || '.venv/bin/python src/main.py --mark-seen';
      try {{
        await navigator.clipboard.writeText(cmd);
        showToast(n
          ? `Marked ${{n}} as seen. Copied DB command — run in terminal to persist.`
          : 'All listings were already marked as seen.');
      }} catch (e) {{
        showToast(n ? `Marked ${{n}} as seen.` : 'All listings were already marked as seen.');
        prompt('Run this to persist for the next scan:', cmd);
      }}
    }};
    document.getElementById('rerun-all').onclick = async () => {{
      const cmd = DATA.rerun_command || '.venv/bin/python src/main.py --all --open';
      try {{
        await navigator.clipboard.writeText(cmd);
        showToast('Copied — run in terminal, then hard-refresh this page');
      }} catch (e) {{
        prompt('Run this to refresh all listings (including seen):', cmd);
      }}
    }};
    document.getElementById('cl-warning-dismiss').onclick = () => {{
      sessionStorage.setItem('skout_cl_warn_dismissed', '1');
      document.getElementById('cl-warning').classList.add('hidden');
    }};
    const exportBtn = document.getElementById('export-compare');
    if (exportBtn) exportBtn.onclick = () => exportCompareCsv();
    const clearCompareBtn = document.getElementById('clear-compare');
    if (clearCompareBtn) clearCompareBtn.onclick = () => {{
      localStorage.removeItem(compareKey);
      updateCompareUi();
      render();
      showToast('Cleared compare selection');
    }};
    (function applyHashQuickSearch() {{
      const m = (location.hash || '').match(/(?:^#|[?&])qs=([^&]+)/);
      if (!m) return;
      const id = decodeURIComponent(m[1]);
      const preset = presetById(id);
      if (!preset) return;
      freeOnly = false;
      hideSeen = false;
      // Nationwide: clear Pueblo / any route chip (Sewing preset is not route-bound)
      routeDest = '';
      tripShowRoute = false;
      applyQuickSearch(preset);
      routeDest = '';
      syncRouteInput();
      syncFreeOnlyUi();
      syncHideSeenUi();
      // Full opacity — don't ghost tiles that localStorage marked as seen
      DATA.listings.forEach(item => {{ item.is_new = true; }});
      saveFilterState();
    }})();
    render();
  </script>
</body>
</html>"""

    index_path = SITE_DIR / "index.html"
    index_path.write_text(html, encoding="utf-8")
    return index_path
