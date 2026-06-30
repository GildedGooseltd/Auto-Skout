"""Parse vehicle fields and compute tow-rig fit score for Auto Skout."""

from __future__ import annotations

import re
from typing import Any, Optional

_YEAR = re.compile(r"\b(19[89]\d|20[0-2]\d)\b")
_MILES = re.compile(
    r"(\d{1,3}(?:,\d{3})+|\d{4,7})\s*(?:k\s*)?(?:mi(?:les?)?|miles)\b",
    re.I,
)
_MILES_K = re.compile(r"\b(\d{2,3})k\s*(?:mi(?:les?)?|miles)?\b", re.I)
_PRICE = re.compile(r"\$[\d,]+")

NEAR_HOME_CITIES = (
    "gardner", "walsenburg", "huerfano", "la veta", "cuchara", "aguilar",
    "trinidad", "westcliffe", "silver cliff", "colorado city", "rye",
    "cuchara pass", "81040", "81089",
)
FRONT_RANGE_CITIES = (
    "pueblo", "colorado springs", "cos", "monument", "fountain", "canon city",
    "canon", "florence", "penrose", "denver", "aurora", "longmont", "boulder",
    "fort collins", "castle rock", "parker", "littleton", "highlands ranch",
)
COLORADO_CITIES = FRONT_RANGE_CITIES + NEAR_HOME_CITIES + (
    "grand junction", "durango", "steamboat", "aspen", "vail", "greeley",
    "loveland", "broomfield", "lakewood", "westminster", "thornton",
)
FLORIDA_CITIES = (
    "miami", "tampa", "orlando", "jacksonville", "fort lauderdale", "tallahassee",
    "st petersburg", "st. petersburg", "naples", "sarasota", "gainesville",
    "pensacola", "west palm", "boca raton", "fl ", " florida",
)

MAKE_PATTERNS: list[tuple[str, list[str]]] = [
    ("Chevrolet", [r"\bchev(?:y|rolet)\b", r"\bsilverado\b", r"\b2500hd\b", r"\b3500hd\b"]),
    ("GMC", [r"\bgmc\b", r"\bsierra\b"]),
    ("Ford", [r"\bford\b", r"\bf-?150\b", r"\bf-?250\b", r"\bf-?350\b", r"\bsuper\s+duty\b"]),
    ("Ram", [r"\bram\b", r"\bdodge\b", r"\bcummins\b"]),
    ("Toyota", [r"\btoyota\b", r"\btundra\b", r"\btacoma\b"]),
]

MODEL_HINTS = [
    (r"\bsilverado\s*2500\b", "Silverado 2500"),
    (r"\bsilverado\s*3500\b", "Silverado 3500"),
    (r"\bsierra\s*2500\b", "Sierra 2500"),
    (r"\bsierra\s*3500\b", "Sierra 3500"),
    (r"\bf-?250\b", "F-250"),
    (r"\bf-?350\b", "F-350"),
    (r"\bram\s*2500\b", "Ram 2500"),
    (r"\bram\s*3500\b", "Ram 3500"),
    (r"\b2500hd\b", "2500HD"),
    (r"\b3500hd\b", "3500HD"),
]

