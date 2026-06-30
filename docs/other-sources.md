# Connect Skout to sources beyond Craigslist

All **enabled** sources in `sources.yaml` run together each scan — one combined feed. Enable Craigslist, Freecycle, and/or Facebook as you set them up.

---

## Craigslist (working)

```yaml
# profiles/gardner-farm/sources.yaml
craigslist:
  enabled: true
  regions:
    - slug: pueblo
      name: Pueblo
```

No extra login required.

---

## Freecycle

**What it is:** Neighbors giving away items in local town groups.

**Setup:**

1. Join groups at [freecycle.org](https://www.freecycle.org) for your towns (membership may be required to see posts).
2. In `sources.yaml`:

```yaml
freecycle:
  enabled: true
  groups: [HuerfanoCountyCO, PuebloCO, coloradocity]
```

3. Run a scan — posts appear with the ♻️ badge.

**Town slugs** (in `src/scrapers/freecycle.py`): `PuebloCO`, `ColoradoSpringsCO`, `CanonCityCO`, `coloradocity`. Old `/posts/offers` URLs no longer work — Skout uses `/town/{slug}`.

**Limits:** Only OFFER posts are included (no ISO/wanted). Some towns have zero posts on the public page.

---

## Facebook Marketplace

**What it is:** Local free/cheap listings; requires your Facebook login.

**One-time setup:**

```bash
cd ~/free-stuff-alerts
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
.venv/bin/python src/scrapers/facebook.py --login
```

A browser opens — log into Facebook. Session saves to `data/facebook_state.json` (gitignored).

**Discover Facebook groups automatically (no manual URL copying):**

```bash
.venv/bin/python src/scrapers/facebook.py --discover-groups
```

Skout reads your **joined groups** page, matches names to the `match:` hints in `facebook_groups.yaml`, and caches numeric `/groups/ID` URLs in `data/facebook_groups_cache.json`. Re-runs every 24h (configurable). You only need to join groups in Facebook — Skout finds the IDs.

**Enable in profile:**

```yaml
facebook_marketplace:
  enabled: true
  location_zip: "81040"
```

**Limits:** Facebook blocks automated access; session expires periodically. Re-run `--login` when Skout stops finding FB posts. Planned for Phase 3 as a Pro feature.

---

## OfferUp

**What it is:** Local marketplace; Skout parses public search results (no login).

```yaml
offerup:
  enabled: true
  zip: "81040"
  radius: 50
  search_terms: [free, garden, farm, pallet]
```

Contact sellers via **OfferUp in-app chat** — Skout copies your message and opens the listing.

---

## Nextdoor

**What it is:** Hyper-local For Sale & Free listings.

**Option A — Content API (best for multi-city scans):**

1. Apply at [developer.nextdoor.com](https://developer.nextdoor.com/reference/applying-for-access)
2. Add to `.env`:
   ```
   NEXTDOOR_CLIENT_ID=...
   NEXTDOOR_CLIENT_SECRET=...
   ```

**Option B — Logged-in scrape:**

```bash
.venv/bin/python src/scrapers/nextdoor.py --login
```

---

## Trash Nothing

Aggregates Freecycle + Buy Nothing groups. Requires API key:

```
TRASHNOTHING_API_KEY=...
```

Register at [trashnothing.com/developer](https://trashnothing.com/developer).

See **docs/groups-to-join.md** for which Freecycle towns and Facebook groups to join.

---

## Not built yet

| Source | Status |
|--------|--------|
| Buy Nothing (standalone) | Covered via Facebook groups + Trash Nothing |

**Denver metro Freecycle** (added Jun 2026): `DenverCO`, `North_Denver_CO`, `ArvadaCO`, `AuroraCO` — join at freecycle.org to see member-only posts.

---

**Already scanning when working:** Craigslist (5 regions), Freecycle (10+ towns), OfferUp (zip search), Facebook Marketplace + Groups (needs `--login`), Trash Nothing (API key), Nextdoor (API key or `--login`).

---

## Recommended order

1. **Craigslist** — use daily (Phase 1)
2. **Freecycle** — enable when you've joined local groups
3. **Facebook** — enable when you need broader coverage and accept re-login maintenance
