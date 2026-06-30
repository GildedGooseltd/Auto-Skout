"""Buy Nothing — mostly on Facebook; use FB groups + Trash Nothing until dedicated scraper."""

from typing import List

from scrapers.craigslist import RawListing

_warned = False


def fetch_offers(_cfg: dict) -> List[RawListing]:
    global _warned
    if not _warned:
        print(
            "  buy_nothing: join groups on Facebook (Skout --discover-groups) or Trash Nothing API",
            flush=True,
        )
        _warned = True
    return []
