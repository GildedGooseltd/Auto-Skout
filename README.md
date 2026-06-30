# Auto Skout

Profile-based local marketplace scanner — Craigslist, OfferUp, Facebook Marketplace, AutoTempest, auctions. Builds a mobile-friendly dashboard you can host on GitHub Pages.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium

# Truck search (CO + FL)
SKOUT_PROFILE=kate-vehicles .venv/bin/python src/main.py --all --open

# Trailer hunt (Gardner farm)
SKOUT_PROFILE=gardner-farm .venv/bin/python src/main.py --trailer --all --open
```

Copy `.env.example` → `.env` for optional API keys.

## Share results with friends

```bash
./scripts/publish-github.sh kate-vehicles
```

See **[docs/github-pages.md](docs/github-pages.md)** for repo + Pages setup.

## Profiles

| Profile | Use |
|---------|-----|
| `kate-vehicles` | Used trucks ≤$20k, CO + FL |
| `gardner-farm` | Farm freebies + trailer hunt |
| `kate-art` | Art opportunities |

Add your own under `profiles/_template/`.

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [GitHub Pages publish](docs/github-pages.md)
- [Netlify deploy](docs/netlify-deploy.md) (alternative host)