CAR_NOT_TOW = (
    r"\b(malibu|impala|terrain|equinox|traverse|tahoe(?!.*2500)|suburban(?!.*2500)|"
    r"civic|accord|camry|corolla|prius|sedan|hatchback|coupe|convertible|altima|"
    r"fusion|focus|fiesta|charger(?!.*truck)|challenger|mustang|camaro|"
    r"model\s*[3sxy]|bolt|leaf|ioniq)\b"
)
SUV_NOT_HD = r"\b(yukon|suburban|tahoe|expedition|sequoia|h2|hummer|navigator|escalade)\b"
HD_TOW = (
    r"\b(2500hd|3500hd|f-?250|f-?350|ram\s*2500|ram\s*3500|super\s+duty|"
    r"silverado\s*2500|silverado\s*3500|sierra\s*2500|sierra\s*3500|dually)\b"
)
COMMERCIAL_TOW = (
    r"\b(box\s*truck|commercial\s*truck|reefer|refrigerated|fridge\s*truck|"
    r"work\s*truck|medium\s*duty|stake\s*bed|flatbed\s*truck|dump\s*truck|"
    r"isuzu\s*npr|hino|fuso|international\s*4300|freightliner\s*m2|"
    r"kenworth\s*k|peterbilt\s*3|municipal\s*truck|fleet\s*truck)\b"
)
AUCTION_SIGNAL = re.compile(
    r"\b(auction|govdeals|gov\s*deals|surplus|police|sheriff|impound|"
    r"repo|repossession|bank\s*owned|seized|fleet\s*disposal|public\s*surplus|"
    r"propertyroom|gsa\s*auction|bid4assets|manheim)\b",
    re.I,
)
GRANT_CREDIT_SIGNAL = re.compile(
    r"\b(fleet|municipal|government|gov\s*surplus|surplus|electric|ev\b|"
    r"hybrid|alt\s*fuel|propane|cng|clean\s*vehicle|usda|farm\s*business|"
    r"commercial\s*vehicle|tax\s*credit|grant\s*eligible)\b",
    re.I,
)
VAN_NOT_TOW = re.compile(
    r"\b(cargo\s*van|passenger\s*van|minivan|transit\s*connect|"
    r"promaster\s*city|nv200)\b",
    re.I,
)
LIGHT_TRUCK = r"\b(f-?150|1500|silverado(?!.*2500)|sierra(?!.*2500)|tundra|tacoma)\b"
TRUCK_SIGNAL = re.compile(
    r"\b(truck|pickup|pick\s*up|2500hd|3500hd|f-?250|f-?350|super\s+duty|"
    r"silverado\s*2500|silverado\s*3500|sierra\s*2500|sierra\s*3500|"
    r"ram\s*2500|ram\s*3500|dually|crew\s*cab|work\s*truck|dump\s*truck|"
    r"flatbed\s*truck|diesel\s*truck|pick\s*up\s*truck|box\s*truck|"
    r"commercial\s*truck|reefer|refrigerated|fridge\s*truck|stake\s*bed|"
    r"medium\s*duty|isuzu\s*npr|hino|fuso)\b",
    re.I,
)
NOT_TRUCK = re.compile(
    r"\b(travel\s*trailer|camper|rv\b|avion|hitch|brake\s*controller|"
    r"utility\s*trailer|cargo\s*trailer|boat|motorcycle|atv|parts\s*only|"
    r"transmission\s*only|engine\s*only|wheels?\s*only|tires?\s*only)\b",
    re.I,
)
FORD_60_POWERSTROKE = re.compile(
    r"\b6\.0\s*l?\s*(power\s*stroke|diesel)\b",
    re.I,
)
FORD_HD_SIGNAL = re.compile(r"\b(ford|f-?250|f-?350|super\s+duty)\b", re.I)
FB_COMMERCIAL_CATEGORY = re.compile(
    r"\b(commercial\s*trucks?|work\s*trucks?|box\s*trucks?|medium\s*duty)\b",
    re.I,
)


def is_hard_avoid_tow_rig(blob: str) -> bool:
    """Ford Super Duty 6.0L Power Stroke — exclude (reliability + brake assist loss)."""
    if FORD_60_POWERSTROKE.search(blob) and FORD_HD_SIGNAL.search(blob):
        return True
    return False


def is_ram_deprioritized(blob: str, *, miles_blob: str = "") -> bool:
    """Ram/Dodge trucks — 45RFE and similar; only if willing to maintain."""
    return bool(RAM_SIGNAL.search(blob))


def parse_price_usd(price: str, title: str = "") -> Optional[int]:
    for blob in (price, title):
        m = _PRICE.search(blob or "")
        if not m:
            continue
        try:
            return int(m.group(0).replace("$", "").replace(",", ""))
        except ValueError:
            continue
    return None


def location_band(location: str, *, home_city: str = "Gardner", home_state: str = "CO") -> str:
    loc = (location or "").lower()
    home = (home_city or "").lower()
    if home and home in loc:
        return "near_home"
    if any(c in loc for c in NEAR_HOME_CITIES):
        return "near_home"
    if any(c in loc for c in FRONT_RANGE_CITIES):
        return "front_range"
    if any(c in loc for c in COLORADO_CITIES) or re.search(r"\bco\b|colorado", loc):
        return "colorado"
    if any(c in loc for c in FLORIDA_CITIES):
        return "florida"
    return "other"


