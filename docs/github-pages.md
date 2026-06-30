# GitHub Pages — share Skout with friends

Skout builds a static dashboard (`site/`). You run scans on your Mac; friends open a public URL — no login, no repo access.

## One-time setup

1. **Create repo** — [github.com/GildedGooseltd/Auto-Skout](https://github.com/GildedGooseltd/Auto-Skout) (already created).

2. **Link remote** (from `free-stuff-alerts`):
   ```bash
   git remote add origin git@github.com:GildedGooseltd/Auto-Skout.git
   git push -u origin main
   ```

3. **Enable Pages** — repo **Settings → Pages**:
   - **Build and deployment** → Source: **Deploy from a branch**
   - Branch: **`gh-pages`** · folder **`/ (root)`** → **Save**

4. **Facebook session** (optional, for FB listings):
   ```bash
   .venv/bin/python src/scrapers/facebook.py --login
   .venv/bin/playwright install chromium
   ```

## Publish (each time you want fresh results)

```bash
# Truck search (default)
./scripts/publish-github.sh kate-vehicles

# Trailer hunt
SKOUT_PROFILE=gardner-farm ./scripts/publish-github.sh
```

Wait ~1 min, then share:

**https://gildedgooseltd.github.io/Auto-Skout/**

(Replace org/repo if you used a different name.)

## What gets public

Only the **`gh-pages` branch** — `index.html` + assets + embedded listings. Your code on `main` can stay private. Secrets (`.env`, `data/facebook_state.json`) are never committed.

## Optional: show URL in dashboard footer

After first publish, set `config/deploy.yaml`:

```yaml
hosting:
  provider: github
  public_url: "https://gildedgooseltd.github.io/Auto-Skout/"
```
