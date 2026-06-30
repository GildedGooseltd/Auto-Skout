"""Discover Facebook group IDs automatically from joined groups + search hints."""

from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

GROUP_ID_RE = re.compile(r"/groups/(\d{6,})")
CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "facebook_groups_cache.json"

_NAME_JSON_RE = re.compile(r'"name":"((?:[^"\\]|\\.)*)"')
_ID_JSON_RE = re.compile(r'"id":"(\d{8,})"')


def _decode_json_str(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"').strip()
    except json.JSONDecodeError:
        return raw.strip()


def extract_groups_from_text(text: str) -> Dict[str, str]:
    """Return {group_id: name} from HTML or GraphQL payload fragments."""
    found: Dict[str, str] = {}

    for gid in GROUP_ID_RE.findall(text or ""):
        found.setdefault(gid, "")

    for m in re.finditer(
        r'"name":"((?:[^"\\]|\\.)*)".{0,500}?"id":"(\d{8,})"',
        text or "",
    ):
        name = _decode_json_str(m.group(1))
        found[m.group(2)] = name or found.get(m.group(2), "")

    for m in re.finditer(
        r'"id":"(\d{8,})".{0,500}?"name":"((?:[^"\\]|\\.)*)"',
        text or "",
    ):
        name = _decode_json_str(m.group(2))
        found[m.group(1)] = name or found.get(m.group(1), "")

    for m in re.finditer(
        r'"groupID":"(\d{8,})".{0,300}?"name":"((?:[^"\\]|\\.)*)"',
        text or "",
    ):
        name = _decode_json_str(m.group(2))
        found[m.group(1)] = name or found.get(m.group(1), "")

    return found


def search_query_from_url(url: str) -> str:
    if not url or "/search/groups" not in url:
        return ""
    qs = parse_qs(urlparse(url).query)
    raw = qs.get("q", [""])[0]
    return unquote(raw).strip()


def match_hint(name: str, hint: str) -> float:
    if not hint:
        return 0.0
    a = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    b = re.sub(r"[^a-z0-9]+", " ", hint.lower()).strip()
    if not a or not b:
        return 0.0
    if b in a or a in b:
        return 1.0
    words = [w for w in b.split() if len(w) > 2]
    if words and sum(1 for w in words if w in a) / len(words) >= 0.6:
        return 0.85
    return SequenceMatcher(None, a, b).ratio()


def keywords_match(name: str, include: List[str], exclude: List[str]) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    if any(k.lower() in text for k in exclude if k):
        return False
    if not include:
        return True
    return any(k.lower() in text for k in include if k)


def _scroll_collect(page, *, scrolls: int = 12, pause_ms: int = 700) -> Dict[str, str]:
    collected: Dict[str, str] = {}
    bodies: List[str] = []

    def on_response(resp):
        try:
            url = resp.url or ""
            if not any(x in url for x in ("graphql", "ajax", "api/graphql", "/search/")):
                return
            body = resp.text()
            if body and ("group" in body.lower() or "/groups/" in body):
                bodies.append(body)
        except Exception:
            pass

    page.on("response", on_response)
    for _ in range(scrolls):
        page.mouse.wheel(0, 2200)
        page.wait_for_timeout(pause_ms)

    html = page.content()
    collected.update(extract_groups_from_text(html))
    for body in bodies:
        collected.update(extract_groups_from_text(body))

    dom = page.evaluate(
        """() => {
        const out = [];
        for (const a of document.querySelectorAll('a[href*="/groups/"]')) {
          const href = a.href || a.getAttribute('href') || '';
          const m = href.match(/\\/groups\\/(\\d{6,})/);
          if (!m) continue;
          const text = (a.innerText || a.getAttribute('aria-label') || '').trim();
          out.push({ id: m[1], text });
        }
        return out;
      }"""
    )
    for row in dom or []:
        collected[row["id"]] = row.get("text") or collected.get(row["id"], "")

    return collected


def discover_joined_groups(page) -> Dict[str, str]:
    """All groups on the user's Joined Groups page."""
    urls = [
        "https://www.facebook.com/groups/joins/",
        "https://www.facebook.com/groups/feed/",
        "https://www.facebook.com/bookmarks/groups",
    ]
    joined: Dict[str, str] = {}
    for url in urls:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)
        batch = _scroll_collect(page)
        joined.update(batch)
        if len(joined) >= 3:
            break
    return joined


def resolve_search_groups(page, query: str) -> Dict[str, str]:
    if not query:
        return {}
    url = f"https://www.facebook.com/search/groups/?q={query.replace(' ', '%20')}"
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3500)
    return _scroll_collect(page)


def load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def save_cache(payload: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(payload, indent=2))


def cache_stale(cfg: dict) -> bool:
    discovery = cfg.get("facebook_groups", {}).get("discovery", {})
    hours = discovery.get("cache_hours", 24)
    cache = load_cache()
    ts = cache.get("updated_at", 0)
    return (time.time() - ts) > (hours * 3600)


def build_group_list(cfg: dict, page, *, force: bool = False) -> List[dict]:
    """
    Resolve scrapeable groups automatically.
    Uses joined-group discovery + search-hint matching; writes cache file.
    """
    groups_cfg = cfg.get("facebook_groups", {})
    discovery = groups_cfg.get("discovery", {})
    include_kw = discovery.get("include_keywords", [])
    exclude_kw = discovery.get("exclude_keywords", [])
    auto_joined = discovery.get("auto_all_joined", True)

    if not force and not cache_stale(cfg):
        cached = load_cache().get("groups", [])
        if cached:
            return cached

    joined = discover_joined_groups(page)
    resolved: Dict[str, dict] = {}

    for entry in groups_cfg.get("groups", []):
        name = entry.get("name", "")
        url = entry.get("url", "")
        hint = entry.get("match") or search_query_from_url(url) or name
        gid = ""
        m = GROUP_ID_RE.search(url or "")
        if m:
            gid = m.group(1)

        if not gid and joined:
            best_id, best_score = "", 0.0
            for jgid, jname in joined.items():
                score = match_hint(jname, hint)
                if score > best_score:
                    best_id, best_score = jgid, score
            if best_score >= 0.55:
                gid = best_id
                name = joined.get(gid, name)

        if not gid and hint:
            for sgid, sname in resolve_search_groups(page, hint).items():
                score = match_hint(sname, hint)
                if score >= 0.45:
                    gid = sgid
                    name = sname or name
                    break

        if gid:
            resolved[gid] = {
                "id": gid,
                "name": name or joined.get(gid, gid),
                "url": f"https://www.facebook.com/groups/{gid}",
                "region": entry.get("region", ""),
                "priority": entry.get("priority", "medium"),
                "source": "configured",
            }

    if auto_joined:
        for gid, gname in joined.items():
            if gid in resolved:
                continue
            if keywords_match(gname, include_kw, exclude_kw):
                resolved[gid] = {
                    "id": gid,
                    "name": gname or gid,
                    "url": f"https://www.facebook.com/groups/{gid}",
                    "region": "",
                    "priority": "medium",
                    "source": "auto_joined",
                }

    groups = sorted(resolved.values(), key=lambda g: g.get("name", "").lower())
    save_cache(
        {
            "updated_at": time.time(),
            "joined_found": len(joined),
            "groups": groups,
        }
    )
    return groups