def tow_class(blob: str, category_id: str) -> str:
    lower = blob.lower()
    if re.search(HD_TOW, lower, re.I):
        if re.search(r"\b3500|f-?350|ram\s*3500|dually", lower, re.I):
            return "A"
        return "B"
    if re.search(COMMERCIAL_TOW, lower, re.I):
        if re.search(r"\b(diesel|dually|3500|f-?350|reefer|refrigerated|isuzu\s*npr|hino)", lower, re.I):
            return "B"
        return "C"
    if category_id in ("travel_rv", "trailers", "tow_equipment"):
        return "D"
    if re.search(SUV_NOT_HD, lower, re.I) and not re.search(HD_TOW, lower, re.I):
        return "D"
    if re.search(CAR_NOT_TOW, lower, re.I):
        return "D"
    if re.search(LIGHT_TRUCK, lower, re.I):
        return "C"
    if category_id == "trucks" or re.search(r"\btruck|pickup\b", lower, re.I):
        return "C"
    if category_id == "cars":
        return "D"
    return "C"


def is_truck_listing(
    title: str,
    description: str = "",
    category_id: str = "",
    search: Optional[dict] = None,
) -> bool:
    """Pickup / HD / commercial tow rigs — no cars, SUVs, RVs, trailers, parts."""
    title_blob = (title or "").strip()
    desc_blob = (description or "").strip()[:320]
    blob = f"{title_blob} {desc_blob}".strip()
    if not title_blob:
        return False
    if is_hard_avoid_tow_rig(blob):
        return False
    if FB_COMMERCIAL_CATEGORY.search(blob):
        return True
    if NOT_TRUCK.search(blob):
        return False
    if VAN_NOT_TOW.search(blob) and not TRUCK_SIGNAL.search(blob):
        return False
    if category_id in ("trailers", "travel_rv", "tow_equipment", "cars"):
        return False
    if TRUCK_SIGNAL.search(blob):
        return True
    if AUCTION_SIGNAL.search(blob) and re.search(r"\btruck\b", blob, re.I):
        return True
    if category_id == "trucks":
        return True
    if re.search(CAR_NOT_TOW, title_blob, re.I):
        return False
    if re.search(SUV_NOT_HD, title_blob, re.I) and not TRUCK_SIGNAL.search(blob):
        return False
    if search:
        for bucket in search.get("paid_wanted", []) or []:
            if bucket.get("name") not in ("tow_truck", "commercial_tow"):
                continue
            for kw in bucket.get("keywords", []) or []:
                k = str(kw).lower()
                if len(k) > 2 and k in blob.lower():
                    return True
    return False


def _parse_miles_int(blob: str) -> Optional[int]:
    mm = _MILES.search(blob or "")
    if mm:
        try:
            return int(mm.group(1).replace(",", ""))
        except ValueError:
            pass
    mk = _MILES_K.search((blob or "").lower())
    if mk:
        try:
            return int(mk.group(1)) * 1000
        except ValueError:
            pass
    return None


