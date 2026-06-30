#!/usr/bin/env python3
"""Skout — free farm & resale finder."""

import json
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from dedupe import dedupe_listings
from config_loader import active_profile_id, load_all
from location import active_location, resolve_trip
from site_builder import SITE_DIR, write_site
from scoring import Listing, is_vehicle_listing, is_trailer_listing, score_listing, tier_for, title_mentions_trailer
from vehicle_fields import parse_price_usd
from scrapers.craigslist import enrich_listing_details
from sources_runner import fetch_all_sources
from storage import already_seen, cache_details, get_cached_details, init_db, mark_seen, mark_seen_batch

load_dotenv()


def _source_key(source: str) -> str:
    if source.startswith("craigslist"):
        return "craigslist"
    if source.startswith("web:"):
        return source
    if source in ("facebook", "facebook_group"):
        return "facebook"
    return source.split(":")[0]


def _build_channel_stats(
    cfg: dict,
    raw_listings: list,
    display: list,
) -> list[dict]:
    platforms = cfg.get("platforms", {})
    channel_defs = [
        ("craigslist", "Craigslist", "🟠", None),
        ("freecycle", "Freecycle", "♻️", None),
        ("facebook", "Facebook", "📘", "Run: src/scrapers/facebook.py --login"),
        ("trash_nothing", "Trash Nothing", "🗑️", "TRASHNOTHING_API_KEY in .env"),
        ("nextdoor", "Nextdoor", "🏘️", "NEXTDOOR_CLIENT_ID/SECRET or --login"),
        ("offerup", "OfferUp", "📱", None),
    ]
    web_channels = [
        ("web:cars_com", "Cars.com", "🚗"),
        ("web:truecar", "TrueCar", "💲"),
        ("web:ebay_motors", "eBay Motors", "🛒"),
        ("web:autotrader", "Autotrader", "🚙"),
        ("web:cargurus", "CarGurus", "📊"),
        ("web:privateauto", "PrivateAuto", "🤝"),
        ("web:autolist", "Autolist", "📋"),
        ("web:iseecars", "iSeeCars", "🔍"),
    ]
    enabled = {
        "craigslist": platforms.get("craigslist", {}).get("enabled"),
        "freecycle": platforms.get("freecycle", {}).get("enabled"),
        "facebook": (
            platforms.get("facebook_marketplace", {}).get("enabled")
            or platforms.get("facebook_groups", {}).get("enabled")
        ),
        "trash_nothing": platforms.get("trash_nothing", {}).get("enabled"),
        "nextdoor": platforms.get("nextdoor", {}).get("enabled"),
        "offerup": platforms.get("offerup", {}).get("enabled"),
        "web_marketplaces": platforms.get("web_marketplaces_scrape", {}).get("enabled"),
    }
    fetched: dict[str, int] = defaultdict(int)
    for listing in raw_listings:
        fetched[_source_key(listing.source)] += 1
    shown: dict[str, int] = defaultdict(int)
    for listing, _, _ in display:
        shown[_source_key(listing.source)] += 1

    stats = []
    for cid, label, icon, setup_hint in channel_defs:
        if not enabled.get(cid):
            continue
        f = fetched.get(cid, 0)
        s = shown.get(cid, 0)
        if s > 0:
            status = "ok"
        elif f > 0:
            status = "filtered"
        else:
            status = "setup"
        stats.append({
            "id": cid,
            "label": label,
            "icon": icon,
            "fetched": f,
            "showing": s,
            "status": status,
            "setup_hint": setup_hint or "",
        })
    if enabled.get("web_marketplaces"):
        for wid, wlabel, wicon in web_channels:
            f = fetched.get(wid, 0)
            s = shown.get(wid, 0)
            if s > 0:
                status = "ok"
            elif f > 0:
                status = "filtered"
            else:
                status = "setup"
            stats.append({
                "id": wid,
                "label": wlabel,
                "icon": wicon,
                "fetched": f,
                "showing": s,
                "status": status,
                "setup_hint": "playwright install chromium",
            })
    return stats


def mark_all_seen_from_site() -> int:
    data_path = SITE_DIR / "data.json"
    if not data_path.exists():
        print("No site/data.json — run a scan first.", flush=True)
        return 1
    init_db()
    data = json.loads(data_path.read_text(encoding="utf-8"))
    profile_id = data.get("profile_id") or active_profile_id()
    rows = []
    for item in data.get("listings", []):
        url = item.get("url", "")
        if not url:
            continue
        pid = item.get("posting_id") or f"{profile_id}:{item.get('source', '')}:{url}"
        rows.append((pid, item.get("title", ""), url))
    mark_seen_batch(rows)
    print(f"Marked {len(rows)} listings as seen.", flush=True)
    return 0


