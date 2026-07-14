# Skout monorepo — shared engine, separate apps per profile

Profile-based local marketplace scanner. Builds mobile-friendly dashboards per app.

## Apps (same repo, different profiles)

| App | Profile | Publish path | URL |
|-----|---------|--------------|-----|
| **Skout** | `gardner-farm` | `/skout/` | https://gildedgooseltd.github.io/Auto-Skout/skout/ |
| **Auto Skout** | `kate-vehicles` | `/auto-skout/` | https://gildedgooseltd.github.io/Auto-Skout/auto-skout/ |
| **Estate Skout** | `estate-skout` | `/estate-skout/` | https://gildedgooseltd.github.io/Auto-Skout/estate-skout/ |

Hub (pick an app): https://gildedgooseltd.github.io/Auto-Skout/

Each dashboard has **tabs** at the top of the sidebar to switch between Skout, Auto Skout, and Estate Skout (when published side-by-side on GitHub Pages).

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium

# Skout — farm & free stuff
SKOUT_PROFILE=gardner-farm .venv/bin/python src/main.py --all --open

# Auto Skout — used trucks (CO + FL)
SKOUT_PROFILE=kate-vehicles .venv/bin/python src/main.py --all --open

# Estate Skout — estate / yard / moving sales
SKOUT_PROFILE=estate-skout .venv/bin/python src/main.py --all --open
```

Copy `.env.example` → `.env` (add `GITHUB_TOKEN` for publish).

## Publish to the web

```bash
./scripts/publish-github.sh gardner-farm    # Skout only → /skout/
./scripts/publish-github.sh kate-vehicles   # Auto Skout only → /auto-skout/
./scripts/publish-github.sh estate-skout    # Estate Skout only → /estate-skout/
```

Publishing one app **does not** overwrite the other. See **[docs/github-pages.md](docs/github-pages.md)**.

## Profiles

| Profile | App |
|---------|-----|
| `gardner-farm` | Skout — farm freebies + buildout |
| `kate-vehicles` | Auto Skout — trucks ≤$20k |
| `estate-skout` | Estate Skout — estate & yard sales |
| `kate-art` | Art Scout — grants & opportunities |

Add your own under `profiles/_template/`.

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [GitHub Pages publish](docs/github-pages.md)
- [Netlify deploy](docs/netlify-deploy.md) (alternative host)