def compute_vehicle_fit(
    title: str,
    description: str = "",
    price: str = "",
    location: str = "",
    category_id: str = "",
    *,
    make_preference: str = "chevy",
    max_price_usd: int = 20000,
    home_city: str = "Gardner",
    home_state: str = "CO",
    search: Optional[dict] = None,
) -> dict[str, Any]:
    """0–100 score: tow capacity, price, Chevy pref, quality signals, location."""
    title_lower = title.lower()
    fields = parse_vehicle_fields(title, description, make_preference=make_preference, title_only_make=True)
    price_usd = parse_price_usd(price, title)
    tc = tow_class(title_lower, category_id)
    band = location_band(location, home_city=home_city, home_state=home_state)
    quality_blob = f"{title} {description[:280]}".strip() if description else title
    is_diesel = bool(re.search(r"\bdiesel\b|duramax|cummins|powerstroke|7\.3", quality_blob, re.I))
    is_dually = bool(re.search(r"\bdually\b|dual\s+rear", quality_blob, re.I))
    is_commercial = bool(re.search(COMMERCIAL_TOW, quality_blob, re.I))
    is_auction = bool(AUCTION_SIGNAL.search(quality_blob))
    grant_credit = bool(GRANT_CREDIT_SIGNAL.search(quality_blob)) or is_auction or fields.get("is_fleet")

    score = 0.0
    if tc == "A":
        score += 42
    elif tc == "B":
        score += 34
    elif tc == "C":
        score += 14
    else:
        score += 2

    if fields.get("make_preferred"):
        score += 18
    elif fields.get("make") == "Ford" and tc in ("A", "B"):
        score += 10

    avoid_ram = is_ram_deprioritized(quality_blob, miles_blob=fields.get("miles") or "")
    if avoid_ram:
        score -= 22
        if re.search(r"\b45rfe\b", quality_blob, re.I):
            score -= 10
        miles_val = _parse_miles_int(quality_blob)
        if miles_val is not None and miles_val >= 150000:
            score -= 12
        elif miles_val is not None and miles_val >= 120000:
            score -= 6

    if price_usd is not None:
        if price_usd <= max_price_usd:
            headroom = max_price_usd - price_usd
            score += 10 + min(10, headroom / 2000)
        elif price_usd <= max_price_usd + 2500:
            score -= 12
        else:
            score -= 35
    elif fields.get("price_display"):
        score += 4

    if fields.get("is_rebuilt"):
        score += 10
    if fields.get("is_fleet"):
        score += 8
    if is_diesel:
        score += 8
    if is_dually:
        score += 6
    if is_commercial and tc in ("A", "B", "C"):
        score += 6
    if is_auction:
        score += 5
    if grant_credit:
        score += 7

    if band == "near_home":
        score += 14
    elif band == "front_range":
        score += 10
    elif band == "colorado":
        score += 5
    elif band == "florida":
        score += 4

    if search:
        pri = [str(k).lower() for k in search.get("priority_keywords", []) or []]
        if any(k in title_lower for k in pri if len(k) > 2):
            score += 4

    if tc == "D":
        score -= 18

    score = int(max(0, min(100, round(score))))
    if score >= 75:
        label = "Top fit"
    elif score >= 55:
        label = "Good fit"
    elif score >= 35:
        label = "Possible"
    else:
        label = "Weak fit"

    return {
        **fields,
        "price_usd": price_usd,
        "location_band": band,
        "fit_tow_class": tc,
        "is_diesel": is_diesel,
        "is_dually": is_dually,
        "is_commercial": is_commercial,
        "is_auction": is_auction,
        "grant_credit_angle": grant_credit,
        "is_hd_tow": tc in ("A", "B"),
        "avoid_ram": avoid_ram,
        "avoid_ford_60": is_hard_avoid_tow_rig(quality_blob),
        "fit_score": score,
        "fit_label": label,
    }


def _first_match(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def parse_vehicle_fields(
    title: str,
    description: str = "",
    *,
    make_preference: str = "",
    title_only_make: bool = False,
) -> dict:
    blob = f"{title} {description}".strip()
    make_blob = title if title_only_make else blob
    lower = blob.lower()
    make_lower = make_blob.lower()

    year = ""
    ym = _YEAR.search(blob)
    if ym:
        year = ym.group(1)

    miles = ""
    mm = _MILES.search(blob)
    if mm:
        miles = mm.group(0).strip()
    else:
        mk = _MILES_K.search(lower)
        if mk:
            miles = f"{mk.group(1)}k mi"

    make = ""
    for label, patterns in MAKE_PATTERNS:
        if _first_match(make_lower, patterns):
            make = label
            break

    model = ""
    for pat, label in MODEL_HINTS:
        if re.search(pat, make_lower, re.I):
            model = label
            break

    pref = (make_preference or "").lower()
    make_preferred = False
    if pref in ("chevy", "chevrolet", "gm"):
        make_preferred = make in ("Chevrolet", "GMC") or _first_match(
            make_lower, [r"\bchev", r"\bsilverado", r"\bgmc\b", r"\bsierra", r"\b2500hd", r"\b3500hd"]
        )

    price_num = ""
    pm = _PRICE.search(title)
    if pm:
        price_num = pm.group(0)

    quality_blob = f"{title} {description[:280]}".strip() if description else title
    return {
        "year": year,
        "make": make,
        "model": model,
        "miles": miles,
        "make_preferred": make_preferred,
        "price_display": price_num,
        "is_rebuilt": bool(re.search(
            r"rebuilt|reman|refurbished|new engine|new trans|overhauled",
            quality_blob,
            re.I,
        )),
        "is_fleet": bool(re.search(
            r"fleet|municipal|utility fleet|city truck|county truck|work truck",
            quality_blob,
            re.I,
        )),
    }


VEHICLE_CATEGORY_IDS = frozenset({
    "trucks", "paid_wanted",
})


def is_vehicle_listing(title: str, description: str, category_id: str, search: dict) -> bool:
    return is_truck_listing(title, description, category_id, search)
