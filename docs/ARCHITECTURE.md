# Skout architecture — test case now, sellable product later

## Vision

Skout is a **profile-based alert platform**: each user (or test case) defines what they want, where, and how far they'll travel. The engine scrapes sources, scores listings, and publishes a mobile web dashboard.

**Gardner farm buildout** = first profile (`profiles/gardner-farm/`).  
**Future subscriber** = same folder shape, stored in Postgres as JSON.

---

## Layers

```
┌─────────────────────────────────────────┐
│  site/index.html  (mobile dashboard)    │
├─────────────────────────────────────────┤
│  Filter engine (scoring.py)             │
│  Categories (categories.yaml)           │
├─────────────────────────────────────────┤
│  Profile (profiles/*/profile.yaml)      │
│  Keywords, routes, sources, trips     │
├─────────────────────────────────────────┤
│  Scrapers (craigslist, freecycle, fb)    │
├─────────────────────────────────────────┤
│  Platform (config/platform.yaml)        │
└─────────────────────────────────────────┘
```

---

## Profile = future user account

| File | Purpose |
|------|---------|
| `profile.yaml` | Home ZIP, economics, schedule, haul |
| `search_criteria.yaml` | Keywords, excludes, paid wants, condition rules |
| `sources.yaml` | Which platforms + regions |
| `trip_routes.yaml` | Route cities + filter tags |
| `travel_calendar.yaml` | Where you are when |
| `scoring.yaml` | Optional score overrides |
| `facebook_groups.yaml` | Group watch list |

Copy `profiles/_template/` to create a new vertical (flip, retail, equipment, etc.).

```bash
SKOUT_PROFILE=gardner-farm .venv/bin/python src/main.py --test --open
```

---

## Sellable product path

| Phase | Now | Later |
|-------|-----|-------|
| Config | YAML profiles | DB per `user_id` |
| Run | Mac cron | Railway worker |
| Output | Static site → Netlify | Per-user URL or shared app |
| Auth | — | Supabase / Clerk |
| Pay | — | Stripe |
| SMS | — | Twilio (pro) |
| Scrape | Per profile regions | **Regional shared scrape**, filter per user |

**Key scalability rule:** scrape each Craigslist region **once**, score **per profile**. 1,000 users in Pueblo ≠ 1,000 CL requests.

---

## What makes it acquirable eventually

- Recurring revenue (Stripe MRR)
- Multi-vertical profiles (not just homesteaders)
- Cloud worker (not Mac-dependent)
- Documented scraper + filter engine
- Domain + brand

---

## Repo layout

```
config/           Platform defaults (categories, scoring defaults, platform.yaml)
profiles/
  gardner-farm/   Your test case
  _template/      New profile starter
src/
  scrapers/       Source plugins
  main.py         Runner
  site_builder.py Web output
site/             Deploy this folder to Netlify
```
