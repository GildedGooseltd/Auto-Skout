#!/usr/bin/env python3
"""Art Scout — scan curated art/grant feeds (Skout profile kate-art).

Usage:
  cd free-stuff-alerts
  .venv/bin/python scripts/art_opportunity_hunt.py

Writes:
  ../Documents/1 Cursor Helper/daily/briefings/ART-OPPORTUNITIES.md
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles" / "kate-art"
OUT = ROOT.parent / "Documents" / "1 Cursor Helper" / "daily" / "briefings" / "ART-OPPORTUNITIES.md"

EXCLUDE = re.compile(
    r"pollock|pkf\.org|entry fee:\s*\$[1-9]|ship (your )?art|mail (your )?art|watercolor only",
    re.I,
)
BOOST = re.compile(
    r"oil|painting|stipend|grant|sponsorship|residency|free to enter|no entry fee|colorado",
    re.I,
)


def fetch(url: str, timeout: int = 25) -> str:
    req = Request(url, headers={"User-Agent": "ArtScout/1.0 (Kate art finder)"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_snippets(html: str, limit: int = 40) -> list[tuple[str, str]]:
    """Rough extract: links + nearby text."""
    hits: list[tuple[str, str]] = []
    for m in re.finditer(r'href="(https?://[^"]+)"[^>]*>([^<]{4,120})', html):
        url, title = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        blob = title.lower()
        if EXCLUDE.search(blob):
            continue
        if BOOST.search(blob) or BOOST.search(url.lower()):
            hits.append((title, url))
        if len(hits) >= limit:
            break
    return hits


def main() -> int:
    sources_yaml = PROFILE / "sources.yaml"
    if not sources_yaml.exists():
        print("Missing profile:", PROFILE, file=sys.stderr)
        return 1

    import yaml  # noqa: PLC0415

    cfg = yaml.safe_load(sources_yaml.read_text())
    feeds = cfg.get("feeds", [])
    lines = [
        f"# Art opportunities — {date.today().isoformat()}",
        "",
        "Auto-scan (Art Scout / kate-art). **Verify deadlines on source site.**",
        "Rules: oil · $0 entry (or grant-sponsored) · no mail-in · [KATE-CONTEXT.md](../KATE-CONTEXT.md)",
        "",
    ]
    for feed in feeds:
        name = feed.get("name", feed.get("url", "?"))
        url = feed["url"]
        lines.append(f"## {name}")
        lines.append(f"Source: {url}")
        lines.append("")
        try:
            html = fetch(url)
            snippets = extract_snippets(html)
            if not snippets:
                lines.append("- *(No keyword hits this run — check site manually)*")
            for title, link in snippets[:15]:
                lines.append(f"- [{title}]({link})")
        except Exception as e:
            lines.append(f"- *Fetch failed: {e}*")
        lines.append("")

    lines.append("---")
    lines.append("*Manual sweep: [ART-OPPORTUNITY-FINDER.md](../../gilded-goose/ART-OPPORTUNITY-FINDER.md)*")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
