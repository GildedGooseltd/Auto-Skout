"""Fetch listings from every enabled source into one deduped feed."""

import re

from categories import categorize, platform_display
from scoring import (
    Listing,
    is_iso_post,
    is_trailer_listing,
    trailer_keywords,
)
from vehicle_fields import is_truck_listing
from scrapers.buy_nothing import fetch_offers as fetch_buy_nothing
from scrapers.craigslist import fetch_free, fetch_paid, fetch_trailers
from scrapers.freecycle import fetch_offers as fetch_freecycle
from scrapers.facebook import fetch_facebook_all
from scrapers.nextdoor import fetch_offers as fetch_nextdoor
from scrapers.trash_nothing import fetch_offers as fetch_trash_nothing
from scrapers.offerup import fetch_offers as fetch_offerup
from scrapers.autotempest import fetch_listings as fetch_autotempest, listing_source as autotempest_source
from scrapers.auctions import fetch_auction_listings, listing_source as auction_source


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()[:72]


def _listing_from_raw(raw, source: str, cfg: dict, *, paid: bool = False, paid_name: str = "") -> Listing:
    cats = cfg["categories"].get("categories", [])
    plat_icons = cfg["categories"].get("platforms", {})
    cat = categorize(raw.title, cats, is_paid_wanted=paid)
    plat = platform_display(source, plat_icons)
    imgs = list(getattr(raw, "image_urls", None) or [])
    img = getattr(raw, "image_url", "") or ""
    if not imgs and img:
        imgs = [img]
    return Listing(
        title=raw.title,
        url=raw.url,
        source=source,
        price=raw.price,
        location=raw.location,
        is_paid_wanted=paid,
        paid_item_name=paid_name,
        category_id=cat["id"],
        category_label=cat["label"],
        category_icon=cat["icon"],
        platform_label=plat["label"],
        platform_icon=plat["icon"],
        image_url=img,
        image_urls=imgs,
        description=getattr(raw, "description", "") or "",
        reply_email=getattr(raw, "reply_email", "") or "",
        reply_url=getattr(raw, "reply_url", "") or "",
    )


