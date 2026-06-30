# Skout product roadmap

## Product definition

**Skout** is a personalized free-and-cheap local alerts platform. Users define what they want, where they are, how far they'll drive, and which routes they travel. Skout watches marketplace sources, filters noise, and surfaces matches on a mobile web dashboard.

**Not:** a generic Craigslist reader.  
**Is:** configurable hunt rules + route-aware filtering + category scoring.

**Business model (target):** subscription SaaS, regional/vertical agnostic.  
**Exit goal:** small profitable SaaS or acquisition ($15k–$500k+ depending on MRR).

---

## Guiding principles

1. **Profile = user** — every tester gets a profile config; later that becomes a DB row.
2. **Scrape once, filter many** — regional scrapes are shared; scoring is per user.
3. **Ship the dashboard first** — the product people see and pay for is the filtered feed.
4. **Reliability > sources** — one working source beats five broken ones.
5. **Beta feedback drives Phase 2** — don't build auth/Stripe until 3–5 people use Phase 1 weekly.

---

## Phase 1 — Private beta (3–5 testers)

**Goal:** Prove strangers will use Skout weekly and say they'd pay for it.

**Duration:** 4–6 weeks build + 4 weeks beta feedback.

### In scope

| Area | Deliverable |
|------|-------------|
| **Hosting** | `skout.gildedgoosegarage.com` live on Netlify |
| **Reliability** | Craigslist scraper stable; empty runs are rare |
| **Multi-profile** | You onboard each tester manually (`profiles/their-name/`) |
| **Core filters** | Keywords, excludes, categories with icons, route filters |
| **Sources** | Craigslist only (v1 beta) — one source done well |
| **Updates** | Cloud worker OR you run + deploy 2×/day minimum (not Mac-only) |
| **Feedback** | Simple form link on dashboard footer (Google Form / Tally) |
| **Onboarding** | 15-min intake: ZIP, what they hunt, excludes, route cities |

### Out of scope (Phase 1)

- User self-signup / login
- Stripe / payments
- SMS alerts
- Facebook / Freecycle (unless trivially working)
- Resale profit calculator UI
- Native app
- Public marketing site

### Phase 1 tech stack

| Piece | Choice |
|-------|--------|
| Dashboard | Static site (`site/index.html`) — already built |
| Profiles | YAML in `profiles/` — you edit per tester |
| Worker | Railway cron running `src/main.py` → deploy to Netlify |
| Deploy | Netlify CLI or drag-and-drop after each run |
| DB | None — `seen.db` per profile file on worker |
| Auth | Secret URL per tester optional (`?key=`) or public beta with profile slug |

### Phase 1 success metrics

| Metric | Target |
|--------|--------|
| Active testers | 3–5 using it weekly |
| Useful finds reported | ≥2 per tester over 4 weeks |
| Would pay $7–12/mo | ≥2 of 5 say yes |
| Scraper uptime | Dashboard updates daily without you debugging |
| Time to onboard new tester | <30 min (copy template + intake) |

### Phase 1 build order

```
Week 1–2
  [ ] Fix Craigslist parser regression tests
  [ ] Railway worker + scheduled runs (2× daily min)
  [ ] Netlify auto-deploy from worker
  [ ] Per-profile output: site/{profile_id}/index.html OR token URLs
  [ ] Beta feedback link on dashboard

Week 3
  [ ] Onboarding checklist + intake template
  [ ] Recruit 3–5 testers (flippers, homesteaders, haulers — not all identical to you)
  [ ] Second profile besides gardner-farm live

Week 4–6
  [ ] Collect feedback weekly
  [ ] Fix top 3 pain points
  [ ] Go/no-go for Phase 2
```

### Phase 1 open decisions

- [ ] One shared dashboard with profile switcher vs separate URL per tester?
- [ ] Free beta vs "pay what you want" during test?
- [ ] Geographic spread: all Southern CO or one tester in another state to prove vertical?