def rebuild_ui_from_data(*, open_browser: bool = False) -> int:
    data_path = SITE_DIR / "data.json"
    if not data_path.exists():
        print("No site/data.json — run a scan first.", flush=True)
        return 1
    cfg = load_all()
    loc = active_location(cfg["travel"])
    data = json.loads(data_path.read_text(encoding="utf-8"))
    items: list[tuple[Listing, int, str]] = []
    for d in data.get("listings", []):
        listing = Listing(
            title=d.get("title", ""),
            url=d.get("url", ""),
            source=d.get("source", ""),
            price=d.get("price", "free"),
            location=d.get("location", ""),
            category_id=d.get("category_id", "other"),
            category_label=d.get("category_label", "Other"),
            category_icon=d.get("category_icon", "📌"),
            platform_label=d.get("platform", ""),
            platform_icon=d.get("platform_icon", "🔗"),
            image_url=d.get("image_url", ""),
            image_urls=d.get("image_urls") or [],
            description=d.get("description", ""),
            reply_email=d.get("reply_email", ""),
            reply_url=d.get("reply_url", ""),
            also_on=d.get("also_on") or [],
        )
        items.append((listing, d.get("score", 0), d.get("tier", "everything_else")))
    stats = data.get("stats", {})
    new_urls = {d["url"] for d in data.get("listings", []) if d.get("is_new") and d.get("url")}
    path = write_site(
        items,
        loc,
        cfg,
        total_checked=stats.get("checked", len(items)),
        new_count=stats.get("new", len(new_urls)),
        show_all=True,
        new_urls=new_urls,
        channel_stats=data.get("channel_stats", []),
        duplicates_removed=stats.get("duplicates_removed", 0),
    )
    print(f"Rebuilt dashboard: {path} ({len(items)} listings)", flush=True)
    if open_browser:
        subprocess.run(["open", str(path)], check=False)
    return 0


def _parse_trip_arg() -> str:
    for i, arg in enumerate(sys.argv):
        if arg == "--trip" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if arg.startswith("--trip="):
            return arg.split("=", 1)[1]
    return ""