def fetch_all_sources(cfg: dict, *, quick: bool = False, focus: str = "") -> list[Listing]:
    """Run all enabled sources; shared scoring keywords apply to the combined results."""
    platforms = cfg["platforms"]
    search = cfg["search"]
    trailer_terms = trailer_keywords(search) if focus == "trailer" else []
    trailer_max_price = 4000
    if focus == "trailer":
        for bucket in search.get("paid_wanted", []) or []:
            if bucket.get("name") == "trailer":
                trailer_max_price = int(bucket.get("max_price_usd", 4000))
                break

    def query_terms(terms: list, paid=None):
        if focus != "trailer":
            return terms
        picked = [t for t in terms if "trailer" in t.lower()]
        if paid:
            picked = list(dict.fromkeys(picked + list(paid)))
        return picked or trailer_terms[:1]

    results: list[Listing] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    profile = cfg.get("profile") or {}
    vertical = profile.get("vertical", "")
    shop_rules = profile.get("shop_rules") or {}
    trucks_only = bool(shop_rules.get("trucks_only")) or vertical == "vehicles"
    always_full = bool((profile.get("scan") or {}).get("always_full")) or vertical == "vehicles"
    if always_full:
        quick = False

    def add(raw, source: str, **kwargs) -> bool:
        if trucks_only and not is_truck_listing(
            raw.title,
            getattr(raw, "description", "") or "",
            search=cfg.get("search"),
        ):
            return False
        if focus != "trailer" and is_iso_post(raw.title, search):
            return False
        if focus == "trailer" and not is_trailer_listing(
            raw.title,
            getattr(raw, "description", "") or "",
            cfg.get("search"),
        ):
            return False
        if not raw.url or "help_blocks" in raw.url or "reqType=help_blocks" in raw.url:
            return False
        if raw.url in seen_urls:
            return False
        fp = _norm_title(raw.title)
        if fp and fp in seen_titles:
            return False
        seen_urls.add(raw.url)
        if fp:
            seen_titles.add(fp)
        results.append(_listing_from_raw(raw, source, cfg, **kwargs))
        return True

    active_sources: list[str] = []

    # --- Craigslist ---
    cl = platforms.get("craigslist", {})
    if cl.get("enabled"):
        active_sources.append("Craigslist")
        regions = cl["regions"]
        if quick:
            regions = [r for r in regions if r["slug"] in ("pueblo", "cosprings")] or regions[:2]

        for region in regions:
            slug = region["slug"]
            print(f"Checking Craigslist {slug}…", flush=True)
            if focus == "trailer":
                batch = fetch_trailers(slug, max_price=trailer_max_price)
                if batch:
                    print(f"  trailers: {len(batch)} (≤${trailer_max_price})", flush=True)
                for raw in batch:
                    add(raw, f"craigslist:{slug}")
                continue

            queries = cl.get("search_queries", [""])
            if quick:
                queries = ["", "garden", "dirt", "pallet", "plant", "brick", "hose", "trailer"]
            for cat in cl.get("free_categories", []):
                for q in queries:
                    batch = fetch_free(slug, cat, q)
                    if batch:
                        print(f"  {cat} q={q!r}: {len(batch)}", flush=True)
                    for raw in batch:
                        add(raw, f"craigslist:{slug}")

            if not quick or vertical == "vehicles" or always_full:
                wanted_list = search.get("paid_wanted", [])
                if trucks_only:
                    wanted_list = [
                        w for w in wanted_list
                        if w.get("name") in ("tow_truck", "commercial_tow")
                    ]
                elif quick and vertical == "vehicles":
                    wanted_list = [
                        w for w in wanted_list
                        if w.get("name") in ("tow_truck", "daily_car")
                    ]
                for wanted in wanted_list:
                    keywords = wanted["keywords"]
                    if quick and not always_full:
                        keywords = keywords[:8]
                    for kw in keywords:
                        for cat in cl.get("paid_categories", ["fga"]):
                            batch = fetch_paid(slug, cat, kw, wanted["max_price_usd"])
                            if batch:
                                print(
                                    f"  {cat} q={kw!r}: {len(batch)} "
                                    f"(≤${wanted['max_price_usd']})",
                                    flush=True,
                                )
                            for raw in batch:
                                add(
                                    raw,
                                    f"craigslist:{slug}",
                                    paid=True,
                                    paid_name=wanted["name"],
                                )

    # --- Freecycle ---
    fc = platforms.get("freecycle", {})
    if fc.get("enabled"):
        active_sources.append("Freecycle")
        towns = fc.get("groups", [])
        print("Checking Freecycle…", flush=True)
        for town in towns:
            for raw in fetch_freecycle(town):
                add(raw, "freecycle")

    # --- Facebook (Marketplace + joined groups) ---
    fb_mp = platforms.get("facebook_marketplace", {})
    fb_gr = platforms.get("facebook_groups", {})
    if fb_mp.get("enabled") or fb_gr.get("enabled"):
        active_sources.append("Facebook")
        print("Checking Facebook…", flush=True)
        for raw, source in fetch_facebook_all(cfg, quick=quick, focus=focus, always_full=always_full):
            add(raw, source)

    # --- Trash Nothing (Freecycle + Buy Nothing aggregator) ---
    tn = platforms.get("trash_nothing", {})
    if tn.get("enabled"):
        active_sources.append("Trash Nothing")
        print("Checking Trash Nothing…", flush=True)
        for raw in fetch_trash_nothing({**tn, "search_terms": query_terms(tn.get("search_terms", ["free"]))}):
            add(raw, "trash_nothing")

    # --- Nextdoor ---
    nd = platforms.get("nextdoor", {})
    if nd.get("enabled"):
        active_sources.append("Nextdoor")
        print("Checking Nextdoor…", flush=True)
        for raw in fetch_nextdoor({**nd, "search_terms": query_terms(nd.get("search_terms", ["free"]))}):
            add(raw, "nextdoor")

    # --- OfferUp ---
    ou = platforms.get("offerup", {})
    if ou.get("enabled"):
        active_sources.append("OfferUp")
        print("Checking OfferUp…", flush=True)
        ou_cfg = dict(ou)
        if focus == "trailer":
            ou_cfg["search_terms"] = []
            ou_cfg["paid_search_terms"] = list(trailer_keywords(search))
            ou_cfg["max_price_usd"] = trailer_max_price
            ou_cfg["trailer_hunt"] = True
        for raw in fetch_offerup(ou_cfg):
            add(raw, "offerup")

    # --- AutoTempest (Cars.com · TrueCar · eBay · Autotrader-class) ---
    wm = platforms.get("web_marketplaces_scrape", {})
    if wm.get("enabled"):
        active_sources.append("Web marketplaces")
        print("Checking AutoTempest (Cars.com · TrueCar · eBay · …)…", flush=True)
        if focus == "trailer":
            wm_keys = wm.get("keywords") or list(trailer_keywords(cfg.get("search", {})))
            for raw in fetch_autotempest(
                zip_code=wm.get("zip", "81040"),
                radius=int(wm.get("radius", 150)),
                max_price_usd=trailer_max_price,
                min_price_usd=int(wm.get("min_price_usd", 0)),
                keywords=wm_keys,
                markets=wm.get("markets"),
                bodystyle="",
                listing_mode="trailer",
                search=cfg.get("search"),
                quick=False,
            ):
                add(raw, autotempest_source(raw), paid=True, paid_name="trailer")
        else:
            for raw in fetch_autotempest(
                zip_code=wm.get("zip", "81040"),
                radius=int(wm.get("radius", 200)),
                max_price_usd=int(wm.get("max_price_usd", 20000)),
                min_price_usd=int(wm.get("min_price_usd", 1000)),
                keywords=wm.get("keywords") or [],
                commercial_keywords=wm.get("commercial_keywords") or [],
                markets=wm.get("markets"),
                bodystyle=str(wm.get("bodystyle", "truck")),
                quick=False,
            ):
                add(raw, autotempest_source(raw), paid=True, paid_name="tow_truck")

    # --- Gov / police / bank auctions ---
    auc = platforms.get("auction_scrape", {})
    if auc.get("enabled"):
        active_sources.append("Auctions")
        print("Checking auctions (GovDeals · Public Surplus · PropertyRoom)…", flush=True)
        for raw in fetch_auction_listings(
            keywords=auc.get("keywords") or [],
            markets=auc.get("markets"),
            max_price_usd=int(auc.get("max_price_usd", 20000)),
            sites=auc.get("sites"),
            quick=False,
        ):
            add(raw, auction_source(raw), paid=True, paid_name="commercial_tow")

    # --- Buy Nothing (FB groups + Trash Nothing until dedicated scraper) ---
    bn = platforms.get("buy_nothing", {})
    if bn.get("enabled"):
        active_sources.append("Buy Nothing")
        print("Checking Buy Nothing…", flush=True)
        for raw in fetch_buy_nothing(bn):
            add(raw, "buy_nothing")

    if active_sources:
        print(f"Sources active: {', '.join(active_sources)}", flush=True)
    return results
