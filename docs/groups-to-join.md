# Groups to join — Gardner Farm / Southern CO / Denver corridor

Skout can **search** public pages and APIs, but **replying** almost always requires membership on that platform. This guide ranks what to join by activity for your routes (Gardner → Pueblo → COS → Denver).

---

## Freecycle (join at [freecycle.org](https://www.freecycle.org))

Skout reads public town pages. Joining unlocks member-only posts and lets you **reply on Freecycle**.

| Town slug | Members | Public offers (sample scan) | Priority |
|-----------|---------|----------------------------|----------|
| **DenverCO** | ~8,500 | 35 | **Must join** — highest volume |
| **ColoradoSpringsCO** | ~9,200 | 25 | **Must join** — COS trips |
| **ArvadaCO** | ~3,500 | 13 | **Join** — Denver metro west |
| **AuroraCO** | ~3,300 | 13 | **Join** — Denver metro east |
| **PuebloCO** | ~1,500 | 3 | **Join** — home base |
| **North_Denver_CO** | ~3,700 | 0* | Join if you travel I-25 north |
| **CanonCityCO** | ~500 | 2 | Optional — Royal Gorge route |
| **ElPasoCountySE_CO** | ~170 | 1 | Optional — Fountain / SE COS |
| coloradocity / WestcliffeCO | &lt;150 | 0 | Low activity — skip unless local |

\*Zero on public page ≠ dead group; join to see member feed.

**Also worth joining manually** (add slug to `sources.yaml` if you join): `WheatRidge-EdgewaterCO`, `CommerceCityCO`, `EnglewoodCO` — linked as “nearby towns” on Denver’s page.

---

## Trash Nothing ([trashnothing.com](https://trashnothing.com))

**No per-group joining** for search — one API key covers Freecycle + Buy Nothing groups in a radius.

1. Create account → [Developer API](https://trashnothing.com/developer) → register app
2. Add `TRASHNOTHING_API_KEY=...` to `.env`
3. Skout searches 80-mile radius from Gardner (COS + Denver included)

Trash Nothing is the easiest way to cover **many** Buy Nothing / Freecycle groups without joining each one individually — but you still reply **on Trash Nothing** or the underlying group.

---

## Facebook — Marketplace + groups

**Requires:** `.venv/bin/python src/scrapers/facebook.py --login` then `--discover-groups`

You must **join groups in Facebook**. Skout discovers IDs from your joined list (no URL pasting).

### High priority — join these

| Group | Region | Why |
|-------|--------|-----|
| **Pueblo Colorado Free Stuff** | Pueblo | Direct freebies on your home corridor |
| **Colorado Springs Free Stuff** | COS | High volume for Jun 20 COS trip |
| **Buy Nothing Pueblo** | Pueblo | Gift economy; furniture, kids, household |
| **Buy Nothing — Walsenburg / Huerfano** | Gardner area | Closest Buy Nothing to Gardner |
| **Southern Colorado Buy Sell Trade** | I-25 south | Free + cheap farm/garden gear |

### Denver / metro — join for Sunday Denver trip

Search Facebook for **“Buy Nothing” + neighborhood** (Briargate, Stapleton, Highlands, Arvada, Aurora, etc.). KOAA reported active COS Buy Nothing groups in Briargate, Woodmen, Black Forest, central/east COS — join the ones matching your routes.

Also join in Facebook (then re-run `--discover-groups`):

- **Denver Free Stuff** (search exact name — several variants exist)
- **Denver Buy Nothing** / **Buy Nothing Denver** neighborhood groups
- **Arvada Buy Nothing** / **Aurora Buy Nothing**

### Already in `facebook_groups.yaml` (auto-matched when joined)

- Colorado Local Food & Regenerative Ag Hub
- La Veta / Cuchara community
- Southern Colorado Homesteading
- Pueblo Colorado Gardening
- Walsenburg Farm and Makers Market

---

## Nextdoor

**Not group-based** — you’re tied to **verified home neighborhoods**. To see Gardner + COS + Denver freebies you typically need:

1. **Verified address** in each neighborhood you care about, **or**
2. **Nextdoor Content API** (apply at [developer.nextdoor.com](https://developer.nextdoor.com)) — set `NEXTDOOR_CLIENT_ID` + `NEXTDOOR_CLIENT_SECRET` in `.env`, **or**
3. **Logged-in scrape:** `.venv/bin/python src/scrapers/nextdoor.py --login`

**Recommended:** Verify in **Gardner/Walsenburg**, **Colorado Springs**, and **Denver** neighborhoods if you have access (home, family, rental). API access is best for scanning multiple cities without multi-hood membership.

Contact: **in-app message only** — Skout opens the listing and copies your pickup text.

---

## OfferUp

**No groups** — geo search by zip + radius. Already works without login.

- Skout searches zip `81040`, 50-mile radius
- Free items appear with $0 price or “free” in title
- Contact: **OfferUp in-app chat** — Skout opens listing + copies message

Widen radius in `sources.yaml` if you want more Denver listings (tradeoff: more distant pickups).

---

## Can you email from Skout?

| Source | Email from Skout? | What Skout does |
|--------|-------------------|-----------------|
| **Craigslist** | Sometimes | Opens `mailto:` if relay address found; else opens Craigslist reply page |
| **Freecycle** | No | Opens post on freecycle.org + copies message |
| **Trash Nothing** | No | Opens post on trashnothing.com + copies message |
| **Facebook** | No | Opens group/Marketplace + copies message |
| **Nextdoor** | No | Opens For Sale & Free listing + copies message |
| **OfferUp** | No | Opens listing + copies message |

**Why:** These platforms hide seller email on purpose (spam/scam prevention). Skout’s **Contact** button always copies your pickup message first, then opens the best contact channel without leaving the dashboard.

True “send email without opening anything” would require an SMTP server and a known recipient address — only Craigslist occasionally exposes a relay. A future option is optional SMTP in `.env` for when `reply_email` exists.

---

## Quick setup checklist

```bash
# Trash Nothing
# → TRASHNOTHING_API_KEY in .env

# Facebook
.venv/bin/python src/scrapers/facebook.py --login
.venv/bin/python src/scrapers/facebook.py --discover-groups

# Nextdoor (pick one)
# → NEXTDOOR_CLIENT_ID + NEXTDOOR_CLIENT_SECRET in .env
# OR:
.venv/bin/python src/scrapers/nextdoor.py --login

# Freecycle — join towns at freecycle.org (see table above)

# Rerun
.venv/bin/python src/main.py --all --open
```