def run(
    test_mode: bool = False,
    open_browser: bool = False,
    show_all: bool = False,
    trailer_only: bool = False,
    trip_query: str = "",
) -> int:
    cfg = load_all()
    profile = cfg["profile"]
    init_db()
    focus_trip_id = ""
    if trip_query:
        resolved = resolve_trip(cfg["travel"], trip_query)
        if resolved:
            loc, focus_trip_id = resolved
            print(
                f"Trip focus: {loc.get('name')} ({loc.get('start')} → {loc.get('end')})",
                flush=True,
            )
        else:
            print(f"Trip not found: {trip_query!r} — using today's location", flush=True)
            loc = active_location(cfg["travel"])
    else:
        loc = active_location(cfg["travel"])
    min_score = cfg["scoring"]["tiers"]["must_email_min_score"]
    vertical = profile.get("vertical", "")
    display_cfg = profile.get("display") or {}
    if test_mode:
        min_score = 25
    if vertical == "vehicles" or display_cfg.get("show_all_scored_matches"):
        min_score = 0

    scan_cfg = profile.get("scan") or {}
    always_full = bool(scan_cfg.get("always_full")) or vertical == "vehicles"
    effective_quick = test_mode and not always_full

    pid = active_profile_id()
    print(f"Skout starting — profile: {pid}…", flush=True)
    if test_mode and not always_full:
        print("Quick test — all enabled sources (limited regions)…", flush=True)
    elif test_mode and always_full:
        print("Full scan — all CO + FL regions & keywords (no quick mode)…", flush=True)
    elif show_all:
        print("Showing all matches (including previously seen)…", flush=True)
    if trailer_only:
        print("Trailer hunt — title match only, no scoring…", flush=True)
        show_all = True
    raw_listings = fetch_all_sources(
        cfg,
        quick=effective_quick,
        focus="trailer" if trailer_only else "",
    )
    raw_count = len(raw_listings)
    raw_listings, dupes_removed = dedupe_listings(raw_listings)
    if dupes_removed:
        print(f"Deduplicated {dupes_removed} cross-source duplicates ({raw_count} → {len(raw_listings)})", flush=True)
    print(f"Fetched {len(raw_listings)} total listings", flush=True)

    scored: list[tuple[Listing, int, str]] = []
    new_items: list[tuple[Listing, int, str]] = []

    trailer_max = 4000
    if trailer_only:
        for bucket in cfg["search"].get("paid_wanted", []) or []:
            if bucket.get("name") == "trailer":
                trailer_max = int(bucket.get("max_price_usd", 4000))
                break

    for listing in raw_listings:
        if trailer_only and not is_trailer_listing(
            listing.title,
            listing.description or "",
            cfg["search"],
        ):
            continue
        if trailer_only:
            price_usd = parse_price_usd(listing.price, listing.title)
            if price_usd is not None and price_usd > trailer_max:
                continue
        if vertical == "vehicles" and not is_vehicle_listing(
            listing.title,
            listing.description or "",
            listing.category_id,
            cfg["search"],
        ):
            continue
        pid = f"{active_profile_id()}:{listing.source}:{listing.url}"
        if trailer_only:
            score, tier = 1, "trailer"
        else:
            score = score_listing(listing, cfg)
            tier = tier_for(listing, score, cfg)
            if score <= -100:
                continue
            source_base = listing.source.split(":")[0] if ":" in listing.source else listing.source
            if score < min_score and source_base not in ("offerup", "web"):
                continue
        scored.append((listing, score, tier))
        if not already_seen(pid):
            new_items.append((listing, score, tier))
            if not test_mode and not show_all:
                mark_seen(pid, listing.title, listing.url)

    if test_mode or show_all:
        display = scored
    else:
        display = new_items or scored

    detail_listings = [
        listing for listing, _, _ in display
        if listing.source.startswith("craigslist:")
        and (
            not listing.image_url
            or not listing.description
            or not getattr(listing, "reply_email", "")
        )
    ]
    if detail_listings:
        print(f"Fetching Craigslist photos/descriptions for {len(detail_listings)}…", flush=True)
        enrich_listing_details(
            detail_listings,
            cache_get=get_cached_details,
            cache_set=cache_details,
            max_fetch=len(detail_listings),
        )
        with_photo = sum(1 for listing, _, _ in display if listing.image_url)
        with_desc = sum(1 for listing, _, _ in display if listing.description)
        with_reply = sum(1 for listing, _, _ in display if getattr(listing, "reply_email", ""))
        print(f"  {with_photo} photos, {with_desc} descriptions, {with_reply} reply emails", flush=True)

    new_urls = {listing.url for listing, _, _ in new_items}
    channel_stats = _build_channel_stats(cfg, raw_listings, display)
    path = write_site(
        display,
        loc,
        cfg,
        total_checked=len(raw_listings),
        new_count=len(new_items),
        show_all=show_all,
        new_urls=new_urls,
        channel_stats=channel_stats,
        duplicates_removed=dupes_removed,
        focus_trip_id=focus_trip_id,
        trailer_hunt=trailer_only,
    )
    print(f"\nWebsite: {path}", flush=True)
    print(f"Deploy site/ → Netlify (see docs/netlify-deploy.md)", flush=True)
    if open_browser:
        open_target = str(path)
        if focus_trip_id:
            open_target += f"#trip={focus_trip_id}"
        subprocess.run(["open", open_target], check=False)

    by_plat = defaultdict(int)
    for listing, _, _ in display:
        by_plat[listing.platform_label] += 1
    if by_plat:
        print("By source:", ", ".join(f"{k} {v}" for k, v in sorted(by_plat.items())), flush=True)
    if channel_stats:
        print("Channels:", ", ".join(
            f"{c['label']} {c['showing']}/{c['fetched']}" + (" ⚠" if c["status"] != "ok" else "")
            for c in channel_stats
        ), flush=True)

    print(f"Done — {len(display)} listings shown", flush=True)
    return 0


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
    if "--mark-seen" in sys.argv:
        raise SystemExit(mark_all_seen_from_site())
    if "--rebuild" in sys.argv:
        raise SystemExit(rebuild_ui_from_data(open_browser="--open" in sys.argv))
    test = "--test" in sys.argv
    open_page = "--open" in sys.argv
    show_all = "--all" in sys.argv
    trailer_only = "--trailer" in sys.argv
    trip_query = _parse_trip_arg()
    raise SystemExit(
        run(
            test_mode=test,
            open_browser=open_page,
            show_all=show_all,
            trailer_only=trailer_only,
            trip_query=trip_query,
        )
    )