---

## Phase 2 — Self-serve beta (10–30 users)

**Goal:** Users sign up and configure themselves; first paying subscribers.

**Trigger:** Phase 1 success metrics met.

### In scope

| Area | Deliverable |
|------|-------------|
| **Auth** | Email login (Supabase Auth or Clerk) |
| **Settings UI** | Web form replaces YAML for: ZIP, keywords, excludes, routes |
| **Database** | Postgres: users, profiles, listings, seen |
| **Payments** | Stripe — one plan (~$9/mo), 14-day trial |
| **Worker** | Shared regional scrape → score all active profiles |
| **Email** | Weekly digest (Resend) |
| **Admin** | You see users, MRR, last scrape status |

### Out of scope

- SMS
- Facebook per-user
- Native app
- Multiple pricing tiers

### Phase 2 success metrics

| Metric | Target |
|--------|--------|
| Paying subscribers | 10+ |
| MRR | $90+ |
| Churn (monthly) | <15% |
| Self-serve signup completion | >60% who start finish onboarding |

---

## Phase 3 — Growth product (30–100 users)

**Goal:** Retention, differentiation, reduce support burden.

### In scope

- **SMS alerts** (Twilio, pro tier, urgent-only, daily cap)
- **Trip calendar UI** — date ranges boost route filters
- **Second source** — Facebook Marketplace (shared session, pro tier) or Freecycle
- **Resale / drive score** on each card ("worth the trip")
- **Pricing tiers** — Free (1 region, weekly) / Pro ($12) / Pro+ SMS ($18)
- **Regional scrape optimization** — don't re-scrape unchanged regions

---

## Phase 4 — Sellable SaaS (100+ users or $1k+ MRR)

**Goal:** Business runs without daily manual intervention; acquirable.

### In scope

- Multi-vertical templates (farm, flip, retail clearance, equipment)
- White-label / group plans (Buy Nothing chapter, flipper community)
- Status page + monitoring
- Legal: ToS, privacy, SMS opt-in compliance
- LLC + Stripe atlas bookkeeping
- List on Acquire.com / MicroAcquire when MRR justifies

---

## Architecture evolution

```
Phase 1   YAML profiles + static site + Railway worker
Phase 2   Postgres profiles + Next.js app + Stripe
Phase 3   + SMS + shared scrape cache + tier gating
Phase 4   + multi-tenant admin + vertical templates + API
```

---

## Revenue model (target)

| Tier | Price | Includes |
|------|-------|----------|
| Beta | Free | Phase 1 testers, feedback in exchange |
| Starter | $7/mo | 1 region, daily refresh, email weekly |
| Pro | $12/mo | Multi-region, route filters, 2× daily, email daily |
| Pro+ | $18/mo | Pro + SMS urgent (3/day cap) |

Adjust after Phase 1 feedback.

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Craigslist breaks parser | HTML fallback tests; alert if 0 listings |
| Nobody pays | Phase 1 validates before Stripe build |
| Support overload | Limit beta to 5; strict onboarding template |
| Facebook scaling | Defer to Phase 3; CL-only is viable product |
| Legal (scraping) | ToS for users; don't resell raw CL data; curated links only |

---

## Immediate next action

**Start Phase 1, Week 1:**

1. Railway worker + Netlify deploy pipeline  
2. Per-profile dashboard URLs  
3. Deploy `gardner-farm` live at `skout.gildedgoosegarage.com`  
4. Draft tester intake form (ZIP, hunt list, excludes, route)

When Phase 1 Week 1 is done, recruit first external tester.

---

## Document map

| Doc | Purpose |
|-----|---------|
| `ROADMAP.md` | This file — product phases |
| `ARCHITECTURE.md` | Technical layers |
| `squarespace-deploy.md` | Domain + Netlify DNS |
| `profiles/_template/` | New user/tester config |
