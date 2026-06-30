"""Match listing locations to drive routes and destination cities."""


def _location_matches(location: str, cities: list[str]) -> bool:
    loc = (location or "").lower()
    return any(city.lower() in loc for city in cities if city)


def cities_toward_destination(dest: str, routes_cfg: dict) -> list[str]:
    """Cities from route start through the destination city (inclusive)."""
    needle = (dest or "").strip().lower()
    if not needle:
        return []

    for route in routes_cfg.get("routes", {}).values():
        cities = route.get("cities", [])
        for i, city in enumerate(cities):
            if needle in city.lower() or city.lower() in needle:
                return cities[: i + 1]
    return [dest.strip()]


def match_destination(location: str, dest: str, routes_cfg: dict) -> bool:
    if not dest or not dest.strip():
        return True
    cities = cities_toward_destination(dest, routes_cfg)
    return _location_matches(location, cities)


def match_routes(location: str, routes_cfg: dict) -> dict:
    """Match listing location against profile route definitions."""
    routes = routes_cfg.get("routes", {})
    matched_ids = []
    tags: set[str] = set()

    for route_id, route in routes.items():
        if _location_matches(location, route.get("cities", [])):
            matched_ids.append(route_id)
            tags.update(route.get("tags", []))

    flags = {tag: True for tag in tags}
    flags["route_ids"] = matched_ids
    return flags
