from datetime import date
from typing import Optional, Tuple
import re


def trip_id(trip: dict, index: int) -> str:
    tid = re.sub(r"[^a-z0-9]+", "-", trip.get("name", "trip").lower()).strip("-") or "trip"
    return f"{tid}-{index}"


def resolve_trip(travel: dict, query: str, *, today: Optional[date] = None) -> Optional[Tuple[dict, str]]:
    """Find a trip by partial name/city match; prefer the soonest upcoming."""
    today = today or date.today()
    q = query.lower().strip()
    if not q:
        return None
    matches: list[tuple[dict, int]] = []
    for i, trip in enumerate(travel.get("trips", [])):
        name = (trip.get("name") or "").lower()
        city = (trip.get("city") or "").lower()
        if q in name or q in city:
            matches.append((trip, i))
    if not matches:
        return None

    def sort_key(item: tuple[dict, int]) -> tuple:
        trip, _ = item
        start = date.fromisoformat(trip["start"])
        end = date.fromisoformat(trip["end"])
        past = 1 if end < today else 0
        return (past, start)

    matches.sort(key=sort_key)
    trip, idx = matches[0]
    loc = {**trip, "name": trip.get("name", trip.get("city", ""))}
    return loc, trip_id(trip, idx)


def active_location(travel: dict, today: Optional[date] = None) -> dict:
    today = today or date.today()
    trips = travel.get("trips", [])
    for trip in trips:
        start = date.fromisoformat(trip["start"])
        end = date.fromisoformat(trip["end"])
        if start <= today <= end:
            return {**trip, "name": trip.get("name", trip.get("city", ""))}
    fallback = trips[0] if trips else {}
    return {**fallback, "name": fallback.get("name", fallback.get("city", ""))}
