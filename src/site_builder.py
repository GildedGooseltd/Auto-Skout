import hashlib
import json
import re
import shutil
from datetime import date, datetime
from html import escape, unescape
from pathlib import Path
from typing import Optional

from date_format import format_range, trip_labels
from pickup_message import build_pickup_message
from route_matcher import match_destination, match_routes
from scrapers.craigslist import normalize_reply_url
from scoring import is_free_by_price, is_priority_match
from vehicle_fields import compute_vehicle_fit, is_vehicle_listing

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

RECOMMENDED_PLATFORMS = [
    {"id": "nextdoor", "name": "Nextdoor", "icon": "🏘", "status": "setup",
     "why": "API key or --login for For Sale & Free"},
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
                resp = session.get(url, timeout=12)
                if resp.ok and len(resp.content) > 400:
                    path.write_bytes(resp.content)
                    local.append(rel)
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
        if local:
            item["image_url"] = local[0]
            item["image_urls"] = local
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
            "contact_label": "Email seller",
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
        "also_on": list(getattr(listing, "also_on", None) or []),
        "year": vehicle.get("year", ""),
        "make": vehicle.get("make", ""),
        "model": vehicle.get("model", ""),
        "miles": vehicle.get("miles", ""),
        "make_preferred": vehicle.get("make_preferred", False),
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
    logo_128 = branding.get("logo_icon", "skout-dog-icon-128.png")
    logo_64 = branding.get("logo_favicon", "skout-dog-icon-64.png")
    wordmark = branding.get("wordmark") or app
    wordmark_parts = branding.get("wordmark_parts") or []
    if not wordmark_parts and wordmark:
        parts = wordmark.split(None, 1)
        wordmark_parts = [
            {"text": parts[0], "class": "logo-auto"},
            {"text": parts[1] if len(parts) > 1 else "", "class": "logo-skout"},
        ]
    template = cfg["scoring"].get("response_template", "").strip()
    loc_label = f"{loc.get('city', '')}, {loc.get('state', '')} {loc.get('zip', '')}"
    now = datetime.now().strftime("%a %b %-d, %-I:%M %p")
    public_url = deploy.get("public_url", "")

    search_cfg = cfg.get("search", {})
    today = date.today()

    def _trip_payload(trip: dict, index: int) -> dict:
        tid = re.sub(r"[^a-z0-9]+", "-", trip.get("name", "trip").lower()).strip("-") or "trip"
        tid = f"{tid}-{index}"
        labels = trip_labels(trip)
        return {
            "id": tid,
            "name": trip.get("name", ""),
            "city": trip.get("city", ""),
            "start": trip.get("start", ""),
            "end": trip.get("end", ""),
            "date_label": labels["date_label"],
            "notes": trip.get("notes", ""),
        }

    all_trips = [
        _trip_payload(t, i) for i, t in enumerate(cfg.get("travel", {}).get("trips", []))
    ]
    all_trips.sort(key=lambda t: (t.get("start") or "9999-12-31", t.get("end") or ""))
    upcoming_trips = [t for t in all_trips if t.get("end") and date.fromisoformat(t["end"]) >= today]

    new_url_set = new_urls

    active_trip_label = loc.get("name", "")
    if loc.get("start") and loc.get("end"):
        active_trip_label = f"{loc.get('name', '')} ({format_range(loc['start'], loc['end'])})"

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
        {"id": cat_id, "label": f"{cat['icon']} {cat['label']}"}
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
    listings = []
    for listing, score, tier in items:
        flags = match_routes(listing.location, routes_cfg)
        labels = _route_tag_labels(routes_cfg, flags)
        listings.append(_listing_dict(
            listing, score, tier, flags, labels, template, search=search_cfg,
            is_new=listing.url in new_url_set,
            profile_id=profile_id,
            vertical=vertical,
            shop_rules=profile.get("shop_rules") or {},
            home=profile.get("home") or {},
        ))
    listings.sort(key=lambda item: (
        0 if item.get("is_new") else 1,
        -int(item.get("fit_score") or 0) if vertical == "vehicles" else 0,
        0 if vertical != "vehicles" or item.get("make_preferred") else 1,
        -item["is_priority"],
        -item["score"],
        item["title"],
    ))

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
    source_filter_options = [
        {"id": "src:craigslist", "label": "🟠 Craigslist"},
        {"id": "src:freecycle", "label": "♻️ Freecycle"},
        {"id": "src:facebook", "label": "📘 Facebook"},
        {"id": "src:trash_nothing", "label": "🗑️ Trash Nothing"},
        {"id": "src:nextdoor", "label": "🏘️ Nextdoor"},
        {"id": "src:offerup", "label": "📱 OfferUp"},
        {"id": "src:buy_nothing", "label": "🎁 Buy Nothing"},
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
    }
    for key, label in source_labels.items():
        if platforms_cfg.get(key, {}).get("enabled") and label not in active_sources:
            active_sources.append(label)
    if trailer_hunt and platforms_cfg.get("web_marketplaces_scrape", {}).get("enabled"):
        if "Web marketplaces" not in active_sources:
            active_sources.append("Web marketplaces")
        source_filter_options.extend([
            {"id": "src:web:cars_com", "label": "🚗 Cars.com"},
            {"id": "src:web:truecar", "label": "💲 TrueCar"},
            {"id": "src:web:ebay_motors", "label": "🛒 eBay Motors"},
            {"id": "src:web:autotrader", "label": "🚙 Autotrader"},
            {"id": "src:web:cargurus", "label": "📊 CarGurus"},
        ])

    filter_groups = []
    if vertical == "vehicles":
        vehicle_source_opts = [
            {"id": "src:craigslist", "label": "🟠 Craigslist"},
            {"id": "src:facebook", "label": "📘 Facebook"},
            {"id": "src:offerup", "label": "📱 OfferUp"},
            {"id": "src:web:cars_com", "label": "🚗 Cars.com"},
            {"id": "src:web:truecar", "label": "💲 TrueCar"},
            {"id": "src:web:ebay_motors", "label": "🛒 eBay Motors"},
            {"id": "src:web:autotrader", "label": "🚙 Autotrader"},
            {"id": "src:web:cargurus", "label": "📊 CarGurus"},
            {"id": "src:auction:govdeals", "label": "🏛 GovDeals"},
            {"id": "src:auction:publicsurplus", "label": "📋 Public Surplus"},
            {"id": "src:auction:propertyroom", "label": "👮 PropertyRoom"},
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
                    {"id": "chevy_preferred", "label": "🟦 Chevy / GMC"},
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
                    {"id": "free_only", "label": "Free only (price)"},
                ],
                "priority": 1,
            },
        ]
        filter_groups.append({
            "id": "source",
            "label": "Source",
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
        "public_url": public_url,
        "exclude_categories": exclude_categories,
        "exclude_title_keywords": exclude_title_keywords,
        "route_city_hints": route_city_hints,
        "default_route_dest": profile.get("home", {}).get("default_route_dest")
            or profile.get("default_route_dest", "Pueblo"),
        "priority_keywords": search_cfg.get("priority_keywords", []),
        "upcoming_trips": upcoming_trips,
        "trips": all_trips,
        "active_sources": active_sources,
        "channel_stats": channel_stats,
        "source_counts": source_counts,
        "focus_trip_id": focus_trip_id,
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
    * {{ box-sizing: border-box; }}
    body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0; font-size: 16px;
      background: #f5f0e8; color: #1c1917; }}
    .layout {{ display: flex; min-height: 100vh; }}
    .sidebar {{ width: 240px; min-width: 240px; background: #fff; border-right: 1px solid #e7e5e4;
      position: sticky; top: 0; align-self: flex-start; max-height: 100vh; overflow-y: auto;
      z-index: 11; }}
    .sidebar-head {{ padding: .85rem 1rem; border-bottom: 1px solid #e7e5e4;
      background: linear-gradient(135deg, #3f6212, #166534); color: #fff; }}
    .brand-row {{ display: flex; gap: .65rem; align-items: center; }}
    .brand-icon {{ width: 56px; height: 56px; border-radius: 50%;
      border: 2px solid rgba(255,255,255,.45); flex-shrink: 0; object-fit: cover;
      object-position: center 20%; box-shadow: 0 2px 8px rgba(0,0,0,.2); }}
    .brand-text {{ min-width: 0; }}
    .logo-wordmark {{ margin: 0; font-size: 1.55rem; font-weight: 800; letter-spacing: -.02em;
      line-height: 1; display: flex; align-items: baseline; }}
    .logo-auto {{ color: #fff; }}
    .logo-skout {{ color: #bbf7d0; font-weight: 700; }}
    .sidebar-head .sub {{ opacity: .92; font-size: .78rem; margin-top: .25rem; line-height: 1.35; }}
    .content-toolbar {{ display: flex; flex-wrap: wrap; gap: .5rem; align-items: center;
      padding: .65rem 1rem 0; }}
    .toolbar-btn {{ padding: .55rem .85rem; border-radius: 8px; border: 1px solid #166534;
      background: #166534; color: #fff; font-size: .88rem; font-weight: 600; cursor: pointer; }}
    .toolbar-btn:hover {{ background: #14532d; }}
    .toolbar-btn.secondary {{ background: #fff; color: #44403c; border-color: #d6d3d1; font-weight: 500; }}
    .image-health {{ font-size: .78rem; color: #57534e; }}
    .image-health.warn {{ color: #b45309; }}
    .platform-recs {{ padding: .5rem 1rem 1rem; border-top: 1px solid #f5f5f4; }}
    .platform-recs summary {{ font-size: .82rem; font-weight: 600; cursor: pointer; color: #44403c; }}
    .platform-rec {{ padding: .45rem 0; border-bottom: 1px solid #f5f5f4; font-size: .8rem; }}
    .platform-rec:last-child {{ border-bottom: none; }}
    .platform-rec b {{ font-size: .85rem; }}
    .platform-rec span {{ display: block; color: #78716c; font-size: .75rem; margin-top: .1rem; }}
    .platform-rec .tag {{ display: inline-block; font-size: .62rem; text-transform: uppercase;
      letter-spacing: .03em; color: #166534; background: #f0fdf4; padding: .1rem .35rem;
      border-radius: 4px; margin-left: .25rem; }}
    .upcoming-nav {{ padding: .65rem 1rem .75rem; border-bottom: 1px solid #e7e5e4; background: #fafaf9; }}
    .upcoming-nav-title {{ font-size: .68rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: .04em; color: #78716c; margin-bottom: .4rem; }}
    .upcoming-trip-link {{ display: block; padding: .45rem .55rem; border-radius: 8px;
      text-decoration: none; color: #292524; border: 1px solid #e7e5e4; margin-bottom: .35rem;
      background: #fff; cursor: pointer; }}
    .upcoming-trip-link:hover {{ border-color: #166534; background: #f0fdf4; }}
    .upcoming-trip-link.active {{ border-color: #166534; background: #dcfce7; box-shadow: inset 0 0 0 1px #166534; }}
    .ut-name {{ display: block; font-size: .78rem; font-weight: 600; line-height: 1.25; }}
    .ut-date {{ display: block; font-size: .68rem; color: #78716c; margin-top: .1rem; }}
    .ut-meta {{ display: block; font-size: .62rem; color: #166534; margin-top: .15rem; }}
    .ut-empty {{ font-size: .72rem; color: #a8a29e; font-style: italic; }}
    .trip-save-search {{ margin-top: .35rem; width: 100%; font-size: .68rem; padding: .35rem;
      border: 1px solid #d6d3d1; border-radius: 6px; background: #fff; cursor: pointer; color: #44403c; }}
    .trip-save-search:hover {{ border-color: #166534; background: #f0fdf4; }}
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
    .filter-options input {{ margin-top: .15rem; accent-color: #166534; flex-shrink: 0; }}
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
    .trip-row.active {{ border-color: #166534; background: #f0fdf4; }}
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
    .trip-tag {{ font-size: .58rem; color: #166534; font-weight: 600; }}
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
    .tile.priority {{ box-shadow: 0 0 0 2px #ca8a04; }}
    .tile.paid {{ box-shadow: 0 0 0 2px #166534; }}
    .tile.saved-pin {{ outline: 2px solid #eab308; outline-offset: -2px; }}
    .modal {{ position: fixed; inset: 0; z-index: 100; display: flex; align-items: flex-start;
      justify-content: center; padding: 1rem 1rem 2rem; overflow-y: auto; }}
    .modal.hidden {{ display: none; }}
    .modal-backdrop {{ position: fixed; inset: 0; background: rgba(0,0,0,.55); }}
    .modal-panel {{ position: relative; background: #fff; width: 100%; max-width: 760px;
      max-height: none; overflow-y: visible; border-radius: 16px; padding: 1rem 1rem 1.5rem;
      z-index: 1; margin-top: 0; box-shadow: 0 12px 40px rgba(0,0,0,.25); }}
    .modal-panel img {{ width: 100%; max-height: 50vh; object-fit: contain; background: #f5f5f4;
      border-radius: 8px; margin-bottom: 0; display: block; }}
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
    .gallery-dot.active {{ background: #166534; }}
    .channel-bar-wrap {{ padding: .5rem 1rem 0; }}
    .channel-bar-title {{ font-size: .72rem; font-weight: 600; color: #78716c;
      text-transform: uppercase; letter-spacing: .04em; margin-bottom: .35rem; }}
    .channel-bar {{ display: flex; flex-wrap: wrap; gap: .35rem; }}
    .channel-pill {{ font-size: .72rem; padding: .25rem .55rem; border-radius: 999px;
      border: 1px solid #e7e5e4; background: #fafaf9; color: #44403c; }}
    .channel-pill.ok {{ border-color: #bbf7d0; background: #f0fdf4; }}
    .channel-pill.setup {{ border-color: #fde68a; background: #fffbeb; color: #92400e; }}
    .channel-pill.filtered {{ border-color: #fed7aa; background: #fff7ed; color: #9a3412; }}
    .channel-pill .ch-status {{ font-weight: 700; margin-right: .15rem; }}
    .worth-badge {{ display: inline-block; font-size: .68rem; font-weight: 600;
      padding: .15rem .45rem; border-radius: 6px; margin-top: .25rem; }}
    .worth-high {{ background: #dcfce7; color: #166534; }}
    .worth-medium {{ background: #fef9c3; color: #854d0e; }}
    .worth-low {{ background: #f5f5f4; color: #78716c; }}
    .fit-badge {{ display: inline-block; font-size: .68rem; font-weight: 700;
      padding: .15rem .45rem; border-radius: 6px; margin-top: .2rem; }}
    .fit-top {{ background: #dcfce7; color: #166534; }}
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
    .modal-close {{ position: absolute; top: .5rem; right: .75rem; font-size: 1.5rem;
      border: none; background: transparent; cursor: pointer; color: #78716c; }}
    .modal-panel h2 {{ margin: 0 0 .35rem; font-size: 1.25rem; line-height: 1.35; }}
    .modal-meta {{ font-size: .9rem; color: #78716c; margin: 0 0 .75rem; }}
    .modal-desc {{ font-size: 1rem; line-height: 1.55; white-space: pre-wrap; margin: 0 0 1rem; }}
    .modal-actions {{ display: flex; gap: .5rem; flex-wrap: wrap; }}
    .modal-actions button, .modal-actions a {{ padding: .5rem .85rem; border-radius: 8px;
      border: 1px solid #d6d3d1; background: #fff; font-size: .85rem; cursor: pointer;
      text-decoration: none; color: #1c1917; }}
    .modal-actions button.primary {{ background: #166534; color: #fff; border-color: #166534; }}
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
    .stat b {{ display: block; font-size: 1.5rem; color: #166534; }}
    .stat span {{ font-size: .78rem; color: #78716c; }}
    .source-counts {{ display: flex; gap: .4rem; padding: .65rem 1rem 0; flex-wrap: wrap;
      border-bottom: 1px solid #e7e5e4; background: #fff; }}
    .source-counts-title {{ width: 100%; font-size: .72rem; font-weight: 700; color: #44403c;
      text-transform: uppercase; letter-spacing: .04em; margin-bottom: .15rem; }}
    .source-count {{ background: #fff; border-radius: 8px; padding: .4rem .65rem; min-width: 72px;
      text-align: center; box-shadow: 0 1px 2px rgba(0,0,0,.06); border: 1px solid #e7e5e4; }}
    .source-count b {{ display: block; font-size: 1.35rem; color: #166534; line-height: 1.1; }}
    .source-count.zero b {{ color: #a8a29e; }}
    .source-count span {{ font-size: .68rem; color: #57534e; line-height: 1.2; }}
    #list {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: .55rem; padding: 0 1rem 1rem; }}
    @media (min-width: 900px) {{
      #list {{ grid-template-columns: repeat(auto-fill, minmax(155px, 1fr)); }}
    }}
    @media (min-width: 1200px) {{
      #list {{ grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); }}
    }}
    .tile {{ background: #fff; border-radius: 10px; overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,.08); display: flex; flex-direction: column;
      position: relative; transition: opacity .15s ease; }}
    .tile.post-seen {{ opacity: 0.4; }}
    .tile.post-seen:hover {{ opacity: 1; }}
    .tile-media-wrap {{ position: relative; }}
    .tile-media {{ position: relative; aspect-ratio: 1; background: #e7e5e4;
      display: flex; align-items: center; justify-content: center; overflow: hidden;
      cursor: pointer; border: none; padding: 0; width: 100%; font: inherit; }}
    .tile-media img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
    .tile-media .placeholder {{ font-size: 2rem; opacity: .4; }}
    .tile-badges {{ position: absolute; top: .3rem; left: .3rem; display: flex; gap: .2rem; }}
    .tile-badge {{ font-size: .85rem; background: rgba(255,255,255,.92); border-radius: 6px;
      padding: .1rem .25rem; line-height: 1; box-shadow: 0 1px 2px rgba(0,0,0,.12); }}
    .tile-save {{ position: absolute; top: .3rem; right: .3rem; width: 1.6rem; height: 1.6rem;
      border-radius: 50%; border: none; background: rgba(255,255,255,.92); cursor: pointer;
      font-size: .75rem; box-shadow: 0 1px 2px rgba(0,0,0,.15); }}
    .tile-save.saved {{ background: #166534; color: #fff; }}
    .tile-seen {{ position: absolute; bottom: .35rem; left: .35rem; width: 1.6rem; height: 1.6rem;
      border-radius: 50%; border: none; background: rgba(255,255,255,.92); cursor: pointer;
      font-size: .72rem; box-shadow: 0 1px 2px rgba(0,0,0,.15); color: #78716c; z-index: 2; }}
    .tile-seen.seen {{ background: #44403c; color: #fff; }}
    .tile-compare {{ position: absolute; bottom: .35rem; left: 2.1rem; z-index: 2;
      display: flex; align-items: center; gap: .2rem; font-size: .62rem; font-weight: 600;
      background: rgba(255,255,255,.94); border-radius: 6px; padding: .15rem .35rem;
      border: 1px solid #d6d3d1; cursor: pointer; color: #44403c; }}
    .tile-compare input {{ margin: 0; cursor: pointer; }}
    .tile.chev-pref {{ box-shadow: inset 0 0 0 2px #2563eb; }}
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
    .tile-body {{ padding: .4rem .45rem .5rem; flex: 1; display: flex; flex-direction: column; gap: .2rem; }}
    .tile-title {{ margin: 0; font-size: .88rem; font-weight: 600; line-height: 1.3;
      display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
    .tile-title a {{ color: #1c1917; text-decoration: none; }}
    .tile-meta {{ font-size: .72rem; color: #78716c; line-height: 1.3;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
    .tile-price {{ font-size: .76rem; font-weight: 600; color: #166534; }}
    .tile-price.paid-price {{ color: #15803d; }}
    @media (max-width: 768px) {{
      .layout {{ flex-direction: column; }}
      .sidebar {{ width: 100%; min-width: 0; position: relative; max-height: none;
        border-right: none; border-bottom: 1px solid #e7e5e4; }}
      .mobile-filter-btn {{ display: inline-block; margin: .5rem 1rem 0; padding: .45rem .75rem;
        border: 1px solid #d6d3d1; border-radius: 8px; background: #fff; font-size: .8rem; }}
      .sidebar.collapsed .filter-panel {{ display: none; }}
    }}
    .empty {{ text-align: center; padding: 3rem 1rem; color: #78716c; }}
    footer {{ text-align: center; font-size: .75rem; color: #a8a29e; padding: 1.5rem; }}
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-head">
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
      <nav class="upcoming-nav" id="upcoming-nav" aria-label="Upcoming trips">
        <div class="upcoming-nav-title">Upcoming trips</div>
        <div id="upcoming-trip-links"></div>
      </nav>
      <button type="button" class="mobile-filter-btn" id="toggle-filters">Filters</button>
      <div class="filter-panel">
        <div class="filter-panel-top">
          <h2>Filters</h2>
          <button type="button" id="clear-filters">Clear</button>
        </div>
        <div class="filter-count" id="filter-count"></div>
        <label class="source-toggle-opt"><input type="checkbox" id="show-offerup" checked> 📱 OfferUp</label>
        <label class="hide-seen-opt" id="hide-seen-wrap"><input type="checkbox" id="hide-seen"> Hide already seen</label>
        <details class="trips-panel" open>
          <summary>My trips</summary>
          <label for="trip-filter-select" style="font-size:.72rem;font-weight:600;color:#44403c">Filter feed by trip</label>
          <select id="trip-filter-select">
            <option value="">All listings</option>
          </select>
          <div class="trip-filter-opts">
            <label><input type="checkbox" id="trip-show-saved"> Only 📌 saved for this trip</label>
            <label><input type="checkbox" id="trip-show-route"> Only 🛣 along route</label>
          </div>
          <button type="button" class="trip-save-search" id="save-search-trip" style="display:none">
            💾 Save current filters for selected trip
          </button>
          <div class="trip-rows" id="trips-list"></div>
          <button type="button" class="trip-add-btn" id="trip-add">+ Add trip</button>
        </details>
        <div class="route-filter" id="route-filter-box">
          <label for="route-dest">Route — show listings on the way to</label>
          <input type="text" id="route-dest" list="route-city-list" placeholder="e.g. Pueblo, Colorado Springs">
          <datalist id="route-city-list"></datalist>
          <div class="hint">Default: Gardner → Pueblo along I-25. Clear to show everywhere.</div>
        </div>
        <div class="vehicle-loc-filter" id="vehicle-loc-filter" hidden>
          <label for="vehicle-loc-query">Location contains</label>
          <input type="text" id="vehicle-loc-query" placeholder="e.g. Pueblo, Walsenburg, 81040">
          <div class="hint">Filter by city or ZIP in the listing location.</div>
        </div>
        <details class="exclude-keywords" open>
          <summary>Hide posts with keywords</summary>
          <div class="exclude-kw-add">
            <input type="text" id="exclude-kw-input" placeholder="Type word to hide…" autocomplete="off">
            <button type="button" id="exclude-kw-add">Add</button>
          </div>
          <div class="exclude-kw-custom" id="exclude-kw-custom"></div>
          <div id="exclude-keywords"></div>
        </details>
        <div id="filter-groups"></div>
        <details class="platform-recs">
          <summary>Add more sources</summary>
          <div id="platform-recs"></div>
        </details>
        <details class="web-market-panel" id="web-market-panel" open>
          <summary>More sites — saved searches &amp; forums</summary>
          <div class="web-market-searches" id="web-market-searches"></div>
          <div id="web-market-sites"></div>
        </details>
      </div>
    </aside>
    <main class="content">
      <div class="content-toolbar">
        <button type="button" class="toolbar-btn secondary" id="mark-all-seen">✓ Mark all as seen</button>
        <button type="button" class="toolbar-btn secondary" id="reset-seen">↺ Reset seen marks</button>
        <button type="button" class="toolbar-btn" id="rerun-all">↻ Rerun search (include seen)</button>
        <button type="button" class="toolbar-btn" id="export-compare" hidden>⬇ Export compare (Excel)</button>
        <button type="button" class="toolbar-btn secondary" id="clear-compare" hidden>☐ Clear compare</button>
        <span class="image-health" id="image-health"></span>
      </div>
      <div class="compare-bar hidden" id="compare-bar">
        <span id="compare-count">0 selected for compare</span>
      </div>
      <div id="cl-warning" class="cl-warning hidden" role="status">
        <button type="button" id="cl-warning-dismiss" aria-label="Dismiss">×</button>
        Craigslist may block your browser after a big scan. Use <b>Email seller</b> (relay address when available)
        instead of opening listings. If you see a block page, wait a few hours or switch networks, then rerun Skout.
      </div>
      <div id="trip-filter-banner" class="trip-filter-banner hidden" role="status"></div>
      <div id="web-market-bar" class="web-market-bar hidden"></div>
      <div id="source-counts" class="source-counts hidden"></div>
      <div class="channel-bar-wrap">
        <div class="channel-bar-title">Source health</div>
        <div class="channel-bar" id="channel-bar"></div>
      </div>
      <div class="stats" id="stats"></div>
      <div id="list"></div>
  <div id="modal" class="modal hidden" aria-hidden="true">
    <div class="modal-backdrop" id="modal-backdrop"></div>
    <div class="modal-panel">
      <button type="button" class="modal-close" id="modal-close" aria-label="Close">×</button>
      <div class="modal-gallery" id="modal-gallery">
        <button type="button" class="gallery-nav gallery-prev" id="gallery-prev" aria-label="Previous photo">‹</button>
        <img id="modal-img" alt="">
        <button type="button" class="gallery-nav gallery-next" id="gallery-next" aria-label="Next photo">›</button>
        <span class="gallery-count" id="gallery-count"></span>
        <div class="gallery-dots" id="gallery-dots"></div>
      </div>
      <h2 id="modal-title"></h2>
      <p class="modal-meta" id="modal-meta"></p>
      <p class="modal-desc" id="modal-desc"></p>
      <label class="modal-compare" id="modal-compare-wrap" hidden>
        <input type="checkbox" id="modal-compare"> ☑ Include in compare export
      </label>
      <div class="modal-actions">
        <button type="button" class="email-btn" id="modal-email">✉️ Email seller</button>
        <button type="button" class="copy-btn" id="modal-copy">Copy message</button>
        <button type="button" class="primary" id="modal-save">📌 Save for trip</button>
        <a id="modal-link" target="_blank" rel="noopener">Open listing</a>
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
    const saveKey = 'skout_saved_' + (DATA.profile_id || 'default');
    const tripsKey = 'skout_trips_' + (DATA.profile_id || 'default');
    const tripSearchesKey = 'skout_trip_searches_' + (DATA.profile_id || 'default');
    const filterStateKey = 'skout_filters_' + (DATA.profile_id || 'default');
    const manualSeenKey = 'skout_manual_seen_' + (DATA.profile_id || 'default');
    const compareKey = 'skout_compare_' + (DATA.profile_id || 'default');
    const hiddenKeywords = new Set((DATA.exclude_title_keywords || []).map(k => k.toLowerCase()));
    let routeDest = '';
    let selectedTripId = '';
    let tripShowSaved = false;
    let tripShowRoute = false;
    let hideSeen = false;
    let showOfferUp = true;
    let vehicleLocQuery = '';
    let modalItem = null;
    let galleryUrls = [];
    let galleryIdx = 0;

    function loadFilterState() {{
      try {{
        const saved = JSON.parse(localStorage.getItem(filterStateKey) || '{{}}');
        (saved.include || []).forEach(id => includeFilters.add(id));
        (saved.hidden || []).forEach(id => hiddenCats.add(id));
        (saved.hiddenKeywords || []).forEach(k => hiddenKeywords.add(k));
        routeDest = saved.routeDest || DATA.default_route_dest || '';
        selectedTripId = saved.selectedTripId || '';
        tripShowSaved = saved.tripShowSaved === true;
        tripShowRoute = saved.tripShowRoute === true;
        hideSeen = !!saved.hideSeen;
        showOfferUp = saved.showOfferUp !== false;
        vehicleLocQuery = saved.vehicleLocQuery || '';
      }} catch (e) {{}}
    }}

    function saveFilterState() {{
      localStorage.setItem(filterStateKey, JSON.stringify({{
        include: [...includeFilters],
        hidden: [...hiddenCats],
        hiddenKeywords: [...hiddenKeywords],
        routeDest,
        selectedTripId,
        tripShowSaved,
        tripShowRoute,
        hideSeen,
        showOfferUp,
        vehicleLocQuery,
      }}));
    }}
    loadFilterState();

    function configureVehicleUi() {{
      if (DATA.vertical !== 'vehicles') return;
      const hideIds = ['upcoming-nav', 'route-filter-box'];
      hideIds.forEach(id => {{
        const el = document.getElementById(id);
        if (el) el.hidden = true;
      }});
      const tripsPanel = document.querySelector('.trips-panel');
      if (tripsPanel) tripsPanel.hidden = true;
      const locBox = document.getElementById('vehicle-loc-filter');
      if (locBox) locBox.hidden = false;
    }}
    configureVehicleUi();

    DATA.listings.forEach(item => {{ item._was_new = item.is_new; }});

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
      DATA.listings.forEach(item => {{
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
        hiddenKeywords: [...hiddenKeywords],
        tripShowSaved,
        tripShowRoute,
        hideSeen,
        showOfferUp,
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
      hiddenKeywords.clear();
      (DATA.exclude_title_keywords || []).forEach(k => hiddenKeywords.add(k.toLowerCase()));
      (saved.hiddenKeywords || []).forEach(k => hiddenKeywords.add(k));
      tripShowSaved = saved.tripShowSaved === true;
      tripShowRoute = saved.tripShowRoute === true;
      hideSeen = !!saved.hideSeen;
      showOfferUp = saved.showOfferUp !== false;
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
      const enabled = !!DATA.compare_export_enabled;
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

    async function contactSeller(item) {{
      if (!item) return;
      const msg = pickupMessage(item);
      const subject = ('Re: ' + (item.title || 'listing')).slice(0, 120);
      const body = msg.slice(0, 1800);
      const copied = await copyText(msg);

      const method = item.contact_method || (item.reply_email ? 'email' : 'site');
      let contactUrl = sanitizeReplyUrl(item.contact_url || item.reply_url || '');
      const listingUrl = item.url || '';

      if (method === 'email' && (item.reply_email || '').trim()) {{
        const to = item.reply_email.trim();
        const href = `mailto:${{to}}?subject=${{encodeURIComponent(subject)}}&body=${{encodeURIComponent(body)}}`;
        const a = document.createElement('a');
        a.href = href;
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        showToast(copied ? 'Message copied · opening mail' : 'Opening mail — copy message from modal if needed');
        return;
      }}

      const src = (item.source || '').split(':')[0];
      if (src === 'craigslist') {{
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
        'Craigslist may block your browser after a big scan. Prefer Email seller to copy your message. Open listing anyway?'
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

    function openModal(item) {{
      modalItem = item;
      const modal = document.getElementById('modal');
      galleryUrls = listingImages(item);
      galleryIdx = 0;
      renderGallery();
      document.getElementById('modal-title').textContent = item.title;
      document.getElementById('modal-meta').textContent =
        `${{item.platform}} · ${{item.price}} · ${{item.location}}` +
        (DATA.vertical === 'vehicles'
          ? ` · fit ${{item.fit_score || 0}} (${{item.fit_label || 'n/a'}})`
          : ` · score ${{item.score}}`) +
        (item.make ? ` · ${{item.make}} ${{item.model}}` : '') +
        (item.miles ? ` · ${{item.miles}}` : '');
      document.getElementById('modal-desc').textContent =
        item.description || 'No description available — open the listing for full details.';
      document.getElementById('modal-link').href = item.url;
      document.getElementById('modal-link').onclick = (e) => skoutOpenListing(e, item.source);
      const compareWrap = document.getElementById('modal-compare-wrap');
      const compareBox = document.getElementById('modal-compare');
      if (compareWrap && compareBox) {{
        compareWrap.hidden = !DATA.compare_export_enabled;
        compareBox.checked = isCompareSelected(item.id);
        compareBox.onchange = () => toggleCompareItem(item, compareBox.checked);
      }}
      const saveBtn = document.getElementById('modal-save');
      saveBtn.textContent = isSaved(item.id)
        ? '📌 Saved — tap to remove'
        : (DATA.vertical === 'vehicles' ? '📌 Save to shortlist' : '📌 Save for trip');
      const contactBtn = document.getElementById('modal-email');
      const cLabel = item.contact_label || (item.reply_email ? 'Email seller' : 'Contact seller');
      contactBtn.textContent = (item.contact_method === 'email' ? '✉️ ' : '💬 ') + cLabel;
      modal.classList.remove('hidden');
      modal.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
      modal.scrollTop = 0;
      window.scrollTo(0, 0);
    }}

    function closeModal() {{
      document.getElementById('modal').classList.add('hidden');
      document.getElementById('modal').setAttribute('aria-hidden', 'true');
      document.body.style.overflow = '';
      modalItem = null;
    }}

    document.getElementById('modal-close').onclick = closeModal;
    document.getElementById('modal-backdrop').onclick = closeModal;
    document.getElementById('modal-save').onclick = () => {{
      if (!modalItem) return;
      if (isSaved(modalItem.id)) unsaveListing(modalItem.id);
      else pickTripAndSave(modalItem);
      document.getElementById('modal-save').textContent =
        isSaved(modalItem.id) ? '📌 Saved — tap to remove'
          : (DATA.vertical === 'vehicles' ? '📌 Save to shortlist' : '📌 Save for trip');
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
      if (e.key === 'ArrowLeft') {{ e.preventDefault(); galleryStep(-1); }}
      if (e.key === 'ArrowRight') {{ e.preventDefault(); galleryStep(1); }}
      if (e.key === 'Escape') closeModal();
    }});

    function itemMatchesInclude(item, filterId) {{
      if (filterId === 'priority') return item.is_priority;
      if (filterId === 'saved') return isSaved(item.id);
      if (filterId === 'free_only') return item.is_free_price;
      if (filterId === 'chevy_preferred') return !!item.make_preferred;
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

    function matchesFilter(item) {{
      if (hideSeen && !itemIsNew(item)) return false;
      if (!showOfferUp && (item.source || '').startsWith('offerup')) return false;
      if (hiddenCats.has(item.category_id)) return false;
      for (const kw of hiddenKeywords) {{
        if ((item.title || '').toLowerCase().includes(kw)) return false;
      }}
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
      return list.slice().sort((a, b) => {{
        if (trip) {{
          const sa = isSavedForTrip(a.id, trip.id) ? 0 : 1;
          const sb = isSavedForTrip(b.id, trip.id) ? 0 : 1;
          if (sa !== sb) return sa - sb;
        }}
        const seenA = itemIsNew(a) ? 0 : 1;
        const seenB = itemIsNew(b) ? 0 : 1;
        if (seenA !== seenB) return seenA - seenB;
        if (DATA.vertical === 'vehicles') {{
          const fitA = a.fit_score || 0;
          const fitB = b.fit_score || 0;
          if (fitA !== fitB) return fitB - fitA;
          const chevA = a.make_preferred ? 0 : 1;
          const chevB = b.make_preferred ? 0 : 1;
          if (chevA !== chevB) return chevA - chevB;
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
      if (opts.listOnly) {{
        renderListings();
      }} else {{
        if (DATA.vertical !== 'vehicles') {{
          renderUpcomingNav();
          renderTripsPanel();
        }}
        renderWebMarketplaces();
        renderListings();
      }}
      if (scrollY !== null) {{
        requestAnimationFrame(() => window.scrollTo(0, scrollY));
      }}
    }}

    function renderListings() {{
      const footer = [DATA.branding?.wordmark || DATA.app || 'Skout', DATA.profile_id || ''].filter(Boolean).join(' · ');
      document.getElementById('footer').textContent =
        DATA.public_url ? footer + ' · ' + DATA.public_url : footer;

      const s = DATA.stats;
      const savedCount = Object.keys(getSaved()).length;
      const trip = getSelectedTrip();
      const shownPreview = sortShownList(DATA.listings.filter(matchesFilter), trip);

      const sourceCountsEl = document.getElementById('source-counts');
      if (sourceCountsEl && (DATA.vertical === 'vehicles' || DATA.trailer_hunt) && (DATA.source_counts || []).length) {{
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

      let statsHtml =
        `<div class="stat"><b>${{shownPreview.length}}</b><span>showing</span></div>` +
        `<div class="stat"><b>${{DATA.listings.filter(itemIsNew).length}}</b><span>new</span></div>` +
        `<div class="stat"><b>${{savedCount}}</b><span>saved</span></div>` +
        `<div class="stat"><b>${{s.checked}}</b><span>checked</span></div>` +
        `<div class="stat"><b>${{DATA.listings.length}}</b><span>in feed</span></div>`;
      if (s.duplicates_removed > 0) {{
        statsHtml += `<div class="stat"><b>${{s.duplicates_removed}}</b><span>dupes merged</span></div>`;
      }}
      if (trip && DATA.vertical !== 'vehicles') {{
        const nSaved = countSavedForTrip(trip.id);
        const nRoute = trip.city
          ? DATA.listings.filter(i => locationOnRouteTo(i.location, trip.city)).length
          : 0;
        statsHtml +=
          `<div class="stat"><b>${{nSaved}}</b><span>saved · ${{trip.name}}</span></div>` +
          `<div class="stat"><b>${{nRoute}}</b><span>on route</span></div>`;
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

      const hasCl = (DATA.listings || []).some(i => (i.source || '').startsWith('craigslist'));
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

      const showOfferUpEl = document.getElementById('show-offerup');
      showOfferUpEl.checked = showOfferUp;
      showOfferUpEl.onchange = () => {{
        showOfferUp = showOfferUpEl.checked;
        saveFilterState();
        render();
      }};

      function renderFilterOption(group, opt) {{
        const id = opt.id;
        const checked = group.exclude ? !hiddenCats.has(id) : includeFilters.has(id);
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
            if (input.checked) hiddenCats.delete(id); else hiddenCats.add(id);
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
        defaultHiddenCats().forEach(id => hiddenCats.add(id));
        hiddenKeywords.clear();
        (DATA.exclude_title_keywords || []).forEach(k => hiddenKeywords.add(k.toLowerCase()));
        routeDest = '';
        selectedTripId = '';
        tripShowSaved = false;
        tripShowRoute = false;
        vehicleLocQuery = '';
        hideSeen = false;
        showOfferUp = true;
        saveFilterState();
        updateTripHash();
        render();
      }};

      const hideSeenEl = document.getElementById('hide-seen');
      hideSeenEl.checked = hideSeen;
      hideSeenEl.onchange = () => {{
        hideSeen = hideSeenEl.checked;
        saveFilterState();
        render();
      }};

      const tripBanner = document.getElementById('trip-filter-banner');
      if (tripBanner && DATA.vertical !== 'vehicles') {{
        const narrowing = trip && (tripShowSaved || tripShowRoute);
        if (narrowing && shownPreview.length < DATA.listings.length) {{
          tripBanner.classList.remove('hidden');
          tripBanner.innerHTML =
            `Trip filter — showing ${{shownPreview.length}} of ${{DATA.listings.length}}.` +
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
      if (!showOfferUp) activeCount += 1;
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
        let msg = 'No listings match your current filters.';
        if (trip && (tripShowSaved || tripShowRoute)) {{
          msg = `No listings match trip filter for ${{trip.name}}. Click <b>Show all</b> above or uncheck the route/saved boxes in My trips.`;
        }} else if (DATA.listings.length) {{
          msg = `${{DATA.listings.length}} in feed but filters hide them all. ` +
            `<button type="button" id="empty-clear-filters">Clear all filters</button>`;
        }}
        document.getElementById('list').innerHTML = `<p class="empty">${{msg}}</p>`;
        const clearBtn = document.getElementById('empty-clear-filters');
        if (clearBtn) clearBtn.onclick = () => document.getElementById('clear-filters').click();
        return;
      }}
      document.getElementById('list').innerHTML = shown.map((item, idx) => {{
        const imgs = listingImages(item);
        const thumb = imgs.length
          ? `<img src="${{escapeHtml(imgs[0])}}" alt="" loading="lazy" referrerpolicy="no-referrer"
              data-src="${{escapeHtml(imgs[0])}}" data-icon="${{escapeHtml(item.category_icon)}}"
              onerror="window.skoutImgError(this)">`
          : `<span class="placeholder">${{item.category_icon}}</span>`;
        const multiBadge = imgs.length > 1
          ? `<span class="tile-badge" title="${{imgs.length}} photos">🖼 ${{imgs.length}}</span>` : '';
        let tripTag = '';
        let tripClass = '';
        if (DATA.vertical === 'vehicles') {{
          if (isSaved(item.id)) tripTag = '<span class="trip-tag">📌 Shortlist</span>';
        }} else if (trip) {{
          const savedFor = isSavedForTrip(item.id, trip.id);
          const onRoute = trip.city && locationOnRouteTo(item.location, trip.city);
          if (savedFor) {{
            tripClass = 'trip-saved';
            tripTag = '<span class="trip-tag">📌 Saved for trip</span>';
          }} else if (onRoute) {{
            tripClass = 'trip-route-only';
            tripTag = '<span class="trip-tag">🛣 On route</span>';
          }}
        }} else if (isSaved(item.id)) {{
          const sv = getSaved()[item.id];
          tripTag = `<span class="trip-tag">📌 ${{escapeHtml(sv.tripName || 'Saved')}}</span>`;
        }}
        const tileClass = [
          'tile',
          item.is_priority ? 'priority' : '',
          !item.is_free_price ? 'paid' : '',
          isSaved(item.id) ? 'saved-pin' : '',
          !itemIsNew(item) ? 'post-seen' : '',
          item.make_preferred ? 'chev-pref' : '',
          isCompareSelected(item.id) ? 'compare-on' : '',
          tripClass,
        ].filter(Boolean).join(' ');
        const priceClass = item.is_free_price ? 'tile-price' : 'tile-price paid-price';
        const loc = (item.location || '').split('(')[0].trim();
        const worthBadge = worthTripBadge(item, trip);
        const alsoOn = (item.also_on || []).length
          ? `<p class="also-on">Also on: ${{item.also_on.map(escapeHtml).join(', ')}}</p>` : '';
        const compareChk = DATA.compare_export_enabled
          ? `<label class="tile-compare" title="Export to Excel compare sheet">
              <input type="checkbox" data-compare="${{idx}}" ${{isCompareSelected(item.id) ? 'checked' : ''}}> Compare
            </label>` : '';
        return `<article class="${{tileClass}}">
          <div class="tile-media-wrap">
            <button type="button" class="tile-media" data-idx="${{idx}}" aria-label="View details">${{thumb}}</button>
            <div class="tile-badges">
              <span class="tile-badge" title="${{item.category_label}}">${{item.category_icon}}</span>
              <span class="tile-badge">${{item.platform_icon}}</span>
              ${{multiBadge}}
            </div>
            <button type="button" class="tile-seen ${{!itemIsNew(item) ? 'seen' : ''}}" data-seen="${{idx}}"
              aria-label="Mark seen">✓</button>
            <button type="button" class="tile-save ${{isSaved(item.id) ? 'saved' : ''}}" data-save="${{idx}}"
              aria-label="${{DATA.vertical === 'vehicles' ? 'Save to shortlist' : 'Save for trip'}}">📌</button>
            <button type="button" class="tile-email" data-email="${{idx}}" aria-label="Email seller">✉️</button>
            ${{compareChk}}
          </div>
          <div class="tile-body">
            <p class="${{priceClass}}">${{item.price}}</p>
            <h3 class="tile-title"><a href="${{item.url}}" target="_blank" rel="noopener noreferrer"
              onclick="return skoutOpenListing(event, '${{escapeHtml(item.source)}}')">${{item.title}}</a></h3>
            <p class="tile-meta">${{item.platform}} · ${{loc}} ${{tripTag}}</p>
            ${{worthBadge}}
            ${{alsoOn}}
          </div>
        </article>`;
      }}).join('');

      document.querySelectorAll('.tile-media').forEach(btn => {{
        btn.onclick = (e) => {{
          if (e.target.closest('.tile-save') || e.target.closest('.tile-seen')) return;
          e.preventDefault();
          openModal(shown[parseInt(btn.dataset.idx, 10)]);
        }};
      }});
      document.querySelectorAll('.tile-seen').forEach(btn => {{
        btn.onclick = (e) => {{
          e.stopPropagation();
          e.preventDefault();
          toggleItemSeen(shown[parseInt(btn.dataset.seen, 10)]);
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
      const icon = img.dataset.icon || '📦';
      const span = document.createElement('span');
      span.className = 'placeholder';
      span.textContent = icon;
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
      DATA.listings.forEach(item => {{
        if (item._was_new !== undefined) item.is_new = item._was_new;
      }});
      render();
      showToast('Cleared seen marks — new listings restored');
    }};
    document.getElementById('mark-all-seen').onclick = async () => {{
      const seen = getManualSeen();
      let n = 0;
      DATA.listings.forEach(item => {{
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
    render();
  </script>
</body>
</html>"""

    index_path = SITE_DIR / "index.html"
    index_path.write_text(html, encoding="utf-8")
    return index_path
